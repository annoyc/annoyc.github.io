---
title: 子 Agent 别把主循环卡死：异步任务、半路改需求与三态回传
slug: async-subagents-non-blocking-loop
date: 2026-11-24 16:00:00
tags: [Agent, Subagents, 架构设计]
excerpt: 传统 Agent 调一个慢工具或同步子 Agent，整个主循环就会卡在 await 上，用户既插不上话也改不了需求。拆解 LangChain Deep Agents、Claude Code 和 OpenAI 最近落地的异步子 Agent 与异步工具机制：主从分离、五件控制工具、interrupt 与 enqueue 纠偏，以及主循环三态下的结果回传。
---

第一篇笔记写过，最基础的 Agent 拆开看就是一个 `while` 循环：模型发工具调用，代码执行工具、等结果返回，再把结果塞回上下文跑下一轮。

这个模型对短工具很好使，但一旦接上长任务——比如让子 Agent 去翻几十个源码文件、做一轮深度调研、或者跑一套集成测试——体验马上就垮了。前端同学一眼就能看出问题在哪：它把慢任务直接塞进了主线程。主循环死死卡在那个 `await` 上，用户盯着转圈的光标，既不能顺手问个别的简单问题，也不能在半路插一句“算了，别查 Postgres 了，改查 SQLite”，想停下来只能把整个主会话一起掐掉。

最近各家 Agent 基础设施几乎不约而同在解决这件事。LangChain 在 11 月 5 日发了一篇《Agents Are Going Async》，把异步子 Agent（Async Subagents）搬进了 Deep Agents 的 Python 和 JavaScript SDK；Claude Code 在交互会话里已经默认把大部分子 Agent 放到后台跑，结果通过后续轮次的完成通知回传；OpenAI 也在 Responses API 上提供了 `async: true` 的异步工具调用（从 GPT-6 Astra 开始支持），模型发完工具调用不用傻等，可以先回答独立的部分，等应用层把后台任务跑完再按原来的 `call_id` 把结果补回后续请求里。

把这几份官方文档对照着读了一遍，我才搞清楚：把子 Agent 从同步改成异步，关键不在“多开几个并发”，而在把“陪用户聊天的协调者”和“埋头干重活的后台 Worker”拆成两套生命周期。

## 同步子 Agent 和异步子 Agent，差在哪几步

以前大家用子 Agent，主要是为了上下文隔离：把调研、读大文件这种脏活甩给子循环，主循环只收一段压缩后的结论。但同步子 Agent 依然运行在主循环的同一次调用里，哪怕一次能并发派出三个子 Agent，主循环还是得等最慢的那个跑完才能动。

LangChain 在文章里打了个前端特别好懂的比方：同步子 Agent 像逐个 `await` 的慢调用，异步子 Agent 则像建了一个 `Promise`（或者 Python 的 `asyncio.create_task`）但先不 `await`——工具调用立刻返回一个 `taskId`，后台子 Agent 在自己的独立线程上接着跑，主 Agent（Orchestrator）马上把控制权交回给用户。

拆成异步之后，子 Agent 就变成了一个有状态的后台任务，主 Agent 不能只靠一个“调用”工具了。LangChain 的 `AsyncSubAgentMiddleware` 给主 Agent 配了五件控制工具：

- `start_async_task`：在独立线程上启动后台子 Agent，立刻返回 `taskId`（底层就是子线程 ID）。
- `check_async_task`：查询任务当前状态，拿到结果、报错或待确认请求。
- `update_async_task`：往正在跑的同一个子线程发补充指令，`taskId` 保持不变。
- `cancel_async_task`：中途取消后台任务，把状态标成 `cancelled`。
- `list_async_tasks`：列出所有跟踪中的任务和实时状态（不拉全量结果，省上下文）。

这里面我觉得设计得最巧的是 `update_async_task` 的两种更新策略 `update_strategy`：

一种是 `"interrupt"`（默认策略），直接中止子 Agent 当前这轮执行，带着完整历史和新指令立刻重跑。适合半路纠偏，比如用户突然说“别看 Postgres 了，换 SQLite”。另一种是 `"enqueue"`，不打断正在跑的活，把新指令排到队列后面，等当前这一轮跑完再接着执行。适合追加步骤，比如“编译完顺手把单测也跑了”。

## 后台跑完了，结果怎么回传给主 Agent

刚看上面五个工具时，我冒出一个很自然的疑问：后台任务跑完了，难道全靠主 Agent 隔几秒自己去调 `check_async_task` 查吗？

肯定不行。真靠模型自己轮询，不仅浪费 token，而且主 Agent 闲着没说话的时候根本没人触发它去查。LangChain 在 Agent Server 里的做法是把所有未完成任务的清单动态挂进主 Agent 的 system prompt，让它随时知道手里有几个在途任务，同时在后台任务结束或失败时主动推通知。

但推通知有个棘手的竞态问题：后台子 Agent 跑完的那一瞬间，主 Agent 可能正处在三种完全不同的状态里。LangChain 的文档把这三种情况拆得很干净：

1. 主 Agent 正在忙（`busy`）：不另起炉灶，而是在主 Agent 下一次调模型之前，把完成通知塞进它的上下文里，让它这轮顺手接住结果。
2. 主 Agent 闲着（`idle`）：由后台完成事件直接唤醒主 Agent，拉起一轮新 run 去消化结果并汇报给用户。
3. 主 Agent 正挂起等用户审批（`paused`）：不去打断当前审批流程，先把通知暂存起来，等用户点完确认之后再把结果交进去。

还有一种情况是反过来：后台子 Agent 自己撞到了需要人工确认的高风险操作。在 LangChain 的架构里，这个子 Agent 会进入 `waiting` 状态，但它不会强行唤醒或打断主 Agent，用户可以直接在子 Agent 对应的线程上回复审批；而 Claude Code 在本地终端里的交互处理稍有不同，后台子 Agent 触发权限提示时会直接浮到主会话里，标明是哪个子 Agent 在申请，用户按 `Esc` 只拒绝这一次工具调用，不会把整个子 Agent 杀掉。

## 最小可运行原型：用 TypeScript 写一个异步子 Agent 调度器

下面用不到 130 行纯 TypeScript（不依赖任何框架）把这套核心机制串起来：立即返回 `taskId`、`interrupt` 与 `enqueue` 两种半路改需求策略，以及主 Agent 在 `busy / idle / paused` 三种状态下的通知回传。

```typescript
type TaskStatus = 'running' | 'success' | 'error' | 'cancelled';
type UpdateStrategy = 'interrupt' | 'enqueue';
type OrchestratorState = 'idle' | 'busy' | 'paused';

interface AsyncTask {
  id: string;
  agentName: string;
  strategy: UpdateStrategy;
  status: TaskStatus;
  history: string[];
  queuedFollowUps: string[];
  result?: string;
  error?: string;
  controller?: AbortController;
}

interface TaskNotification {
  taskId: string;
  agentName: string;
  status: TaskStatus;
  summary: string;
}

type WorkerRunner = (
  history: string[],
  signal: AbortSignal,
) => Promise<string>;

export class AsyncSubagentRuntime {
  private tasks = new Map<string, AsyncTask>();
  private pendingNotifications: TaskNotification[] = [];
  private orchestratorState: OrchestratorState = 'idle';
  private workerRunner: WorkerRunner;
  private wakeOrchestrator: (notifications: TaskNotification[]) => Promise<void>;

  constructor(
    workerRunner: WorkerRunner,
    wakeOrchestrator: (notifications: TaskNotification[]) => Promise<void>,
  ) {
    this.workerRunner = workerRunner;
    this.wakeOrchestrator = wakeOrchestrator;
  }

  setOrchestratorState(state: OrchestratorState): void {
    this.orchestratorState = state;
    if (state === 'idle' && this.pendingNotifications.length > 0) {
      void this.flushToIdleOrchestrator();
    }
  }

  // 主 Agent 处于 busy 时，在每次模型调用前把积压通知抽走
  drainNotificationsBeforeModelCall(): TaskNotification[] {
    return this.pendingNotifications.splice(0);
  }

  // 1. 启动后台任务：立刻返回 taskId，绝不阻塞主循环
  startAsyncTask(agentName: string, prompt: string, strategy: UpdateStrategy = 'interrupt'): string {
    const task: AsyncTask = {
      id: crypto.randomUUID(),
      agentName,
      strategy,
      status: 'running',
      history: [prompt],
      queuedFollowUps: [],
    };
    this.tasks.set(task.id, task);
    void this.runTaskLoop(task);
    return task.id;
  }

  // 2. 半路改需求：interrupt 立即中止重跑，enqueue 排到当前轮后面
  updateAsyncTask(taskId: string, instruction: string): string {
    const task = this.tasks.get(taskId);
    if (!task) throw new Error(`Unknown taskId: ${taskId}`);
    if (task.status === 'cancelled') throw new Error(`Task ${taskId} already cancelled`);

    if (task.status !== 'running') {
      task.history.push(instruction);
      task.status = 'running';
      void this.runTaskLoop(task);
      return 'restarted';
    }

    if (task.strategy === 'interrupt') {
      task.history.push(instruction);
      task.controller?.abort('interrupted_by_update');
      return 'interrupted';
    }

    task.queuedFollowUps.push(instruction);
    return 'enqueued';
  }

  // 3. 取消任务
  cancelAsyncTask(taskId: string): boolean {
    const task = this.tasks.get(taskId);
    if (!task || task.status !== 'running') return false;
    task.status = 'cancelled';
    task.queuedFollowUps = [];
    task.controller?.abort('cancelled_by_user');
    return true;
  }

  // 4. 查询与列表：只把必要字段暴露给主 Agent
  listAsyncTasks() {
    return [...this.tasks.values()].map(({ id, agentName, status }) => ({ id, agentName, status }));
  }

  private async runTaskLoop(task: AsyncTask): Promise<void> {
    while (task.status === 'running') {
      const controller = new AbortController();
      task.controller = controller;
      try {
        const output = await this.workerRunner([...task.history], controller.signal);
        if (task.status !== 'running') return;
        if (task.queuedFollowUps.length > 0) {
          const next = task.queuedFollowUps.shift()!;
          task.history.push(`上次输出：${output}`, `追加指令：${next}`);
          continue;
        }
        task.status = 'success';
        task.result = output;
        this.emitNotification({ taskId: task.id, agentName: task.agentName, status: 'success', summary: output });
      } catch (err) {
        if (controller.signal.aborted && controller.signal.reason === 'interrupted_by_update') {
          continue; // interrupt 策略：带着已追加的新指令重开一轮
        }
        if (task.status === 'cancelled') {
          this.emitNotification({ taskId: task.id, agentName: task.agentName, status: 'cancelled', summary: '任务已取消' });
          return;
        }
        task.status = 'error';
        task.error = err instanceof Error ? err.message : String(err);
        this.emitNotification({ taskId: task.id, agentName: task.agentName, status: 'error', summary: task.error });
      }
    }
  }

  private emitNotification(note: TaskNotification): void {
    this.pendingNotifications.push(note);
    // busy 和 paused 都不打断主循环；只有 idle 才主动唤醒
    if (this.orchestratorState === 'idle') {
      void this.flushToIdleOrchestrator();
    }
  }

  private async flushToIdleOrchestrator(): Promise<void> {
    if (this.orchestratorState !== 'idle') return;
    this.orchestratorState = 'busy';
    try {
      while (this.orchestratorState === 'busy' && this.pendingNotifications.length > 0) {
        const batch = this.pendingNotifications.splice(0);
        await this.wakeOrchestrator(batch);
      }
    } finally {
      if (this.orchestratorState === 'busy') {
        this.orchestratorState = 'idle';
      }
    }
  }
}
```

前端同学看 `AbortController` 区分 `interrupted_by_update` 和 `cancelled_by_user` 那一段应该特别亲切，平时处理搜索框防抖取消和组件卸载取消也是同一套手法：前者要立刻接着新参数重发，后者直接收尾退出。

## 落地时最容易踩的两个坑

把状态机写通只是第一步，翻完 LangChain JS 文档的 Troubleshooting 和 OpenAI 的异步工具指南，真正线上跑起来有两个坑最容易把“异步”搞废。

### 坑一：模型刚派完活就疯狂轮询，把异步又写回了同步

大语言模型在训练时见了太多同步 ReAct 轨迹，形成了一种很强的条件反射：刚调完 `start_async_task` 拿到一个 `taskId`，下一毫秒它自己就开始循环调 `check_async_task`，一直查到任务完成为止。结果你在底层辛辛苦苦拆了独立线程和事件回调，模型一顿原地轮询，又把主循环生生卡死了。

LangChain 在 Troubleshooting 里专门列了这一条，解法是两头夹击：一方面靠推送通知替代模型主动查，另一方面不光在 `start_async_task` 的工具描述里交代，还得在主 Agent 的 system prompt 里写死一句硬规矩——派发完异步子 Agent 之后必须立刻把控制权交回给用户，严禁紧接着调 `check_async_task`。

顺带还有两个和模型习惯有关的小毛病也在同一份文档里踩过：一个是模型懒得再查状态，直接把几轮对话前的旧状态复述给用户（所以要在提示词里提醒它历史消息里的状态是过期的）；另一个是某些模型会手欠把 UUID 形式的 `taskId` 截断或改写，导致后续 `check_async_task` 和 `cancel_async_task` 全都找不到任务，必须在提示词里明确要求原样保留完整 `taskId`。

### 坑二：忽略工作池槽位和审批边界，并行直接退化成排队或串权

本地调试或小规模部署时，很容易按“我要同时跑 3 个子 Agent，所以配 3 个 worker 槽位”来算账。LangChain 的文档特意提醒了一个容易算漏的数：主 Agent 自己也占一个活跃槽位，所以 1 个主 Agent 加 3 个并发子 Agent 至少需要 4 个槽位（比如本地跑 `langgraph dev` 时得显式调大 `--n-jobs-per-worker`）。槽位配小了不会直接报错，但后台子 Agent 启动后会悄悄排队挂起，看起来就像卡死了一样。

另一条边界在权限和等待用户输入上。在 OpenAI 的异步工具调用里，`async: true` 不会一直挂着连接等用户回复，如果用户把异步提问弹窗直接关掉或超时，应用层必须显式回传一条“未作答”的工具结果，否则模型不知道该继续还是放弃。而在多子 Agent 的权限审批上，也要提前想好作用域：Claude Code 文档里专门提醒过，如果用户在主会话里给后台子 Agent 的某次权限申请点了“本会话持续放行”，这条授权会同步生效到整个主会话，而不是只给那一个子 Agent 用。

## 小结

把子 Agent 从同步改成异步，本质上是在 Agent 运行时里补上一层非阻塞任务调度：

- 主 Agent 只负责和用户对话、管任务状态，长任务通过 `start_async_task` 立刻拿到 `taskId` 后丢给后台独立线程；
- 半路改需求用 `update_async_task` 显式区分 `interrupt`（推翻重跑）和 `enqueue`（排队续做），而不是暴力杀进程；
- 完成通知按主 Agent 的 `busy / idle / paused` 三种状态分流，并在提示词里按住模型“派完活立刻轮询”的冲动。

参考：

- [Agents Are Going Async（LangChain Blog, 2026-11-05）](https://www.langchain.com/blog/agents-are-going-async)
- [Async subagents（LangChain Deep Agents JavaScript Docs）](https://docs.langchain.com/oss/javascript/deepagents/async-subagents)
- [Create custom subagents — Run subagents in foreground or background（Claude Code Docs）](https://code.claude.com/docs/en/sub-agents)
- [Async tool calling（OpenAI API Docs）](https://developers.openai.com/api/docs/guides/async-tool-calling)
