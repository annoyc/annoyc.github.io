---
title: 工具要等用户点确认，别从外面把循环掐断：Agent 的 suspend 与 resume
slug: suspend-and-resume-agent-loop
date: 2026-11-16 16:00:00
tags: [Agent, Agent Loop, Human-in-the-Loop, 数问工作台]
excerpt: 数问工作台里执行 SQL 和交互筛选都要等用户点确认。早期做法是在事件流外头窥探结果、强行 break，等用户点完再拼一段文本历史当新对话重跑，前几步查过的表结构全丢了。这篇讲怎么把挂起与恢复做成循环内部的一等状态，以及状态往返客户端时踩的三个坑。
---

数问工作台里有两个工具不能当场跑完：一个是 `execute_sql`，生成出 SQL 后得停下来等用户在界面上点“确认执行”；另一个是 `smart_filter`，遇到“最近”“按状态”这种模糊说法时，得先弹出筛选卡片等用户选完再继续。

写第一版的时候，我的 Agent 循环只是一个顺着往下跑的 `while (step < maxSteps)`，根本没有“暂停”的概念。为了把循环停住，当时的做法挺别扭：`execute_sql` 先返回一段带 `pending_confirmation` 的 JSON 字符串，顺手塞进请求外头的一个 `Map`；SSE 路由每收到下一个 `step` 事件，就去那个 `Map` 里偷看一眼，发现有待确认的 SQL，就往前端发一条确认消息，然后直接 `break` 跳出 `for await` 循环。

停是停住了，接不回去。用户点完“确认”、真正跑出查询结果之后，前端只能再发一次普通请求，把界面上看得到的几句文字问答和刚跑出来的表格拼成一条新的 `user` 消息，起一个全新的 Agent 实例。

这带来了两个明显的毛病。第一，模型在调 `execute_sql` 之前往往已经调过 `list_tables`、`get_table_schema` 摸清了表结构，但这些中间的工具调用不在前端可见的文字列表里。强行 `break` 再重开一轮，前几步查出来的表结构全丢了，要是 SQL 报错需要改写，模型还得从头再查一遍表。第二，在外层靠工具名字配对结果队列，一旦模型在同一轮里并发调了两次同名工具，顺序很容易乱。

前几天我把那套外层 `break` 拆了，在循环里加了正式的 `suspend` 和 `resume`。

## 先看重型框架怎么做，再定自己的边界

动手之前我去翻了 LangGraph 的 [Interrupts 文档](https://docs.langchain.com/oss/javascript/langgraph/interrupts)。它的思路很像前端熟悉的代数效应或者协程：工具或节点内部直接调 `interrupt(payload)`，框架底层抛一个特殊异常把图停住，用 `checkpointer` 把当前状态按 `thread_id` 存进内存或数据库；等拿到用户输入，再调 `graph.invoke(new Command({ resume }), config)`。恢复的时候，被中断的那个节点会从函数第一行重新往下跑，直到撞上 `interrupt()` 时直接拿到外部传入的值。

这套机制有个隐含前提：服务端得配一个持久化的 `checkpointer`，而且因为节点会重头重跑，`interrupt()` 之前的代码不能有不可重入的副作用，文档里还专门警告不能在同一个节点里写 `while` 循环调多次 `interrupt()`。

我的场景比通用图编排简单得多。在标准的 OpenAI / DeepSeek 工具调用协议里，天然的暂停边界本来就存在：模型发出一条带 `tool_calls` 的 `assistant` 消息，正在等对应的 `role: 'tool'` 消息填回来。工具本身不需要执行到一半挂起再重跑，只要告诉循环“这次调用还没有真结果，先停在这，等外部把 `tool_call_id` 对应的结果补上再调下一次模型”就够了。

而且我不打算为了等用户点个按钮就在服务端引一套 Redis 或数据库存会话快照。数问工作台的连接配置和会话本来就在浏览器端管，挂起的状态只要能安全地序列化成 JSON，完全可以先交还给前端拿着，点完确认再原样带回来。

## 最小实现：一个 Symbol 加三段状态流转

整个机制拆开只有三小块。

第一块在 [`src/core/tool/index.ts`](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/core/tool/index.ts#L49-L55)，给工具提供一个 `suspend` 辅助函数，返回带特定 `Symbol` 的对象：

```typescript
const SUSPEND = Symbol.for('agent-loop.tool.suspend')

export function suspend(payload: unknown, message = 'Not executed: waiting for user input.'): ToolSuspension {
  return { [SUSPEND]: true, payload, message }
}
```

`execute_sql` 通过安全检查后不再自己拼状态字符串，直接 `return suspend(resultPayload, '该 SQL 未执行：尚未获得用户确认。如仍需要，请重新提交。')`（见 [`src/server/tools.ts`](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/server/tools.ts#L269)）。

第二块在 [`src/core/generate/generate-loop.ts`](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/core/generate/generate-loop.ts#L214-L250)。一轮工具并发执行完，循环按调用顺序把每条结果收进 `currentMessages`，同时记下哪些调用返回了 `suspension`。只要 `pending` 数组不为空，发完 `suspend` 事件就直接 `return`，不再进入下一轮模型请求：

```typescript
const toolResults = await executeToolCalls(stepResult.toolCalls, currentTools, signal, runner, hooks, currentModel, truncatedCallId)
const pending: PendingToolCall[] = []
for (const { toolCall, outcome } of toolResults) {
  const name = toolCall.function?.name ?? ''
  currentMessages.push({ role: 'tool', content: outcome.content, tool_call_id: toolCall.id })
  yield {
    type: 'tool-result',
    step: stepRef.value,
    toolCallId: toolCall.id,
    name,
    content: outcome.content,
    ...(outcome.suspension ? { suspended: true, payload: outcome.suspension.payload } : {}),
  }
  if (outcome.suspension) {
    pending.push({ toolCallId: toolCall.id, name, arguments: toolCall.function?.arguments ?? '', payload: outcome.suspension.payload })
  }
}
// ...
if (pending.length > 0) {
  const suspended = { messages: conversationOf(currentMessages), pending }
  yield { type: 'suspend', step: stepRef.value, state: suspended }
  return { text: lastAssistantMsg(currentMessages), output: undefined, usage: totalUsage, suspended }
}
```

第三块是恢复。用户确认后，前端把刚才收到的 `state`、`toolCallId` 和真实的 SQL 执行结果传回给服务端，循环入口用 [`src/core/generate/suspend.ts`](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/core/generate/suspend.ts#L62-L76) 里的 `applyResume` 把真实结果填回对应的 `tool_call_id` 位置：

```typescript
export function applyResume(resume: ResumeParams): ChatMessage[] {
  const pendingIds = new Set(resume.state.pending.map(call => call.toolCallId))
  const results = new Map<string, string>()
  for (const { toolCallId, content } of resume.results) {
    if (!pendingIds.has(toolCallId)) {
      throw new Error(`Cannot resume: "${toolCallId}" is not a pending tool call`)
    }
    results.set(toolCallId, content)
  }
  return resume.state.messages.map((message) => {
    if (message.role === 'tool' && results.has(message.tool_call_id)) {
      return { ...message, content: results.get(message.tool_call_id)! }
    }
    return { ...message }
  })
}
```

这样一来，之前同一次运行里查过的表结构、做过的查询规划都在 `state.messages` 里原封不动地留着，模型拿到的就是一条正常的 `tool` 响应。

## 踩过的三个坑

主干代码加起来不到一百行，但写单测和联调的时候连着踩了三个细节问题。

### 1. 挂起的那一刻，就要先把占位 `tool` 消息写进去

看上面的 `applyResume` 会发现，它做的事情不是往消息数组末尾 `push` 一条新的 `tool` 消息，而是去 `map` 替换一条已经存在的 `tool` 消息。

我一开始写的是挂起时不写 `tool` 消息，等恢复时再 `push`。马上就想到一个反例：模型完全可能在同一个 step 里同时发出两个工具调用（比如一边调 `smart_filter` 一边调 `execute_sql`，或者连续提两条待确认调用），而前端界面一次只展示一张确认卡片，恢复时只带回其中一个 `toolCallId` 的结果。

Chat Completions 协议有一个死规矩：只要前一条 `assistant` 消息的 `tool_calls` 数组里有 N 个调用，后面就必须紧跟 N 条对应的 `role: 'tool'` 消息，少一条接口直接报 400。

所以在 [`src/core/tool/index.ts`](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/core/tool/index.ts#L103-L105) 检测到 `isSuspension(execResult)` 时，当场就会用 `suspend()` 传入的第二参数生成一条形如 `{"success":false,"error":"该 SQL 未执行：尚未获得用户确认..."}` 的占位内容，先塞进 `currentMessages`。恢复的时候，用户确认过的那条被真结果覆盖；没被覆盖到的另一条，留在原地的就是一句合法的“尚未执行”说明，既不会把协议搞崩，模型读到也明白发生了什么。

### 2. 状态从客户端回来，不能信，也不能带 `system`

把 `state` 交给浏览器保存，省掉了服务端存储，代价是 `state` 跨了信任边界——浏览器传回来的任何 JSON 都得当成不可信输入。

如果挂起时把整条 `currentMessages` 原样发给前端，里面第一条就是完整的 `system` prompt（包含数据库概览、安全规则）和 few-shot 示例。这有两个问题：一是白白把大段系统提示词在网络上来回传；二是客户端一旦篡改了 `state.messages[0]`，就能直接替换掉服务端的 `system` 指令。

所以在 [`src/core/generate/suspend.ts`](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/core/generate/suspend.ts#L11-L59) 里做了两道隔离。

挂起导出时，`conversationOf(currentMessages)` 会把排在最前面的 `system` 消息和 `name === 'few-shot'` 的示例全部剥掉，只留真正的对话轮次。恢复解析时，`parseSuspendedState` 里的 Zod schema 用 `discriminatedUnion` 严格限制 `role` 只能是 `'user' | 'assistant' | 'tool'`：

```typescript
// A stored state never carries configuration: system and few-shot messages are rejected.
const messageSchema = z.discriminatedUnion('role', [
  z.object({ role: z.literal('user'), content: z.string(), name: z.string().optional() }),
  z.object({
    role: z.literal('assistant'),
    content: z.string().nullable().optional(),
    reasoning_content: z.string().optional(),
    tool_calls: z.array(toolCallSchema).optional(),
  }),
  z.object({ role: z.literal('tool'), content: z.string(), tool_call_id: z.string().min(1) }),
])
```

谁往 `messages` 里塞一条 `role: 'system'`，校验直接抛错。等校验通过了，服务端再用当前最新的配置重新拼上 `system` 和 `fewShot`（见 [`generate-loop.ts`](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/core/generate/generate-loop.ts#L84)）。用户在等待确认的空当里哪怕改了模型设置或权限档位，恢复后生效的也是新配置。

除此之外，`parseSuspendedState` 还会交叉检查 `pending` 里的每一个 `toolCallId`，必须同时在某条 `assistant.tool_calls` 和某条 `tool.tool_call_id` 里出现过。万一前端传上来的状态坏了或者对不上号，[`src/server/chat-events.ts`](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/server/chat-events.ts#L15-L24) 里的 `parseResume` 会捕获异常返回 `undefined`，外层自动退回用可见历史重开一轮的兜底路径，不会直接把对话卡死。

### 3. 流式路径漏存了 `reasoning_content`，恢复后撞上 DeepSeek 的 400

以前每次确认都是新起一轮对话，历史里只有纯文本的 `user` 和 `assistant`，从来没把带 `tool_calls` 的 `assistant` 消息跨请求回传过。改成真正的恢复之后，前一步的 `assistant` 消息要原样带进下一步请求，马上暴露出流式路径的一个老 bug。

按 DeepSeek 官方的 [Thinking Mode 文档](https://api-docs.deepseek.com/guides/thinking_mode)，普通多轮对话不需要回传上一轮的思维链 `reasoning_content`；但只要请求里带了 `tools` 参数，此前步骤生成的 `reasoning_content` 就必须原封不动地传回给 API，让模型接着上一步的推理往下走，漏传会直接返回 400 错误。

我翻了代码才发现，非流式调用因为直接拿了响应里的 `message` 对象，天然留着 `reasoning_content`；而流式路径 [`src/core/generate/generate-stream.ts`](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/core/generate/generate-stream.ts#L85-L92) 一边收到 `delta.reasoning_content` 一边往前端推了 `reasoning-delta` 事件，最后拼 `assistantMessage` 时居然只存了 `content` 和 `tool_calls`，把攒下来的思维链直接丢了。

修这个坑得两头兼顾：一方面在 `generate-stream.ts` 和挂起状态的 `messageSchema` 里都把 `reasoning_content` 留住；另一方面，数问工作台还支持切到其他 OpenAI 兼容服务，那些服务并不认识输入消息里的 `reasoning_content` 字段，严一点的网关还会报错。所以在 [`src/core/model/invoke.ts`](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/core/model/invoke.ts#L19-L35) 发请求前加了一道 `prepareMessages`：

```typescript
export function prepareMessages(messages: ChatMessage[], replayReasoning: boolean): ChatMessage[] {
  if (replayReasoning) return messages
  return messages.map((message) => {
    if (message.role !== 'assistant' || !('reasoning_content' in message || 'reasoning' in message)) return message
    const { reasoning_content: _content, reasoning: _alias, ...rest } = message as AssistantMessage & { reasoning?: unknown }
    return rest
  })
}
```

内存和挂起快照里永远保留完整的 `reasoning_content`，真正发 HTTP 请求的那一刻，只有 `thinkingStyle === 'deepseek'` 且思考模式未关闭时才带出去，其他服务商一律剥掉。

## 小结

做人工确认的时候，如果只是从循环外面强行 `break`，表面上看流是停住了，实际上是把一次完整的工具调用硬生生劈成了两轮不相干的对话。把暂停点放在“工具已经返回占位消息、但还没发起下一次模型调用”的缝隙里，让工具自己返回 `suspend()`，状态就能干干净净地封进快照。剩下的工作，就是防住这个快照在客户端走一遭回来可能带来的越权和协议不一致。

参考：

- [Interrupts - Docs by LangChain（LangGraph JavaScript 文档）](https://docs.langchain.com/oss/javascript/langgraph/interrupts)
- [Thinking Mode - DeepSeek API Docs](https://api-docs.deepseek.com/guides/thinking_mode)
- [数问工作台 deepseek-db-chat](https://github.com/annoyc/deepseek-db-chat)
- [src/core/generate/suspend.ts](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/core/generate/suspend.ts)
- [src/core/generate/generate-loop.ts](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/core/generate/generate-loop.ts)
- [src/core/tool/index.ts](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/core/tool/index.ts)
- [src/core/model/invoke.ts](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/core/model/invoke.ts)
- [src/server/chat-events.ts](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/server/chat-events.ts)
