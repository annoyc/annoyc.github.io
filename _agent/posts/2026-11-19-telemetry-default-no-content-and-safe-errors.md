---
title: Agent 接入可观测性，别一上来就把 Prompt 和报错原文全传上去
slug: telemetry-default-no-content-and-safe-errors
date: 2026-11-19 17:00:00
tags: [Agent, 可观测性, 隐私安全, 数问工作台]
excerpt: 做前端习惯了接 Sentry 和埋点：把入参、返回值和 error.message 一股脑上报，排查问题最省事。搬到查数据库的 Agent 上，这个习惯会直接把监控后台变成明文数据泄漏口。这篇结合数问工作台的 Langfuse + OpenTelemetry 接入，讲清楚怎么做到默认只报元数据、导出层兜底脱敏，以及为什么异常不能在 SDK 回调里直接 throw。
---

做前端的时候，接入 Sentry 或者日志平台几乎是条件反射：把请求参数、响应体、`error.message`、堆栈和用户标识全打进去，线上出了 bug，打开后台一眼就能还原现场。

刚开始给数问工作台接 Langfuse（底层走 OpenTelemetry）时，我差点也照着这个习惯写。Agent 一轮对话要调好几次模型和工具，直觉上总觉得：不把完整的 `messages`、生成的 SQL 和模型输出全传上去，后台看个寂寞？

动手之前我停下来翻了一眼一次普通查数请求里装着什么：用户的自然语言提问、表结构、拼出来的业务 SQL、数据库查回来的明细行（里面随时可能有手机号、身份证、邮箱），以及出错时驱动抛出来的连接信息。如果把这些直接塞进 Span 往外发，等于自己动手搭了一条把生产数据搬去监控后台的明文管道。

后来去翻 OpenTelemetry 的 GenAI 语义规范（`semantic-conventions-genai`），发现规范里早就把这条线划得很清：**默认只采运行遥测，Prompt 和 Completion 正文必须是显式 opt-in（默认关闭）**。

看明白这一点后，我在数问工作台里把可观测性拆成了三层防线：默认只报“骨架”、导出层统一兜底、异常信息剥离原文。

## 两道开关：默认只报元数据，正文单独 opt-in

配了 `LANGFUSE_PUBLIC_KEY` 和 `LANGFUSE_SECRET_KEY`，只代表“我要看系统跑得稳不稳、花了多少 token”，不代表“我同意把业务对话内容发出去”。

在数问工作台的 [`src/core/telemetry.ts`](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/core/telemetry.ts#L3-L22) 里，我把内容采集拆成了一个独立的环境变量 `LANGFUSE_CAPTURE_CONTENT`，默认关闭，同时给 `metadata` 上了严格白名单：

```typescript
/** Content capture is a separate opt-in from enabling operational telemetry. */
export function captureTraceContent(): boolean {
  return process.env.LANGFUSE_CAPTURE_CONTENT === 'true'
}

export function traceContent(value: unknown): unknown {
  if (!captureTraceContent() || value === undefined) return undefined
  try { return JSON.parse(maskPII(JSON.stringify(value))) } catch { return '[unavailable]' }
}

const METADATA_KEYS = new Set([
  'provider', 'model', 'stream', 'messageCount', 'toolCount', 'columnCount',
  'hasToolCalls', 'isContinuation', 'durationMs', 'status', 'errorType',
])

export function traceMetadata(metadata: Record<string, unknown> = {}): Record<string, unknown> {
  return Object.fromEntries(Object.entries(metadata).filter(([key, value]) =>
    METADATA_KEYS.has(key) && ['string', 'number', 'boolean'].includes(typeof value),
  ).map(([key, value]) => [key, typeof value === 'string' ? maskPII(value).slice(0, 120) : value]))
}
```

这里有两个刻意为之的设计：

1. **`input` 和 `output` 统一走 `traceContent()`**。在 [`src/core/model/invoke.ts`](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/core/model/invoke.ts#L62-L85) 里，每次模型调用创建 `generation` 时，都会把 `body.messages` 和返回内容包一层 `traceContent()`。默认模式下它们直接变成 `undefined`，不上传任何正文；后台依然能完整看到模型名、`prompt_tokens / completion_tokens`、消息条数、工具数量、耗时和成功状态。真要在线下或合规允许的环境里排查提示词，显式打开 `LANGFUSE_CAPTURE_CONTENT=true` 之后，内容也会先过一遍 [`maskPII()`](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/lib/masking.ts#L52-L81)，把身份证、银行卡、手机号、邮箱全部打码再发。
2. **`metadata` 用白名单，不用黑名单**。前端写日志过滤很容易写黑名单，列几个 `password`、`token` 删掉完事。但在 Agent 项目里，业务字段随时会变，今天有人往 metadata 里塞一个 `database`，明天有人顺手塞一条 `sql` 或者 `ip`，黑名单永远堵不完。白名单只认 `METADATA_KEYS` 里这 11 个标量键，嵌套对象和未登记的键直接丢弃，字符串还要脱敏并截断到 120 字符。

## 导出层纵深防御：别指望每处埋点都自觉

只在业务代码里调用 `traceContent()` 和 `traceMetadata()`，其实还不够保险。

项目里建 Span 的地方不止一处：主对话、标题生成、幻觉检测、图表列分类、知识库问答都有各自的入口。只要以后加新功能时有一处忘了包 `traceContent()`，或者某个第三方库的自动埋点顺手把原始数据塞进了属性里，前面那套约定就形同虚设。

前端平时写请求封装，习惯在统一的拦截器里做最后一道兜底。OpenTelemetry + Langfuse 也留了同样的口子：在 [`src/server/instrumentation.ts`](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/server/instrumentation.ts#L10-L18) 初始化 `LangfuseSpanProcessor` 时，直接挂一个导出前钩子 `mask`：

```typescript
export const langfuseSpanProcessor = isEnabled
  ? new LangfuseSpanProcessor({ mask: ({ data }) => maskTelemetryAttribute(data) })
  : undefined

if (langfuseSpanProcessor) {
  const sdk = new NodeSDK({
    spanProcessors: [langfuseSpanProcessor],
  })
  sdk.start()
}
```

对应的 [`maskTelemetryAttribute()`](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/core/telemetry.ts#L24-L32) 逻辑很干脆：

```typescript
/** Defense in depth at the exporter, including observations created by other callers. */
export function maskTelemetryAttribute(data: unknown): unknown {
  if (captureTraceContent()) return traceContent(typeof data === 'string' ? parseAttribute(data) : data)
  const value = typeof data === 'string' ? parseAttribute(data) : data
  const safe = value && typeof value === 'object' && !Array.isArray(value)
    ? traceMetadata(value as Record<string, unknown>)
    : {}
  return typeof data === 'string' ? JSON.stringify(safe) : safe
}
```

所有即将离境的 `input`、`output`、`metadata` 属性都会经过这个函数。如果没开内容采集，就算某个调用方直接塞了 `{ input: 'secret', output: 'secret' }`，到导出层也会被洗成 `{}`；就算开了内容采集，也会再过一遍 `maskPII()` 才会发出去。

## 接入时踩过的两个隐蔽坑

上面两层写完，我以为防线已经严实了，结果在排查报错和用户标识时，又撞上两个特别容易漏掉的口子。

### 坑一：异常不能在 SDK 回调里直接 throw

写 `withTrace` 包装函数时，最自然的写法是在 `startActiveObservation` 的回调里 `try/catch`，记一下错误状态，然后把 `error` 原样 `throw` 出去，让外层业务照常处理。

问题就出在这个 `throw` 上。OpenTelemetry 和 Langfuse 的 SDK 一旦在回调里接到未捕获的异常，会自动调用类似 `recordException` 的逻辑，把 `error.message` 和 stack trace 直接抄进 Span 的异常属性里。

在后端跑数据库和模型请求时，`error.message` 往往比正文还危险：数据库连接失败可能带着 `mysql://root:password@db`，SQL 执行报错可能把整条查询语句和参数吐在报错里，上游模型 400 响应有时也会把部分请求文本原样带回来。你在业务层把 `input` 藏得再严实，一次报错直接从 `exception.message` 漏了个精光。

后来在 [`src/server/langfuse.ts`](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/server/langfuse.ts#L33-L56) 里，我把 `withTrace` 改成了“回调内吞掉、回调外重抛”：

```typescript
let failed = false
let failure: unknown
const result = await startActiveObservation(options.name, async (span) => {
  if (options.input) span.update({ input: traceContent(options.input) })
  if (options.metadata) span.update({ metadata: traceMetadata(options.metadata) })

  try {
    return await propagateAttributes({
      userId,
      sessionId: options.sessionId,
      tags: options.tags,
      traceName: options.name,
    }, fn)
  } catch (error) {
    failed = true
    failure = error
    // End the observation normally, then rethrow outside the SDK callback so
    // SDK exception attributes cannot copy SQL/provider response text.
    span.update({ level: 'ERROR', statusMessage: traceError(error) })
    return undefined
  }
})
if (failed) throw failure
return result as T
```

同时把 `statusMessage` 交给 [`traceError()`](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/core/telemetry.ts#L38-L43) 归一化：只保留四个已知错误类名（`AbortError`、`TimeoutError`、`ApiRequestError`、`AgentError`），其他未知错误一律折成 `'RequestError'`，绝不传 `error.message`。

这样一来，Span 正常结束，SDK 不会自动抄写异常原文；代码走出回调后 `if (failed) throw failure`，外层业务拿到的还是完整的原始错误对象，本地服务日志和错误恢复逻辑完全不受影响。

### 坑二：别拿原始 IP 凑数当 `userId`，以及流式结束时的 flush

很多教程里接追踪时，为了让后台能按用户聚合，在没有登录态的场景下会顺手把请求 IP 传给 `userId`。原始 IP 本身就是敏感标识，不应该默认当成遥测里的用户 ID。在 [`src/server/langfuse.ts`](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/server/langfuse.ts#L18-L20) 里，`resolveUserId()` 默认直接返回 `undefined`，保持匿名，只有接了认证体系的调用方显式传入不透明的 `userId` 时才上报。

另外在流式接口 [`src/server/functions/chat.ts`](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/server/functions/chat.ts#L113-L115) 里，对话流结束的 `finally` 块里有一行：

```typescript
void flushTraces().catch(() => console.warn('[chat] Trace flush failed'))
```

因为 OpenTelemetry 的 Span 处理器默认按批次异步导出，短生命周期或流式响应结束后不主动 `forceFlush()`，最后几个 generation 很容易还没发出去就被丢掉；但也不能让可观测性反客为主去阻塞或弄崩主链路，所以用 `void` 触发并吞掉 flush 报错，只记一条本地 warn。

为了防止以后重构时不小心把这几条底线改坏，我在 [`tests/agent-quality.test.ts`](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/tests/agent-quality.test.ts#L54-L66) 里直接写了回归断言：默认模式下 `traceContent` 必须返回 `undefined`，`traceMetadata` 必须过滤掉 `database`、`ip`、`sql`，`traceError(new Error('mysql://root:password@db'))` 必须返回 `'RequestError'`；哪怕打开了 `LANGFUSE_CAPTURE_CONTENT=true`，手机号和邮箱也必须经过脱敏。

## 小结

给 Agent 接可观测性，和前端平时接埋点最大的区别在于：上下文里装的往往是真实业务数据和敏感凭证。

- 默认只上报运行元数据（模型、token、耗时、消息数、错误类型），Prompt 和输出正文做成独立的 opt-in 开关，且开启后仍要过 PII 脱敏；
- Metadata 用白名单而不是黑名单，并在 SpanProcessor 导出层再挂一层 `mask` 做纵深防御；
- 报错不要在 SDK 的 observation 回调里直接往外 `throw`，先把错误类名归一化记录、正常结束 Span，再在回调外面重抛。

参考：

- [OpenTelemetry GenAI Semantic Conventions](https://github.com/open-telemetry/semantic-conventions-genai)
- [Masking Sensitive Data（Langfuse Docs）](https://langfuse.com/docs/observability/features/masking)
- [数问工作台 deepseek-db-chat](https://github.com/annoyc/deepseek-db-chat)
- [src/core/telemetry.ts](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/core/telemetry.ts#L1-L43)
- [src/server/instrumentation.ts](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/server/instrumentation.ts#L1-L19)
- [src/server/langfuse.ts](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/server/langfuse.ts#L18-L63)
- [src/core/model/invoke.ts](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/core/model/invoke.ts#L62-L96)
- [tests/agent-quality.test.ts](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/tests/agent-quality.test.ts#L54-L66)
