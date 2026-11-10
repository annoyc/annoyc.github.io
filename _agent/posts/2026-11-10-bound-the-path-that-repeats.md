---
title: 给 Agent 加了 maxSteps 还是会死循环：上限得套在真正重复的那条路径上
slug: bound-the-path-that-repeats
date: 2026-11-10 14:00:00
tags: [Agent, Agent Loop, 成本, 数问工作台]
excerpt: 我以为循环里写了 maxSteps 就安全了，后来对着一篇扫了 6000 多个 Agent 仓库的论文，回头查自己的项目，才发现有些重复根本不经过那个计数器。这篇讲怎么判断一个上限到底罩没罩住该罩的路径。
---

第一篇写 Agent 循环的时候，我列过三道闸：最大轮数、重复调用检测、完成校验。写完觉得挺稳，`maxSteps` 都有了，还怕什么死循环。

前阵子看到一篇 7 月的论文，标题就叫 [When Agents Do Not Stop](https://arxiv.org/abs/2607.01641)，才发现我对“有上限”这件事想得太简单了。

## 论文量了什么

作者做了个静态分析工具 IAL-Scan，扫 Agent 代码里“反馈路径没有有效上限”的地方，把这种问题叫 Infinite Agentic Loop。我打开原文核对过的数字：扫了 6549 个 LLM Agent 仓库，工具报了 74 处，人工确认 68 处是真的，分布在 47 个项目里，准确率 91.9%。

确认下来的 68 处，按模式分：

- 重试反馈没有上限：17 处（25.0%）
- 工具调用迭代没有上限：16 处（23.5%）
- 多 Agent 对话没有轮数上限：14 处（20.6%）
- 其余是 workflow 循环、消息重入、runner/评估器反馈

按根因看（可以多选）：工具控制的重试占 41.2%，由模型控制终止的占 38.2%。后果方面，95.6% 会烧 API 费用。

我最在意的是论文给应用开发者的两条建议。一条是别指望模型自己会停，另一条是：配置的上限必须真的罩住那条重复的路径，放在反馈路径外面的上限保护不了它。

第二条我当时读了两遍。因为我的项目里确实有个 `maxSteps`，我一直默认它罩住了所有重复。

## 先看循环本身

数问工作台的 Agent 循环在 [`src/core/generate/generate-loop.ts`](https://github.com/annoyc/deepseek-db-chat/blob/055e6922e6093043ef7151c60cf4a9d8ee355640/src/core/generate/generate-loop.ts#L68)，核心就是这样：

```typescript
while (stepRef.value < maxSteps) {
  signal?.throwIfAborted()
  stepRef.value++
  // 调模型 → 有 tool call 就执行并 continue → 没有就返回文本
}

// 走出循环，说明一直没拿到最终回复
throw new AgentError({ type: 'max_steps', retryable: false, /* ... */ })
```

默认上限是 50（`AGENT_LOOP_MAX_STEPS`），但做数据库问答的那个 Agent 在 [`agent.ts`](https://github.com/annoyc/deepseek-db-chat/blob/055e6922e6093043ef7151c60cf4a9d8ee355640/src/server/agent.ts#L381) 里显式传了 `maxSteps: 10`。到了上限不是悄悄停，而是抛一个不可重试的 `max_steps` 错误，让上层知道这次没跑完。这一点我觉得是对的：静默截断会让用户拿到半截答案还以为是完整的。

这是第一层，管的是“一次请求里，模型和工具来回多少次”。

## 第二层：同一个调用别重复

只靠步数，模型可以把 10 步全花在同一个调用上。所以循环里还有一个很土的检测：

```typescript
const currentStepKeys = stepResult.toolCalls.map(tc => makeToolCallKey(tc))
const allDuplicate = currentStepKeys.every(key => seenToolCallKeys.has(key))

if (allDuplicate) consecutiveDuplicateSteps++
else consecutiveDuplicateSteps = 0
currentStepKeys.forEach(key => seenToolCallKeys.add(key))

if (consecutiveDuplicateSteps >= 2) {
  // 模型卡在重复调用上，强制结束
  return { text: lastAssistantMsg(currentMessages), output: undefined, usage: totalUsage }
}
```

key 是“工具名 + 参数”，参数先 `JSON.parse` 再按 key 排序后序列化。这是个小坑：同一个调用，模型两次生成的 JSON 字段顺序可能不同，直接比字符串就漏了。

这段我现在回头读，有两处不满意。一是它在 return 之前往 messages 里 push 了一条“重复调用被阻止”的 tool 消息，但紧接着就 return 了，这条消息没有机会被模型看到，等于白写。二是它只认“参数完全一样”。模型要是每次把 SQL 里改个别名、加个空格，key 就变了，这一层就罩不住，只能靠第一层的 10 步兜底。

## 真正漏的地方：循环外面

这才是读完论文后让我出冷汗的部分。

我的 Agent 不是常驻的。用户每发一条消息，服务端 `createDbAgent` 就新建一个 Agent，`stepRef` 从 0 开始，`seenToolCallKeys` 也是新的。也就是说，`maxSteps: 10` 和重复检测，都只在“一次请求”内部有效。

但一个会话里，用户可以一直问，也可以每次都点“确认执行”。每次确认又是一轮新的请求。如果模型每一轮都生成一条新的 SQL，每一轮都没超过 10 步，两道闸一次都不会触发，数据库却被执行了几十次。这条反馈路径是“跨请求”的，`maxSteps` 压根没经过它。

所以我在工具里另外加了会话级的上限，在 [`src/server/tools.ts`](https://github.com/annoyc/deepseek-db-chat/blob/055e6922e6093043ef7151c60cf4a9d8ee355640/src/server/tools.ts#L216) 的 `execute_sql` 里：

```typescript
// 会话级执行次数上限
if (sqlExecutedCount >= maxSqlExecutions) {
  return JSON.stringify({
    status: 'limit_reached',
    message: `本会话已执行 ${maxSqlExecutions} 条 SQL，达到上限。请基于已有的查询结果给出最终回复，不再生成新的 SQL。`,
  })
}

// 同一次 run 内，完全相同的 SQL 直接拦
const normalizedSql = sql.trim().replace(/\s+/g, ' ')
if (submittedSqlSet.has(normalizedSql)) { /* duplicate_blocked */ }

// 和上一轮用户已经确认过的 SQL 相同，也拦
if (lastConfirmedSql && normalizedSql === lastConfirmedSql.trim().replace(/\s+/g, ' ')) { /* duplicate_blocked */ }
```

默认上限 `SESSION_MAX_SQL_EXECUTIONS = 20`，设置里可以改，范围 1 到 100。计数从哪来？前端 `useChat` 每次请求带上 `executionLog.length` 传给服务端。快用完的时候（剩 5 次以内）还会在 system prompt 里提醒模型“次数即将用尽，尽快给出最终回复”。

这里的思路就是论文那句话：上限要放在重复真正发生的那一层。重复发生在“会话里反复执行 SQL”，那计数器就得挂在执行 SQL 这个动作上，并且是会话级的，不能是 Agent 实例级的。

## 还有两个我没解决干净的

第一，这个计数是客户端传上来的。用户打开开发者工具把 `sqlExecutedCount` 改成 0，上限就废了。所以它只是防模型失控的护栏，不是安全边界。真正拦危险 SQL 的是前几篇写过的执行入口策略，这两件事不要混着看。

第二，`duplicate_blocked` 和 `limit_reached` 返回的都是一段“请直接给最终回复”的文字，靠的是模型听话。多数时候它听，但论文里 38.2% 的案例根因就是“终止由模型决定”，我没法保证它永远听。好在这几种情况下，只要它不听，继续调 `execute_sql`，最后还是会撞上第一层的 10 步。被拦下的调用不计入执行次数，所以它兜底靠的是步数，不是配额。我现在认为这个搭配是合理的，但也只是读代码得出的判断，没有做过压测。

## 自查清单

照着论文的分类，我给自己的 Agent 列了几个要问的问题，前端同学拿去检查自己的也行：

- 每条会重复的路径，是不是都有计数器？重试、工具调用、子 Agent 委派、用户“再试一次”，分开看。
- 这个计数器的生命周期，和重复的生命周期一样长吗？每次请求新建的变量，罩不住跨请求的重复。
- 到上限时是报错还是静默？最好报错，并且让上层能区分“正常结束”和“被截断”。
- 重试有没有退避和次数？我项目里的 [`withRetry`](https://github.com/annoyc/deepseek-db-chat/blob/055e6922e6093043ef7151c60cf4a9d8ee355640/src/core/client/retry.ts) 是 `maxRetries` 次，指数退避加抖动，并且只重试标了 `retryable` 的错误。模型解析失败那种“让它再生成一次”的重试，同样要有上限。
- 消息历史长到一定程度，有没有拦？论文把“状态增长”也算作放大器之一（27.9% 的案例）。

## 小结

我原来以为“加了 maxSteps”和“不会死循环”是一回事。现在觉得它们之间差着一句话：这个上限到底罩住了哪条路径。写完这篇我把会话级那个配额的数据流又过了一遍，下一步打算把客户端传计数改成服务端按会话 ID 自己记。

参考：

- [When Agents Do Not Stop: Uncovering Infinite Agentic Loops in LLM Agents（arXiv:2607.01641）](https://arxiv.org/abs/2607.01641)
- [数问工作台 deepseek-db-chat](https://github.com/annoyc/deepseek-db-chat)
- [src/core/generate/generate-loop.ts](https://github.com/annoyc/deepseek-db-chat/blob/055e6922e6093043ef7151c60cf4a9d8ee355640/src/core/generate/generate-loop.ts)
- [src/server/tools.ts](https://github.com/annoyc/deepseek-db-chat/blob/055e6922e6093043ef7151c60cf4a9d8ee355640/src/server/tools.ts)
