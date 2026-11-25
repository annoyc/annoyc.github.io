---
title: Agent 的上下文占用指示器，别把计费 Token 累加当成窗口用量
slug: context-window-usage-not-billed-tokens
date: 2026-11-25 16:00:00
tags: [Agent, Context Engineering, 多模型适配, 数问工作台]
excerpt: 在数问工作台里加一个上下文窗口占用指示器时，才发现“本轮花了多少 token”和“窗口现在占了多少”完全是两笔账。拆解多步工具调用的账怎么分、流式 SSE 里的 usage 为什么不能直接累加、推理 token 在回合内外怎么算，以及多厂商窗口上限和前端拆解怎么对齐。
---

最近给数问工作台加了一个上下文窗口占用指示器，摆在输入框旁边：一个小圆环显示当前占用了百分之多少，点开能看到系统提示、对话历史、工具结果、输入框草稿各自占了多少，超过 60% 变黄，超过 85% 变红，方便决定什么时候新开会话。

站在前端视角，这需求第一眼看着特别简单：接口本来就会吐 `usage`，拿最后一条消息的 `total_tokens` 除以模型的上下文上限（比如 128k 或 1M），画个 SVG 圆环不就完了。

真动手接进多步 Agent 循环、又顺手切了几个不同模型厂商测了一圈，我才发现这事远没有画个进度条那么直白。很多数字乍看像“统计有点误差”，细查下去其实是把两笔完全不同的账混在了一起。

## 计费 Token 和上下文占用，根本是两笔账

在单轮一问一答的聊天产品里，`usage.total_tokens` 约等于当前窗口占了多少，差得不多。

一到带工具调用的 Agent 上，这个等式马上就失效了。数问工作台里用户问一句“查一下今年各月电量”，后端一轮跑下来往往要调三四次模型：先调一次意图分类，再进主循环调工具查表结构、生成 SQL 挂起等用户确认、确认完带着查询结果继续生成分析。假设这几步每次发给模型的输入分别是 3k、5k、7k、9k token，全加起来计费输入是 24k；可这一轮停下来时，真正躺在模型上下文窗口里的，只是最后一步那 9k 输入加上最后一步的有效回复，绝对不是 24k。

如果前端直接拿整轮累加的 `usage` 去画窗口进度条，用户跑两轮复杂查数，圆环就会虚高到爆表，明明离压缩阈值还早，界面却已经标红催着重开会话。

所以在数问工作台里，我把这两条数据流彻底拆开了。

在 [`src/core/generate/generate-loop.ts`](https://github.com/annoyc/deepseek-db-chat/blob/e298679d53d54c9db17f6eb774d14f526ef6d4b7/src/core/generate/generate-loop.ts#L167-L172) 的主循环里，每跑完一步，原来的 `mergeUsage(totalUsage, stepResult.usage)` 继续负责累加账单用量，同时单独多 `yield` 一个 `step-usage` 事件把这一步的快照送出去：

```typescript
mergeUsage(totalUsage, stepResult.usage)
lastFinishReason = stepResult.finishReason
lastPromptTokens = stepResult.usage.prompt_tokens
if (stepResult.usage.prompt_tokens > 0 || stepResult.usage.total_tokens > 0) {
  yield { type: 'step-usage', step: stepRef.value, usage: { ...stepResult.usage } }
}
```

往外推 SSE 的时候，[`src/server/chat-events.ts`](https://github.com/annoyc/deepseek-db-chat/blob/e298679d53d54c9db17f6eb774d14f526ef6d4b7/src/server/chat-events.ts#L106-L115) 把 `step-usage` 映射成 `context-usage` 帧，把循环结束的 `finish` 映射成 `usage` 帧。前端 [`src/hooks/useChat.tsx`](https://github.com/annoyc/deepseek-db-chat/blob/e298679d53d54c9db17f6eb774d14f526ef6d4b7/src/hooks/useChat.tsx#L114-L119) 收到后的处理刚好相反：

- `usage` 走累加（`sumTokenUsage`），用来算这一轮和整场会话一共烧了多少 token、花了多少钱；
- `context-usage` 直接覆盖（`buffer.contextUsage = chunk.usage`），只保留最新一步的窗口快照。

而且意图分类那次独立调用在 [`src/server/functions/chat.ts`](https://github.com/annoyc/deepseek-db-chat/blob/e298679d53d54c9db17f6eb774d14f526ef6d4b7/src/server/functions/chat.ts#L69-L71) 只发 `usage`，不发 `context-usage`——它虽然花钱，但根本不在主对话的上下文窗口里。

## 最小可运行骨架：流式快照、推理折算与拆解对平

把上面“账单累加 / 窗口覆盖”的分流，加上流式快照覆盖和跨回合推理 token 折算，抽成一个不依赖框架的 TypeScript 最小示例，核心就这几十行：

```typescript
interface RawUsage {
  prompt_tokens?: number
  completion_tokens?: number
  total_tokens?: number
  input_tokens?: number
  output_tokens?: number
  completion_tokens_details?: { reasoning_tokens?: number }
}

interface NormalizedUsage {
  promptTokens: number
  completionTokens: number
  reasoningTokens: number
}

const positiveInt = (v: unknown) =>
  typeof v === 'number' && Number.isFinite(v) && v > 0 ? Math.round(v) : 0

// 单次流式响应内：用最新正数覆盖，绝不累加，也绝不用尾帧 0 抹掉有效值
export function applyStreamUsage(target: NormalizedUsage, chunk: RawUsage): void {
  const prompt = positiveInt(chunk.prompt_tokens ?? chunk.input_tokens)
  const completion = positiveInt(chunk.completion_tokens ?? chunk.output_tokens)
  const reasoning = positiveInt(chunk.completion_tokens_details?.reasoning_tokens)

  if (prompt > 0) target.promptTokens = prompt
  if (completion > 0) target.completionTokens = completion
  if (reasoning > 0) target.reasoningTokens = reasoning
}

// 计算当前窗口实际占用：区分“同一轮中途”和“回合已结束”
export function computeWindowTokens(params: {
  lastStepUsage: NormalizedUsage
  inSameTurn: boolean
  replaysReasoningInTurn: boolean
  draftTokens: number
}): number {
  const { lastStepUsage, inSameTurn, replaysReasoningInTurn, draftTokens } = params
  const keepReasoning = inSameTurn && replaysReasoningInTurn
  const effectiveCompletion = keepReasoning
    ? lastStepUsage.completionTokens
    : Math.max(0, lastStepUsage.completionTokens - lastStepUsage.reasoningTokens)

  return lastStepUsage.promptTokens + effectiveCompletion + draftTokens
}
```

这套逻辑看着很干净，但每加一行条件，背后都是接真实厂商时实打实撞出来的坑。

## 真正踩过的三个坑

### 坑一：SSE 流式里的 `chunk.usage` 不能用累加函数去收

以前在 [`src/core/generate/generate-stream.ts`](https://github.com/annoyc/deepseek-db-chat/blob/e298679d53d54c9db17f6eb774d14f526ef6d4b7/src/core/generate/generate-stream.ts#L40-L56) 里，请求开了 `streamOptions: { include_usage: true }`，每收到一个带 `chunk.usage` 的 SSE 块，代码里顺手调的是 `mergeUsage(stepUsage, chunk.usage)`。

只接 DeepSeek 或 OpenAI 官方接口时，这个写法一直没出过事。按 OpenAI Chat Completions 的协议，开启 `include_usage` 后前面所有分片里的 `usage` 都是 `null`，只有流结束前最后那个空 `choices` 的分片会带一次完整统计，调 `mergeUsage` 等同于赋值一次。

一接上阿里云百炼（DashScope）、Gemini 兼容端点或者部分中转网关，数字直接飞了。这些服务会在流式过程中每个 chunk 都带一个截至当前的累积快照——第一帧报 `prompt: 1200, completion: 10`，第二帧报 `prompt: 1200, completion: 45`，第三帧报 `prompt: 1200, completion: 90`。原来的 `mergeUsage` 把三帧全加在一起，1200 的输入瞬间变成了 3600；一条长回复吐几百个 chunk，上下文占用直接算爆。

更阴的是，有些网关在最后一个结束帧还会塞一个 `{ prompt_tokens: 0, completion_tokens: 0 }`。如果粗暴改成 `stepUsage = chunk.usage`，前面好不容易拿到的数又会被最后一帧清零。

所以后来我专门在 [`src/core/generate/generate-utils.ts`](https://github.com/annoyc/deepseek-db-chat/blob/e298679d53d54c9db17f6eb774d14f526ef6d4b7/src/core/generate/generate-utils.ts#L68-L109) 里写了 `applyStreamUsage` 和 `mergeUsage` 拆开用：跨步骤汇总账单用 `mergeUsage`，单次流内解析 SSE 分片一律用 `applyStreamUsage`，只在字段是正整数时覆盖，遇到 0 直接忽略。顺手还在 [`src/lib/usage.ts`](https://github.com/annoyc/deepseek-db-chat/blob/e298679d53d54c9db17f6eb774d14f526ef6d4b7/src/lib/usage.ts#L42-L54) 里把不同厂商的字段名一起归一化了——输入输出兼容 `prompt_tokens / input_tokens` 和 `completion_tokens / output_tokens`，缓存命中兼容了四种写法（`prompt_cache_hit_tokens`、`prompt_tokens_details.cached_tokens`、`cached_tokens`、`cache_read_input_tokens`）。

### 坑二：思维链 `reasoning_tokens` 算不算占窗口，回合内外不是一回事

开着思考模式跑模型时，`completion_tokens` 里是包含 `reasoning_tokens` 的。这部分 token 在当前这次生成时确实占窗口，也会按输出计费（OpenAI 的推理模型文档里就专门提醒过，起步阶段最好给推理加输出预留至少 25,000 token 的空间，防止还没吐出正文就被 `max_output_tokens` 截断）。

但等这一轮对话结束、用户准备发下一句时，上一轮那几千个思维链 token 还占不占下一轮的上下文窗口？

这得看厂商怎么处理历史里的思考块。DeepSeek 官方的 Thinking Mode 文档写得很明确：普通多轮对话（不带 `tools`）里，上一轮的 `reasoning_content` 不会被拼进下一轮上下文，就算客户端传回去 API 也会忽略；但在带 `tools` 的同一轮工具调用链里，必须把前面的 `reasoning_content` 原样传回，它会被拼进上下文继续推理。Anthropic 的 Context Windows 文档里也专门列过差异：早期模型和 Haiku 4.5 及以前版本会自动把历史回合的 thinking blocks 从上下文里剥掉，而 Opus 4.5、Sonnet 4.6 及之后的模型默认会把历史 thinking blocks 留下来当输入 token 算。

数问工作台在跨回合组装历史时，只把上一轮的最终回复放进 `history`，不会把上一轮的思维链再塞回去；但在同一轮中途（正在流式输出，或者执行 SQL 前挂起等待人工确认 `lastMsg?.agentState`）如果走的是 DeepSeek 思考模式，[`generate-stream.ts`](https://github.com/annoyc/deepseek-db-chat/blob/e298679d53d54c9db17f6eb774d14f526ef6d4b7/src/core/generate/generate-stream.ts#L85-L92) 会把 `reasoning_content` 留在消息里供恢复后回放。所以 [`src/lib/context-window.ts`](https://github.com/annoyc/deepseek-db-chat/blob/e298679d53d54c9db17f6eb774d14f526ef6d4b7/src/lib/context-window.ts#L375-L385) 算实测占用时做了这个区分：

```typescript
const inSameTurn = Boolean(isStreaming || lastMsg?.agentState)
const normalizedId = (modelId ?? '').trim().toLowerCase()
const replaysReasoning = thinkingStyle === 'deepseek' || (thinkingStyle === undefined && normalizedId.includes('deepseek'))
const keepReasoning = inSameTurn && replaysReasoning && thinkingMode !== 'disabled'
const effectiveCompletion = keepReasoning
  ? measured.completionTokens
  : Math.max(0, measured.completionTokens - (measured.reasoningTokens ?? 0))
const measuredTotal = measured.promptTokens + effectiveCompletion
```

如果不扣掉已结束回合里的 `reasoningTokens`，深度推理一轮动辄思考四五千字、正文只回两百字，进度条会长期虚高一大截；反过来如果在 DeepSeek 挂起等确认时扣掉了，真点完确认恢复那一刻又会低估接下来要发出去的 prompt 长度。

### 坑三：分母没有统一标准，前端拆解也得和实测总账对平

分子算准了，分母（模型上下文窗口上限）一样是个雷区。OpenAI 兼容的 `/models` 接口压根没规定上下文长度该叫什么字段：OpenRouter 返回 `context_length` 或 `top_provider.context_length`，vLLM 返回 `max_model_len`，还有各种网关用 `context_window`、`max_input_tokens` 或 `limits.context`。

我先在 [`src/server/providers.ts`](https://github.com/annoyc/deepseek-db-chat/blob/e298679d53d54c9db17f6eb774d14f526ef6d4b7/src/server/providers.ts#L65-L93) 里按候选列表依次从 `/models` 响应里捞，再在 [`src/lib/context-window.ts`](https://github.com/annoyc/deepseek-db-chat/blob/e298679d53d54c9db17f6eb774d14f526ef6d4b7/src/lib/context-window.ts#L94-L117) 里定了一套五级优先级：用户手动设置 > `/models` 接口获取 > 服务端静态配置 > 内置已知模型表 > 128k 兜底。UI 上允许用户点一下小铅笔直接改上限（支持输 `128k`、`1M`），因为自建网关或新发布模型总会跑在硬编码规则前面。

另一个前端细节是进度条的分段拆解。模型接口只给一个总 `promptTokens`，不会告诉你里面系统提示、历史对话、工具结果各占多少；用户在输入框里打字时，消息还没发出去，也没有服务端实测值。我的做法在 [`reconcileMeasuredBreakdown`](https://github.com/annoyc/deepseek-db-chat/blob/e298679d53d54c9db17f6eb774d14f526ef6d4b7/src/lib/context-window.ts#L177-L202) 里：以服务端返回的 `measuredTotal` 为绝对锚点，前端用轻量中英文字符权重（`Math.ceil(cjk * 0.7 + ascii * 0.28 + other * 0.5)`）估出历史和工具结果，把差值归到系统提示与工具定义里（如果本地估超了，就留出一小块系统保底后按比例缩回 `measuredTotal`），再把输入框草稿的估算值叠在最外面。同时在 [`ContextUsageIndicator.tsx`](https://github.com/annoyc/deepseek-db-chat/blob/e298679d53d54c9db17f6eb774d14f526ef6d4b7/src/components/chat/ContextUsageIndicator.tsx#L63-L67) 卡片右上角诚实标出当前数字是“实测”“实测 + 估算”还是“估算”，不拿猜出来的数冒充精确值。

为了防止以后改多模型适配时把这些边界弄坏，我把流式累积覆盖、尾帧归零、五级窗口优先级、以及 DeepSeek 挂起态 vs 阿里云百炼挂起态的 `reasoningTokens` 扣减全写进了 [`tests/context-window.test.ts`](https://github.com/annoyc/deepseek-db-chat/blob/e298679d53d54c9db17f6eb774d14f526ef6d4b7/tests/context-window.test.ts#L126-L226) 的单测里。

## 小结

给多步 Agent 做上下文窗口占用指示器，核心是把“账单思维”和“工作内存思维”拆开：

- 计费 token 跨步骤、跨辅助调用做累加；上下文占用只看主循环最后一步的快照，收到新步骤直接覆盖。
- 单次 SSE 流里的 `chunk.usage` 要当成“截至当前的快照”按正数覆盖，不能拿跨步骤的累加函数去收，否则接非 OpenAI 官方网关时数字会成倍虚高。
- `reasoning_tokens` 是否计入当前占用，要跟着真实的上下文组装逻辑走——已结束回合会被剥离的就扣掉，同一轮工具链内要原样回放的就保留。
- 模型窗口上限和前端分段拆解都做降级兜底，并且用总实测值把分段对平，在 UI 上标明哪些是实测、哪些是估算。

参考：

- [Context windows（Anthropic Docs）](https://docs.anthropic.com/en/docs/build-with-claude/context-windows)
- [Thinking Mode（DeepSeek API Docs）](https://api-docs.deepseek.com/guides/thinking_mode)
- [Reasoning models（OpenAI API Docs）](https://developers.openai.com/api/docs/guides/reasoning)
- [数问工作台 deepseek-db-chat](https://github.com/annoyc/deepseek-db-chat)
- [src/core/generate/generate-loop.ts](https://github.com/annoyc/deepseek-db-chat/blob/e298679d53d54c9db17f6eb774d14f526ef6d4b7/src/core/generate/generate-loop.ts#L167-L172)
- [src/core/generate/generate-stream.ts](https://github.com/annoyc/deepseek-db-chat/blob/e298679d53d54c9db17f6eb774d14f526ef6d4b7/src/core/generate/generate-stream.ts#L40-L56)
- [src/core/generate/generate-utils.ts](https://github.com/annoyc/deepseek-db-chat/blob/e298679d53d54c9db17f6eb774d14f526ef6d4b7/src/core/generate/generate-utils.ts#L68-L109)
- [src/server/chat-events.ts](https://github.com/annoyc/deepseek-db-chat/blob/e298679d53d54c9db17f6eb774d14f526ef6d4b7/src/server/chat-events.ts#L106-L115)
- [src/lib/usage.ts](https://github.com/annoyc/deepseek-db-chat/blob/e298679d53d54c9db17f6eb774d14f526ef6d4b7/src/lib/usage.ts#L42-L54)
- [src/lib/context-window.ts](https://github.com/annoyc/deepseek-db-chat/blob/e298679d53d54c9db17f6eb774d14f526ef6d4b7/src/lib/context-window.ts#L94-L117)
- [src/server/providers.ts](https://github.com/annoyc/deepseek-db-chat/blob/e298679d53d54c9db17f6eb774d14f526ef6d4b7/src/server/providers.ts#L65-L93)
- [src/components/chat/ContextUsageIndicator.tsx](https://github.com/annoyc/deepseek-db-chat/blob/e298679d53d54c9db17f6eb774d14f526ef6d4b7/src/components/chat/ContextUsageIndicator.tsx#L63-L67)
- [tests/context-window.test.ts](https://github.com/annoyc/deepseek-db-chat/blob/e298679d53d54c9db17f6eb774d14f526ef6d4b7/tests/context-window.test.ts#L126-L226)
