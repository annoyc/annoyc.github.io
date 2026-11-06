---
title: 工具调用超时了，别急着重试：它可能已经成功了
slug: tool-timeout-unknown-outcome
date: 2026-11-06 14:00:00
tags: [Agent, 工具调用, 错误恢复, 数问工作台]
excerpt: 上一篇末尾我留了个没想清楚的问题：不可重复的工具，丢了结果就找不回来。这篇接着讲它的另一面：工具超时之后，结果是“未知”，不是“失败”。看看我自己的数据库 Agent 里是怎么处理这件事的，再对照 10 月底的一篇论文。
---

上一篇最后我写了一段没想清楚的话：如果工具是下单、发消息这种不可重复的，“丢了结果再调一次”就不成立。这两天回头翻自己的项目，发现其实我早就在代码里碰到过同一类问题，只是当时没往“方法论”上想。

场景很具体：Agent 调一个工具，等了 60 秒，超时了。这时候你手里有什么？

一个 `TimeoutError`。仅此而已。你不知道请求有没有到服务端，不知道服务端有没有执行，不知道执行了一半还是全做完了，只是响应没回来。

写前端的人对这个不陌生。`fetch` 超时了，你不会直接假设后端没收到。支付按钮点了没反应，用户再点一次，这就是为什么支付接口要带幂等 key。Agent 的工具调用，本质上是同一类问题，只不过重试的发起者从用户的手指换成了一个会自己做决定的模型。

## 失败有两种

我现在把工具调用的“没拿到结果”分成两类来看：

- 明确失败：参数校验没过、返回了 4xx、连接都没建立起来。可以确定没有产生副作用，重试是安全的。
- 结果未知：超时、连接中途断了、取消了请求。副作用可能已经发生。

很多框架的重试逻辑只看“有没有抛异常”，把这两类混在一起。这是我之前自己写的 `withRetry` 也犯过的毛病。

## 我项目里的做法

[数问工作台](https://github.com/annoyc/deepseek-db-chat)是我做的一个自然语言查数据库的 Agent。工具层有一个统一的包装函数，每个工具都能配超时和重试次数。核心是这段（`src/core/tool/index.ts`，我删了无关的部分）：

```typescript
async function withRetries<T>(
  fn: (signal: AbortSignal) => Promise<T>,
  maxRetries: number,
  timeoutMs?: number,
  signal?: AbortSignal,
): Promise<T> {
  for (let attempt = 0; ; attempt++) {
    signal?.throwIfAborted()
    const scope = createAbortScope(signal, timeoutMs)
    try {
      return await abortable(fn(scope.signal), scope.signal)
    } catch (error) {
      signal?.throwIfAborted()
      // A timed-out operation may still be finishing: never replay it automatically.
      if (scope.signal.aborted || attempt >= maxRetries) throw error
    } finally {
      scope.abort()
      scope.dispose()
    }
  }
}
```

[源码在这里](https://github.com/annoyc/deepseek-db-chat/blob/53912725cecacb4daa1525babd9ea70f91403572/src/core/tool/index.ts#L9-L30)。

关键是中间那个判断：`scope.signal.aborted` 为真就直接抛，不重试。每次尝试都会创建一个带截止时间的 `AbortScope`，超时到了，scope 的 signal 就被 abort，于是走到这个分支。也就是说，只有函数自己抛了错、而 signal 还没被 abort 的情况（比如参数问题、连接被拒），才会按 `retries` 重试。超时和取消一律不重试，不管 `retries` 配了几次。

而且默认的 `retries` 是 0，要重试得工具自己声明。目前我项目里没有一个工具开了它。这是我有意的：默认保守，真有需要再显式打开。

## 为什么对数据库工具尤其重要

这个 Agent 的 SQL 执行默认只读，写操作要同时满足好几个条件（服务端开关、白名单、非 prod 环境），还要用户点确认。读操作超时了重试，最多是多花点时间。但一旦走到写操作，情况就不一样了。

`src/server/database.ts` 里执行 SQL 的地方有这样一段，请求被取消时的错误信息：

```typescript
const cancelled = new Error(prepared.isWrite
  ? 'SQL 请求已取消；写操作可能已经提交，断开连接不代表回滚，请核对数据库结果'
  : 'SQL 请求已取消，数据库连接已断开')
```

[源码](https://github.com/annoyc/deepseek-db-chat/blob/53912725cecacb4daa1525babd9ea70f91403572/src/server/database.ts#L298-L304)。这句话是踩过一次想法上的坑之后加的：我一开始以为，连接一断，数据库自然会把没提交的事务回滚掉，所以取消是“干净”的。可对一条已经发到服务端、正在执行或者刚执行完的 `UPDATE`，客户端这边断开，并不能保证它被撤销。从客户端看到的只有“没收到结果”。

所以读和写的提示不同。读：连接断了，重新问一遍就行。写：明确告诉用户（和模型），结果未知，请去核对。

还有一点，我是后来才想到的：这条错误信息最终是会作为工具结果回到模型的上下文里的。模型读到“写操作可能已经提交”，就不太会自作主张地再发一遍同样的 `UPDATE`。信息写得准确，本身就是一层防护。如果只回一个 `timeout`，模型很可能理解成“失败了”，于是顺手重试。

## 对照一篇 10 月底的论文

写之前我去搜了一下有没有人系统地研究这件事，找到一篇 10 月 30 日提交到 arXiv 的：[Evidence-Guided Recovery for AI Agents with Ambiguous Tool Outcomes and Partial Execution](https://arxiv.org/abs/2610.41453)（arXiv:2610.41453）。

我只读了摘要和页面上的信息，没有逐页读完全文，所以这里只说我确认过的部分。它研究的正是这个场景：工具调用超时，动作可能失败、可能已提交、可能还在执行，同时流程里别的步骤已经完成了。作者把恢复建模成在“已批准的义务、服务方声明的能力、和与公开证据一致的状态”上的问题，另外有一个独立的“效果历史裁决器”，负责判断哪些恢复动作是被允许的。他们还强调一点：未知和移交（交给人处理）的结果要保持显式，保证只在服务方声明的能力范围内成立，不假装能做到任意第三方的“恰好执行一次”。

实验部分有一组预先登记的对比：58 组配对任务，分布在 19 个任务族里，两个本地模型。用“规划建议”的方案认证完成了 30 个，用“证据提示”（把证据塞进 prompt 让模型自己判断）的只有 9 个，差 36.2 个百分点；提议的动作数是 272 对 399。这些是论文自己的合成任务，规模也不大，我不会拿它去推断真实系统里的数字。但方向我觉得和直觉一致：光把“可能已经成功”这件事写进 prompt，让模型自己想，不如在模型外面再加一层有明确规则的检查。

我的项目没有做到论文那种程度。我这边只是在两处做了朴素的版本：超时不自动重试，写操作的未知结果如实说出来、交给人。

## 一个自己还没解决的地方

现在我是靠“人去核对”兜底的。这在一个由人确认每条写操作的工具里还能接受，换成全自动的 Agent 就不够了。真正的办法是让写操作可查证：给每次写带一个请求 ID，超时后能反查“这个 ID 有没有生效”。数据库场景下要做这件事，得有一张操作记录表，或者把写操作限定成天然幂等的形式（比如 `UPDATE ... SET status = 'done' WHERE id = ?`，重复执行结果一样）。这块我还没做，先记在这里。

## 小结

工具调用的“没拿到结果”不等于“失败”。超时、取消、断连这几类，状态是未知的，默认别重试，至少对有副作用的工具是这样。把这个区别写进两个地方：重试逻辑里（超时直接放弃），和返回给模型的错误文案里（如实说“可能已经成功”）。

参考：

- [Evidence-Guided Recovery for AI Agents with Ambiguous Tool Outcomes and Partial Execution（arXiv:2610.41453）](https://arxiv.org/abs/2610.41453)
- [数问工作台 deepseek-db-chat](https://github.com/annoyc/deepseek-db-chat)
- [src/core/tool/index.ts 的 withRetries](https://github.com/annoyc/deepseek-db-chat/blob/53912725cecacb4daa1525babd9ea70f91403572/src/core/tool/index.ts#L9-L30)
- [src/server/database.ts 的取消处理](https://github.com/annoyc/deepseek-db-chat/blob/53912725cecacb4daa1525babd9ea70f91403572/src/server/database.ts#L298-L304)
