---
title: 流式请求的“首帧重试”与三段超时：SSE 断在半路，为什么不能从头再来
slug: stream-first-event-retry-and-timeouts
date: 2026-11-17 17:15:00
tags: [Agent, 流式请求, 错误恢复, 数问工作台]
excerpt: 写惯了前端的 JSON 接口重试，转做 Agent 时很容易掉进两个坑：要么不敢给 SSE 流加任何重试，刚建连吃个 503 就整轮报废；要么直接拿普通超时套上去，把思考慢的模型提前掐死。这篇结合数问工作台的真实改造，讲清楚“首帧门禁”怎么安全重试，以及为什么要把一条流的超时拆成三段来看。
---

做前端的时候，给 `fetch` 封一个带重试的请求工具几乎是起手式：接口返回 429 或者 503，等个一两秒重发一遍，页面上连个报错都不需要弹。

刚开始在数问工作台里搭模型调用层时，我也照着这个直觉写了 [`withRetry`](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/core/client/retry.ts#L4-L31)。普通 JSON 请求 `invoke()` 套上之后跑得挺稳，但写到流式输出的 `invokeStream()` 时，我犹豫了，最后干脆一行重试都没加。

原因很现实：流式响应是一边从 `ReadableStream` 里往外读 chunk，一边通过 SSE 往前端推的。用户屏幕上已经看到模型吐出半句话，甚至前端工具卡片已经展开一半了，这时候如果连接突然断掉，你还能从头重发一遍请求吗？重发一遍，模型会从第一个字重新生成，聊天气泡里直接多出半截重复的废话。

结果没跑几天就踩了反方向的坑。上游偶尔在刚建连那一刻吐一个 503 或者限流 429，一个字都还没吐出来，整轮对话就直接红字报错了。明明什么副作用都没发生，却连重连一次的机会都没有。

后来我才想明白，流式请求不是“不能重试”，而是**重试的窗口只开到“拿到第一个有效事件”那一刻为止**。

## 重试门禁：只包住“建连 + 第一帧”

如果把普通 `fetch` 的重试直接套在 `for await (const chunk of stream)` 外面，一旦流吐过半块数据再抛错，重跑就会污染下游状态。

反过来，如果只把 `fetch` 握手包进重试里，也不够。因为很多代理网关和模型服务端会先返回 `200 OK` 和响应头，然后才在读第一条 SSE 事件时吐错、超时或者断流。

真正的边界在 `chunks.next()`。在数问工作台最近的修复（[`src/core/model/invoke.ts`](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/core/model/invoke.ts#L123-L139)）里，做法很直接：把打开流和拉取第一个事件绑在一起放进 `withRetry`：

```typescript
const opened = await withRetry(async () => {
  const chunks = apiStreamRequest(
    url,
    config.apiKey!,
    body,
    timeout,
    params.signal,
    { idleTimeoutMs: config.idleTimeout, deadlineMs: config.streamDeadline },
  )
  return { chunks, first: await chunks.next() }
}, maxRetries, params.signal)

stream = opened.chunks

for (let next = opened.first; !next.done; next = await stream.next()) {
  const chunk = next.value
  // 累加 usage、output 并向调用方 yield chunk
  yield chunk
}
```

这里有一个很巧的对称性：

1. 在 `await chunks.next()` 返回之前，外层 `for await` 还没有消费到任何事件，前端的聊天气泡一个字也没收到。此时不管是 HTTP 返回 `429 / 500 / 502 / 503` 抛出 `ApiRequestError`，还是在拿到第一条有效 SSE 数据前建连失败，[`withRetry`](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/core/client/retry.ts#L17-L25) 都可以安全地按 `Retry-After` 响应头，或者 `1000 * 2 ** attempt + 随机抖动` 等退避时间重新发一次请求。
2. 一旦 `opened.first` 拿到手，代码走出 `withRetry`，进入 `for` 循环把第一帧和后续帧逐个 `yield` 出去，重试门禁就永久关上了。如果流在吐完第一帧之后因为 `connection reset` 中途断掉，[`tests/agent-http.test.ts`](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/tests/agent-http.test.ts#L118-L134) 里专门校验过：此时调用次数必须是 `1`，错误直接向上抛出，绝不会再重跑一次去污染已渲染的文本。

顺便对照一下，Vercel AI SDK 的 `streamText` 里的 `maxRetries` 也是同样逻辑：只保驾到流真正开始输出为止，中途断流不会重头再来。

## 一个 timeout 管不住 SSE：必须拆成三段时钟

解决了“什么时候能重试”，下一个立刻撞上的问题是：**流式请求的超时到底该设多少秒？**

写普通接口的时候，前端习惯给 `fetch` 塞一个 `AbortSignal.timeout(30000)`。这套做法搬到 Agent 的流式接口上，设短了必死，设长了也必死：

- 设成 30 秒：遇到开启了深度思考（reasoning）或者要输出长段 SQL 分析报告的模型，前 20 秒可能在思考，后面持续吐字吐了 40 秒，结果第 30 秒一到，页面上“咔”一下被自己前端或中间层掐断了。
- 设成 180 秒：正常任务是能跑完了，但如果上游网络抽风、TCP 连接半开在那里静默卡死，用户就得对着加载动画干等整整 3 分钟才能看见错误提示。

问题在于，“等待模型开口”和“模型正在源源不断吐字”根本不是一回事。参考类似 Vercel AI SDK 里 `firstChunkMs` / `chunkMs` / `totalMs` 的拆分思路，我在数问工作台的 [`src/core/client/stream-request.ts`](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/core/client/stream-request.ts#L5-L66) 和 [`src/core/model/types.ts`](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/core/model/types.ts#L62-L67) 里，把一条流的超时拆成了三根独立的时钟：

1. **首字节超时（`timeout`，默认 60 秒）**：从发起请求到第一次 `reader.read()` 返回数据之间生效。超过 60 秒连个响动都没有，直接报 `Timed out waiting for first response byte`。
2. **流内静默超时（`idleTimeoutMs`，默认 120 秒）**：只要第一次读到字节，计时器就切到静默模式。之后每收到一块新数据，就重置一次闹钟。只要模型还在往外吐字（或者服务端还在发包），哪怕整段回答跑了一分半钟，也不会被打断；一旦连续 120 秒没有任何数据到达，立刻触发 `Response stream idle timeout`。
3. **单次流总上限（`deadlineMs`，默认 180 秒）**：用 `createAbortScope(signal, limits.deadlineMs ?? 180000)` 挂在整条请求上，兜底防止极慢的“滴水式”响应无限占着服务端连接不放。

对应到 `apiStreamRequest` 的核心骨架，关键就在那两处 `armTimeout` 的切换：

```typescript
const scope = createAbortScope(signal, limits.deadlineMs ?? 180000)
let timer: ReturnType<typeof setTimeout> | undefined
const armTimeout = (ms: number, message: string) => {
  if (timer) clearTimeout(timer)
  if (ms > 0) timer = setTimeout(() => scope.abort(new DOMException(message, 'TimeoutError')), ms)
}

// 1. 建连到拿到第一块响应体之间，受首字节超时约束
armTimeout(timeout, 'Timed out waiting for first response byte')
try {
  const response = await abortable(fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream', Authorization: `Bearer ${apiKey}` },
    body: JSON.stringify({ ...options, stream: true }),
    signal: scope.signal,
    redirect: 'error',
  }), scope.signal)

  const reader = response.body!.getReader()
  while (true) {
    const { done, value } = await abortable(reader.read(), scope.signal)
    if (done) break
    // 2. 只要开始收到数据，就切到流内静默看门狗，并随着每次读到分片重置
    armTimeout(limits.idleTimeoutMs ?? 120000, 'Response stream idle timeout')
    // ... 解码 UTF-8 并按行解析 SSE data: 事件
  }
} finally {
  if (timer) clearTimeout(timer)
  scope.abort()
  // 取消并释放 reader 锁
}
```

注意一个小细节：`armTimeout(timeout, ...)` 是在 `fetch()` 之前启动，直到第一次 `reader.read()` 成功返回之后，才被第 24 行的 `idleTimeoutMs` 覆盖。这样就把“服务端拿到请求头先返回 200、然后卡在首包前”的假死情况也一并盖住了。

## 写这段代码时踩过的几个真实坑

这套机制跑起来之后，还是陆续暴露过好几个前端转过来容易忽视的边缘问题。

### 坑一：reader 没释放，以及把用户“点停止”当成了重试信号

最早我觉得只要把 `scope.signal` 传给 `fetch()` 就万事大吉了。实测才发现，当 `fetch` 已经拿到响应头、代码卡在 `reader.read()` 等下一帧时，如果外部触发超时或者用户取消，部分运行时的底层流并不会立刻唤醒挂起的 `reader.read()`，甚至在异常处理漏掉释放步骤时，还会把 `response.body.locked` 一直锁死。

所以在 [`src/core/client/lifecycle.ts`](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/core/client/lifecycle.ts#L21-L33) 里，我们不仅把 `reader.read()` 包进 `abortable(reader.read(), scope.signal)` 让它在信号触发时立刻 reject，还必须在 `finally` 里显式执行 `void reader.cancel().catch(() => {}); reader.releaseLock()`；外层的 `invokeStream` 也同样在 `finally` 里调 `await stream?.return(undefined)`，保证哪怕消费方提前 `break` 跳出循环，上游连接也能第一时间关掉。

另一个坑出在错误分类上。我之前写 `classifyError` 时，没有细拆 `TimeoutError` 和 `AbortError`。结果用户看模型跑偏了，在前端点了“停止生成”，触发了请求取消（`AbortError`），如果外层循环的错误钩子把它当成一般网络异常去重试，Agent 反而背着用户又悄悄重跑了一遍。后来我们在 [`src/core/errors.ts`](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/core/errors.ts#L43-L61) 里硬性区分：`TimeoutError` 的 `retryable` 是 `true`，而 `AbortError` 标成 `type: 'aborted'`、`retryable: false`。取消是用户的明确决策，不是瞬时故障。

### 坑二：SSE 拆行丢半包、跨块乱码，以及 `redirect: 'error'`

手写 SSE 解析很容易把 `chunk` 直接按 `\n` 切开就 `JSON.parse`。真实网络下，一个 TCP 分片完全可能把一行 `data: {"choices":...}` 从中间劈成两半，甚至把一个三字节的中文 UTF-8 字符劈成前两个字节和后一个字节。如果 `TextDecoder.decode(value)` 漏传 `{ stream: true }`，中文边界必出乱码；如果没留一个 `buffer = lines.pop() || ''` 等下一块拼齐，半截 JSON 直接炸解析错误。

另外一行看上去不起眼但很关键的配置是 `redirect: 'error'`。前端平时调同源接口很少关心重定向，但模型客户端的请求头里带着 `Authorization: Bearer <apiKey>`。一旦配置错了地址或者上游返回 307 跳到了其他域名，默认跟随重定向可能会把鉴权凭据顺手转走。强制 fail closed 之后，既不会自动跳转，也不会被 `withRetry` 误判成可重试错误。

最后提一个目前特意留白的地方：在数问工作台当前的实现里，HTTP 客户端层的 `withRetry` 只重连 `429 / 500 / 502 / 503` 这几类 `ApiRequestError`；如果是在首字节阶段撞上 `TimeoutError`，HTTP 层本身不会自己重发，而是统一向上抛给 `agentLoop` 的 `classifyError` 和 `onError` 钩子去决定怎么处理。把每一层的重试职责划清楚，比到处嵌套隐式重试好排查得多。

## 小结

从前端的普通 HTTP 请求切到 Agent 的流式调用，请求层的改动其实就绕不开三件事：

- 重试的门禁只能开到 `chunks.next()` 拿到第一个有效事件为止，之后掉线就老实报错，不能重头覆盖用户已经看到的内容；
- 超时不能只写一个总毫秒数，要拆成“首字节等待”“流内静默间隔”和“整条流总上限”三根时钟；
- `reader` 的取消与解锁、`AbortError`（用户取消）与 `TimeoutError`（超时）的区分，必须在边界上写清楚。

把这几条线理顺了，Agent 在弱网和上游限流下的表现才算基本稳定。

参考：

- [streamText API Reference（Vercel AI SDK）](https://ai-sdk.dev/docs/reference/ai-sdk-core/stream-text)
- [Streaming Messages（Anthropic API Docs）](https://platform.claude.com/docs/en/api/messages-streaming)
- [数问工作台 deepseek-db-chat](https://github.com/annoyc/deepseek-db-chat)
- [src/core/model/invoke.ts](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/core/model/invoke.ts#L99-L160)
- [src/core/client/stream-request.ts](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/core/client/stream-request.ts#L5-L66)
- [src/core/client/retry.ts](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/core/client/retry.ts#L4-L31)
- [src/core/client/lifecycle.ts](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/core/client/lifecycle.ts#L1-L42)
- [src/core/errors.ts](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/core/errors.ts#L37-L89)
- [tests/agent-http.test.ts](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/tests/agent-http.test.ts#L91-L135)
