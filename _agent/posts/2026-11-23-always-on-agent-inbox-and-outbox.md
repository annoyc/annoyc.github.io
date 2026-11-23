---
title: 常驻 Agent 不是写个死循环：事件收件箱、单活唤醒与 Outbox 节流
slug: always-on-agent-inbox-and-outbox
date: 2026-11-23 16:00:00
tags: [Agent, Always-on Agent, 架构设计]
excerpt: 聊天型 Agent 是用户问一句它答一句，想做成 7x24 后台主动干活的 Always-on Agent，最容易踩的坑就是让 Webhook 和定时任务直接调模型。结合 Anthropic 和 LangChain 最近公开的工程实践，拆解“事件收件箱 + 单活串行唤醒 + Outbox 发件箱”这套骨架，以及为什么“允许什么都不发”比会回复更重要。
---

前面写过很多篇关于 Agent 循环、记忆和工具边界的笔记，默认前提其实都一样：用户在对话框里敲一句话，Agent 醒过来跑一个 `while` 循环，流式吐完答案，这轮活就算结束了。

可真把 Agent 用到日常工作流里，很快会想往前再迈一步：能不能别总等我催？比如仓库来了新 PR 自己看一眼、每天早上八点把昨天的变更汇总发我、发出去的邮件如果对方三天没回就自己跟进一下。

这就是最近大家讨论很多的 Always-on Agent（或者叫 Proactive Agent，主动型 Agent）。Anthropic 在 11 月 13 日发了一篇工程指南《Building always-on agents》，LangChain 在 11 月 17 日也发了一篇复盘《What We've Learned Building Proactive Agents》。他们内部跑在 Managed Deep Agents 上的两个主力 Agent——做销售线索跟进的 GTM Agent 和做代码审阅的 Robin——现在超过 70% 的运行都来自定时触发，已经远远超过用户临时发消息的次数。

我刚开始琢磨怎么自己搭一个类似的后台 Agent 时，第一反应很朴素：配几个 Webhook 接口，再挂几个 cron 定时任务，收到事件就直接调 `runAgent(event)`，干完活调 Slack 或邮件接口把结果推给用户，不就齐活了？

把这两篇工程复盘读完，我才发现这么直连上线几乎必炸。

## “一直在线”是用户的体感，Agent 平时其实在睡觉

Anthropic 在文章开头就泼了一盆很清醒的冷水：所谓 always-on，说的是用户的产品体验，在后端架构上它绝大部分时间都在睡觉。

它真正的工作节奏是离散的：某个外部信号把它叫醒，它在会话里跑一次有明确边界的 `run`，读记忆、调工具、把新进展落盘，然后立刻回去继续睡。之所以今年这种模式突然变得划算，一方面是模型判断“什么时候该打扰人”的能力上来了，另一方面长程任务的单次成本掉得很快——Anthropic 在文里给了一个数据，在他们测的一个长程编程基准上，Opus 5.5 解决单个任务的成本比 Opus 5 降了 78%（常规负载下是降 40%）。

既然底层跑的还是熟悉的 Agent 循环，那把一个“被动聊天机器人”改造成“后台常驻 Agent”，到底多出来了哪几层壳？

Anthropic 把外围骨架拆成了五个原语：事件收件箱（Event Inbox）、定时器（Timers）、记忆（Memory）、权限（Permissions）和发件箱（Outbox）。记忆和人工审批挂起前几篇已经单独聊过，这一篇把重点放在最容易被忽视的“进、醒、出”闭环上：收件箱、定时器和发件箱是怎么咬合在一起的。

## 入口做成单行道：所有触发源一律先写进 Event Inbox

为什么不能让 Webhook、定时器、用户消息各自直接去调 Agent？

做前端的同学对竞态条件都很敏感。假如一个用户三秒内连发三条消息，或者 GitHub 连续推过来两个 Webhook 事件，各自直接启动一次 `runAgent()`，马上就会撞出三件事：两个并发 run 同时读写同一份记忆和会话状态，互相覆盖；模型对着同一件事重复干两遍、连发两条废话；要是进程跑一半重启，内存里正在处理的事件直接就丢了。

解法很像服务端经典的持久化消息队列，Anthropic 叫它 Event Inbox，拆开看就三样东西：一张持久化的事件表、一个负责落库和快速响应的接入 Handler、一个后台消费的 Worker。

这里有四条非常具体的工程约束：

1. **接入层只落库、不干重活**。Webhook 提供商对响应时间通常很苛刻，超时就会判定投递失败然后反复重试。Handler 拿到请求，做完最基础的鉴权和来源 ID 去重，写进 Inbox 表就立刻返回 200，再发一个信号唤醒 Worker。像 prompt injection 检测这种重操作，留给 Worker 跑起来之后再做。
2. **定时器也别直接调 Agent，只负责往 Inbox 投事件**。Anthropic 把 Timer 拆成两类：一类是 Agent 自己设的一次性跟进闹钟（follow-up，比如“明天上午检查这封邮件有没有回复”），另一类是固定周期的定时任务（schedule，比如每天发日报）。它们单独存一张表，由调度器按固定间隔扫描到期项：一次性的标成 `done`，周期性的把下次到期时间往后推，然后统一往 Inbox 里插一条事件唤醒 Worker。这样整个系统只有 Inbox 这一条唤醒入口。
3. **同一个收件箱，同一时刻只允许一个活跃 Worker**。通常按用户维度建收件箱。串行执行彻底消除了并发踩踏。如果当前 run 正在跑，半路又进来一条新事件，可以先留在 Inbox 里等下一轮，也可以按业务需要合并进当前轮，或者中断重跑。
4. **Worker 醒来先打包合批，Inbox 是唯一真相源**。Worker 被叫醒后，不会只盯刚来的那一条，它会把 Inbox 里所有 `pending` 状态的事件一次性捞出来，合并成一次 `run` 交给模型（用户连发三条消息，Agent 合并看完只回一条）。等 run 真正跑完，再把这批事件标成 `done`。万一 Worker 跑一半崩了，事件还在表里躺着，新 Worker 起来还能捞出来重试。

## 出口别写死“必须回复”：留出静默通道，再加一层 Outbox

把入口理顺之后，出口还有一个比入口更反直觉的转变。

以前写聊天机器人，用户发一次请求，后端就流式吐一次回答，逻辑是一一对应的。放到 Always-on Agent 上，一次 run 结束其实有三种合法结局：往外发一条消息、决定什么都不发（保持静默）、或者遇到高风险操作走 deferred tool call 挂起等人工确认。

其中“决定什么都不发”特别关键。如果每次醒来都非得给用户发一句汇报，后台 Agent 很快就会变成高频骚扰器。

就算模型判断确实有事要通知用户，也不建议在所有场景里都让工具直接调 API 发出去。Anthropic 的做法是在模型和外部渠道之间再夹一层 Outbox（发件箱表 + 后台 Dispatcher）：模型只管往 Outbox 表里写待发消息，由 Dispatcher 统一管发送重试、合并节流和免打扰时段。

## 最小可运行原型：用 TypeScript 串起“收件箱 → 单活 Worker → 发件箱”

下面是一段不依赖任何外部框架的 TypeScript 代码，把上面说的单活锁、来源去重、定时转事件、合批消费和 Outbox 节流压缩成了一个最小骨架：

```typescript
type EventStatus = 'pending' | 'done';

interface InboxEvent {
  id: string;
  sourceEventId: string; // 上游唯一 ID，用于 Webhook 重试去重
  userId: string;
  kind: 'user_message' | 'webhook' | 'timer';
  payload: string;
  status: EventStatus;
}

interface TimerJob {
  id: string;
  userId: string;
  type: 'follow_up' | 'schedule';
  dueAt: number;
  intervalMs?: number;
  payload: string;
  done: boolean;
}

interface OutboxMessage {
  id: string;
  userId: string;
  body: string;
  urgent: boolean;
  sent: boolean;
}

// 实际生产应换成数据库表；这里用数组演示状态流转
const inbox: InboxEvent[] = [];
const timers: TimerJob[] = [];
const outbox: OutboxMessage[] = [];
const activeWorkers = new Set<string>();

// 1. 入口 Handler：去重 + 落库 + 立即返回，绝不在 HTTP 链路里等模型跑完
export function enqueueEvent(input: Omit<InboxEvent, 'id' | 'status'>): boolean {
  if (inbox.some((e) => e.userId === input.userId && e.sourceEventId === input.sourceEventId)) {
    return false; // 幂等吞掉上游重试
  }
  inbox.push({ ...input, id: crypto.randomUUID(), status: 'pending' });
  void wakeWorker(input.userId);
  return true;
}

// 2. 定时调度器：只把到期任务转投进 Inbox，不直接调用 Agent
export function tickTimers(now = Date.now()): void {
  for (const job of timers) {
    if (job.done || job.dueAt > now) continue;
    const firedAt = job.dueAt;
    if (job.type === 'follow_up') {
      job.done = true;
    } else if (job.intervalMs) {
      job.dueAt = firedAt + job.intervalMs;
    }
    enqueueEvent({
      userId: job.userId,
      sourceEventId: `timer:${job.id}:${firedAt}`,
      kind: 'timer',
      payload: job.payload,
    });
  }
}

type AgentDecision =
  | { action: 'silent' }
  | { action: 'notify'; body: string; urgent?: boolean };

// 3. 单活 Worker：同一用户串行消费，把积压事件合批进一次 run
export async function wakeWorker(
  userId: string,
  runAgent: (events: InboxEvent[]) => Promise<AgentDecision> = defaultRunAgent,
): Promise<void> {
  if (activeWorkers.has(userId)) return; // 已有活跃 Worker，新事件留在表里等下一轮
  activeWorkers.add(userId);

  try {
    while (true) {
      const batch = inbox.filter((e) => e.userId === userId && e.status === 'pending');
      if (batch.length === 0) break;

      const decision = await runAgent(batch);
      if (decision.action === 'notify') {
        outbox.push({
          id: crypto.randomUUID(),
          userId,
          body: decision.body,
          urgent: Boolean(decision.urgent),
          sent: false,
        });
      }

      // 跑完再统一标 done；如果中途抛错，事件保持 pending，后续可重试
      for (const ev of batch) ev.status = 'done';
    }
  } finally {
    activeWorkers.delete(userId);
  }
}

// 4. Outbox 分发器：控制发送节奏，比如夜间静默期只放行紧急消息
export async function dispatchOutbox(
  isQuietHours: boolean,
  send: (msg: OutboxMessage) => Promise<void>,
): Promise<void> {
  for (const msg of outbox) {
    if (msg.sent) continue;
    if (isQuietHours && !msg.urgent) continue;
    await send(msg);
    msg.sent = true;
  }
}

async function defaultRunAgent(events: InboxEvent[]): Promise<AgentDecision> {
  const hasImportant = events.some((e) => e.payload.includes('urgent'));
  if (!hasImportant) return { action: 'silent' };
  return {
    action: 'notify',
    body: `汇总处理了 ${events.length} 条事件，发现需要关注的告警。`,
    urgent: true,
  };
}
```

写前端的同学看 `wakeWorker` 里的那层 `activeWorkers` 锁加 `while (true)` 应该很眼熟：它和 UI 里防止重复触发异步任务、把期间攒下的更新合并到下一次执行的模式几乎一模一样。唯一的关键差别在于，生产环境里这三张表必须落在持久存储（比如 Postgres）里，靠数据库行锁或分布式锁保住“单活”，别放在 Node 进程内存里。

## 落地时最容易踩的两个坑

这套收发队列搭起来不算复杂，但结合两家团队公开的实战教训，真正线上跑起来有两个坑最容易把体验搞砸。

### 坑一：把“事件到达”当成“必须插话”，把用户烦到直接静音

习惯了做一问一答的聊天产品，很容易把后台 Agent 也做成“有动静就汇报”。

LangChain 在复盘里说得很直白：当 Agent 开始接管跨天、跨周的目标时，真正的稀缺瓶颈很快就变成了用户的注意力——人脑一样有有限的上下文窗口。他们给内部的 GTM Agent 做了专门的审阅列表，直接把会话分成“等你处理”“需要草拟回复”“无需回复”三档，过滤掉低价值邮件；给工程 Agent Robin 接进 Slack 频道时，他们没让它听见动静就回复，专门加了一个决策模型，判断眼下同事讨论的对话里它到底有没有价值插嘴。

哪怕真的决定发消息，展示方式也得为“快速扫一眼”让路。Robin 发通知的习惯是先抛一两句标题说清楚结论，把细节全塞进 thread 回复里；开 PR 请求 review 时，消息里只放一行摘要、改动规模和一个认领按钮。Anthropic 给的实践建议也完全一致：宁可一开始把主动型 Agent 调得安静一点，靠评测校准触发频率，等建立起信任再慢慢提高它的发言权限。

### 坑二：在重试与并发之间漏掉幂等，或者在回调里直接等模型

前端写久了 HTTP 接口，习惯在请求处理器里把整个业务 `await` 完再返回响应。把这个习惯带到 Always-on Agent 的 Webhook 入口上，几乎一定会撞墙。

一次 Agent 的 run 动辄十几秒甚至一两分钟，上游 Webhook（比如邮件网关或 Git 平台）等个几秒没拿到 200 就断开重发。如果接入层没把“落库 ACK”和“后台消费”拆开，也没用上游唯一 ID 在 Inbox 里做幂等去重，一次事件很快就会裂变成三四次并发执行。

另一个连带问题出在“标记完成”的时机上。如果 Worker 刚捞出事件就顺手把状态改成 `done`，结果模型调用半路超时或者容器被回收，这条事件就彻底人间蒸发了；反过来，一定要把标 `done` 放在 run 正常落盘之后，并且配合 `sourceEventId` 和发件箱幂等键，才能在进程意外退出后安全恢复。

## 小结

从“用户发一句、模型回一句”走到“后台持续跑的 Always-on Agent”，核心变化不在模型循环里面，而在循环外面的收发边界：

- 入口统一收口到持久化的 Event Inbox，Webhook 快速落库去重返回，Timer 到期后也只往 Inbox 写事件；
- 同一个收件箱保持单活 Worker 串行消费，顺手把短时间内攒下的待处理事件合批进一次 run；
- 出口显式允许“静默结束”，需要主动触达用户的消息走 Outbox 管节流与重试，把用户的注意力当成和 token 一样贵的资源来省着用。

参考：

- [Building always-on agents（Anthropic / Claude Resources, 2026-11-13）](https://claude.com/resources/articles/building-always-on-agents)
- [What We've Learned Building Proactive Agents（LangChain Blog, 2026-11-17）](https://www.langchain.com/blog/what-weve-learned-building-proactive-agents)
