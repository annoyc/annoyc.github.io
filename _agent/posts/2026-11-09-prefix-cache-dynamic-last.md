---
title: 前缀缓存：system prompt 里会变的东西，一律往后放
slug: prefix-cache-dynamic-last
date: 2026-11-09 14:00:00
tags: [Agent, Prompt Caching, 成本, 数问工作台]
excerpt: Agent 每走一步都要把整段上下文重新发给模型，大部分内容其实和上一步一模一样。前缀缓存吃的就是这部分，但前提是前面的字节不能变。我回头看了自己项目里 system prompt 的拼法，才发现哪些地方做对了，哪些地方只是碰巧没出事。
---

前面写 Agent 循环的时候我提过一句：每一轮都要把整个 messages 重新发给模型。当时只关心“要不要压缩”，没细想另一面：既然每一步发的东西大部分都一样，服务端能不能别重复算？

能，这就是前缀缓存（prompt caching）。我是用 DeepSeek 的 API 才第一次认真看它的，后来发现各家都有，规则也差不多。

## 先把规则说清楚

缓存按前缀匹配。两次请求，从第一个 token 开始，一模一样的那一段可以复用，从第一个不一样的地方开始，后面全部重算，哪怕后面又碰巧一样。

DeepSeek 的[文档](https://api-docs.deepseek.com/guides/kv_cache)里写得很直白：默认开启，不用改代码；请求必须完整命中某个已保存的“前缀单元”才算命中；而且是 best-effort，不保证命中率。命中情况在响应的 `usage` 里：`prompt_cache_hit_tokens` 和 `prompt_cache_miss_tokens`。

前端同学可以类比成 HTTP 缓存里那个“URL 变一个字符就是新资源”，只不过这里是“前缀变一个字符，后面整段都失效”。

所以规则只有一条：越稳定的内容越靠前，越容易变的越靠后。听着像废话，但落到代码里，很容易被一行不起眼的模板字符串破坏。

## 数据说话

1 月有篇论文专门测了这件事：[Don't Break the Cache: An Evaluation of Prompt Caching for Long-Horizon Agentic Tasks](https://arxiv.org/abs/2601.06007)（arXiv:2601.06007）。他们在 OpenAI、Anthropic、Google 三家上，用带真实搜索工具调用的多轮任务跑了 500 多个会话，system prompt 约 1 万 token。我打开摘要核对过的结论：

- 开了缓存之后 API 费用降低 41% 到 80%，首 token 延迟改善 13% 到 31%。
- 缓存放在哪很重要。笼统地把整段上下文都标成可缓存，有时反而会让延迟变差；有控制地放缓存块，收益才稳定。
- 给出的建议里有两条我觉得最实用：动态内容放在 system prompt 的末尾，让稳定的前缀能被缓存；动态的工具结果不要放进被缓存的那一块。

这是它在自己实验设置下的结果，不同模型、不同提示长度会有出入，我没法保证我的项目也是这个数。但方向和我自己的体会一致。

## 我的 system prompt 是怎么拼的

数问工作台里，一次对话的 system prompt 在 [`src/server/agent.ts`](https://github.com/annoyc/deepseek-db-chat/blob/d7a9caf477daa1056e32df8763542774c91abcae/src/server/agent.ts#L297-L324) 的 `createDbAgent` 里拼出来。我删掉细节，只留骨架：

```typescript
const promptSections: string[] = [SYSTEM_PROMPT_CORE, SYSTEM_PROMPT_SCHEMA]

if (intentType !== 'analysis' && intentType !== 'explore_schema') {
  promptSections.push(SYSTEM_PROMPT_WORKFLOW)
}
if (intentType !== 'explore_schema' && intentType !== 'analysis') {
  promptSections.push(SYSTEM_PROMPT_FEW_SHOT)
}
if (intentType === 'analysis' || intentType === 'complex_query'
    || intentType === 'simple_query' || hasHistory) {
  promptSections.push(SYSTEM_PROMPT_ANALYSIS)
}
promptSections.push(SYSTEM_PROMPT_ADDITIONAL)
let systemPrompt = promptSections.join('\n')

// 往下全是动态的
systemPrompt += `\n\n当前时间: ${currentTime}`
systemPrompt += `\n当前连接的数据库: ${connection.database} (${connection.host}:${connection.port})`
// 意图路由提示、SQL 剩余次数、已执行 SQL 的记录……
```

顺序是：固定的大段文字在前，当前时间、连接信息、路由提示、剩余执行次数、执行记录依次排在后面。这个顺序就是为了缓存排的，README 里“确定性消息构建”说的也是这个：线以上不变，线以下才放会变的东西。但今天重读，我发现它没有我想的那么彻底。

这个顺序带来的实际收益在循环里。`maxSteps` 是 10，一次提问里模型最多走 10 步，每一步 system prompt 完全一样，messages 只往后追加。所以第 2 步起，前一步的整段输入都是前缀，只有新增的工具结果是 miss。这是最大的一块，我的 `mergeUsage` 里专门把两个字段累加起来，就是为了能看一次对话总共命中了多少。

## 但有两个地方没那么彻底

第一个，当前时间精确到分钟：`当前时间: 2026-11-09 14:03 星期一`。它在静态段后面，所以只会让它后面的那部分失效，静态大段不受影响。如果我当初顺手把它放到 prompt 开头（“你现在是 xx，现在时间是 xx”这种写法特别常见），整个 prompt 每分钟都换一个前缀。这种坑很隐蔽，功能完全正常，只是账单悄悄变高。

第二个，静态段本身不是“一整块”。`WORKFLOW`、`FEW_SHOT`、`ANALYSIS` 是按意图决定放不放的。意图是一个单独的 LLM 分类请求给出的，不同的问题可能分到不同的意图。意图一变，从被增减的那一段开始往后全部不同，而 `SYSTEM_PROMPT_ADDITIONAL` 是排在这些可选段后面的。所以跨轮次看，真正稳定共享的只有开头的 `CORE` 和 `SCHEMA` 两段，同一个意图的连续提问才能共享得更多。

这两点我都没有实测命中率，下面说的只是读代码得出的判断：同一次提问内部的缓存是稳的，跨提问的缓存取决于意图稳不稳定，大概率没有我 README 里写的那么理想。如果要改，方向是把 `ADDITIONAL` 这种每次都有的段挪到可选段前面，让共享前缀更长。不过挪动会改变模型看到的指令顺序，对行为有没有影响得重新跑一遍评测，我暂时没动。

## 几个容易踩的点

写这篇的时候我把容易破坏前缀的东西列了一遍，大部分我自己的项目里都有出现的可能：

- 时间戳、请求 ID、随机数放在开头。放末尾，或者干脆不放，让模型用工具查。
- 对 JSON 做序列化时 key 顺序不稳定。同一份数据 `JSON.stringify` 两次，只要对象构造顺序不同，字符串就不一样。要么固定顺序，要么排序后再序列化。
- 动态改工具列表。工具定义通常排在 system prompt 附近，增删一个工具，后面整段都 miss。论文里也提醒过动态的函数调用会破坏缓存。
- 改写历史消息。压缩、删除早期消息、重新排序，都会让前缀从改动处开始失效。所以压缩最好是攒到一定量再一次性做，别每轮都动。

一个最小的自检办法，不用等账单：每次请求完把 `usage` 里的两个字段打出来，算个命中占比。

```typescript
const { prompt_cache_hit_tokens: hit = 0, prompt_cache_miss_tokens: miss = 0 } = usage
console.log(`cache hit ${(hit / (hit + miss || 1) * 100).toFixed(1)}%`)
```

改一处提示词，看这个数掉没掉。比任何理论都直观。

## 小结

前缀缓存没有开关要你去配，它只要求你别乱动前面的字节。拼 system prompt 的时候，脑子里放一条线：线以上永远不变，线以下想怎么变怎么变。下一步我打算把命中占比接到 Langfuse 的 trace 上，到时候再回来看跨轮次到底是多少。

参考：

- [Don't Break the Cache: An Evaluation of Prompt Caching for Long-Horizon Agentic Tasks（arXiv:2601.06007）](https://arxiv.org/abs/2601.06007)
- [DeepSeek API 文档：Context Caching](https://api-docs.deepseek.com/guides/kv_cache)
- [数问工作台 deepseek-db-chat](https://github.com/annoyc/deepseek-db-chat)
- [src/server/agent.ts](https://github.com/annoyc/deepseek-db-chat/blob/d7a9caf477daa1056e32df8763542774c91abcae/src/server/agent.ts)
