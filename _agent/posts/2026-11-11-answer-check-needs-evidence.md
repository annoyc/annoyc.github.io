---
title: 让模型检查模型有没有编数据：先想清楚检查器手里拿的是什么
slug: answer-check-needs-evidence
date: 2026-11-11 14:00:00
tags: [Agent, 幻觉检测, 可靠性, 数问工作台]
excerpt: 我项目里有个“回复检查器”，模型答完再调一次模型，看它有没有编造查询结果。第一版只给检查器两个布尔值，结果它自己也在编。这篇讲我怎么把它改成对着证据查，以及现在还剩哪些洞。
---

做自然语言查库，最怕的一种回复长这样：用户问“上个月退款多少笔”，模型没调任何工具，直接答“共 1,283 笔，环比上升 12%”。格式漂亮，语气笃定，数字全是编的。

数问工作台里为这个加过一道检查：模型答完，再调一次模型，判断这条回复有没有编造结果，有就打回去重答。这个东西我前后改过好几回，今天把它翻出来重新读了一遍，正好最近也看了两篇讲同一件事的文章，一起记下来。

## 第一版：只给了两个布尔值

最早的检查器拿到的输入只有三样：助手的回复文本、`hasToolCalls`（这一轮有没有调工具）、`isContinuation`（会话里有没有真实的 SQL 执行记录）。prompt 里还有一条“最高优先级规则”，这是 6 月那个版本的原话：

```
如果 isContinuation = true，说明助手的上下文中包含真实的 SQL 执行结果。
此时助手回复中出现的任何具体数据（姓名、手机号、邮箱、数字、ID、状态值等）
都应被视为来自真实执行结果或会话历史记录，不算幻觉。
```

现在看这条规则问题很大。它的意思是：只要这个会话里执行过 SQL，后面回复里出现什么数字都放行。可“执行过 SQL”和“这个数字出自那次执行”是两回事。查了一张 5 行的用户表，接着编一个全站 GMV，检查器照样说没问题。

当时我没意识到这点，反而觉得检查得不够细，又加了一项“事实校验”：把 SQL 结果的统计摘要也传过去，让检查器判断回复里的数字和摘要是否矛盾，输出 `hasFactError` 和一段 `factErrorDetail`。

这项功能只活了一天，第二天就撤了。撤掉它的那次提交，说明里我写的是：the LLM-based fact-checking was itself hallucinating incorrect validation claims。让模型对着一份统计摘要挑数字的错，它挑出来的“错”有一部分是它自己编的。而检查器一旦判了可疑，前端会清空这条回复、让模型重答，所以误判的代价是一条本来正确的答案在用户眼前消失。

## 两篇文章说的是同一件事

DEV 上有篇 [Hallucination is not a vibe](https://dev.to/saurav_bhattacharya/hallucination-is-not-a-vibe-how-to-actually-detect-ungrounded-claims-in-agent-output-349l)，里面有句话我很认同：幻觉检测是个埋点问题，跟模型质量关系没那么大。大部分团队测不了幻觉，是因为一次运行结束后只留了最终答案，工具输出和检索到的上下文都扔了。没有这些，所谓的检查就是让一个模型凭感觉给另一个模型打分。

作者把“幻觉”拆成三种，各自要用不同的办法查：

- 模型没用给它的数据，凭训练记忆答的（有时候碰巧还答对了）
- 引用了上下文里根本不存在的 ID、字段、数字
- 每条事实都有出处，但结论推不出来

他的建议是前两种用确定性的集合比对，回复里提到的引用在不在证据里，一个 `includes` 就能判；只有第三种才交给模型当裁判。

另一篇是 3 月的 arXiv 论文 [Tool Receipts, Not Zero-Knowledge Proofs](https://arxiv.org/abs/2603.10060)，走得更远：运行时给每次工具执行签一张 HMAC 回执，模型伪造不了，然后把回复里的说法逐条和回执对。作者自建了一个 1800 个场景、四种语言、六类注入幻觉的测试集，报告的检出率是：编造工具引用 94.2%，数量说错 87.6%，谎称“没查到”91.3%，每条回复的校验开销在 15ms 以内。数字是作者在自己的基准上测的，我没见到第三方复现，看个量级就好。

两篇的共同点很直白：检查得对着工具真实返回的东西做，只盯着回复本身查不出什么。我那两个布尔值，就是把证据压缩成了“有/没有”，信息全丢了。

## 现在的版本：把证据原样递过去

现在的检查器在 [`src/server/functions/classify-hallucination.ts`](https://github.com/annoyc/deepseek-db-chat/blob/bcca41f71a373709120cd225fd72946984417c83/src/server/functions/classify-hallucination.ts#L16)，prompt 开头几句把那条“最高优先级规则”整个反过来了：

```
只有 evidence 中的数据可以作为数值依据；hasToolCalls 和 isContinuation
仅表示流程状态，不证明任何具体数字正确。
```

证据由 [`src/lib/answer-check.ts`](https://github.com/annoyc/deepseek-db-chat/blob/bcca41f71a373709120cd225fd72946984417c83/src/lib/answer-check.ts#L11) 里的 `buildAnswerCheckEvidence` 拼出来：

```typescript
const METADATA_TOOLS = new Set(['list_tables', 'get_database_overview', 'get_table_schema'])

export function buildAnswerCheckEvidence(sqlResults: string | undefined, toolCalls: readonly ToolCallInfo[]): string | undefined {
  const metadata = new Map<string, { tool: string; args: Record<string, unknown>; result: string }>()
  for (const tool of toolCalls) {
    if (!METADATA_TOOLS.has(tool.name) || tool.status !== 'completed' || tool.error || !tool.result?.trim()) continue
    metadata.set(`${tool.name}:${JSON.stringify(tool.args)}`, { tool: tool.name, args: tool.args, result: tool.result })
  }
  if (!sqlResults?.trim() && metadata.size === 0) return undefined
  return JSON.stringify({
    sqlResults: sqlResults || undefined,
    metadata: [...metadata.values()],
  })
}
```

这里有三个决定。

第一，证据分两类。`sqlResults` 是用户确认后真正执行的 SQL 结果，`metadata` 是表清单、表结构这类工具返回。分开是因为它们能证明的东西不一样：有了 `list_tables` 的结果，回答“库里有几张表”就不算编造；但拿着表结构去说“订单表上个月有 3 万条记录”就是编。metadata 这一类是 10 月底才加的。在那之前证据里只有 SQL 结果，用户问“库里有几张表”，模型调 `list_tables` 老老实实答了，在检查器眼里却是“没有执行证据就给了数字”。

第二，只收成功的工具调用。`status !== 'completed'`、有 `error`、结果为空的都跳过。失败的调用不能当证据，这听起来像废话，但第一版的 `hasToolCalls` 恰恰是“调了就算”，不管成没成功。

第三，同一个工具同样的参数只留最后一次。key 是工具名加参数，后面的覆盖前面的，避免表结构被重复塞好几遍。

prompt 里还交代了证据的边界：概览里的 `~N rows` 是近似行数，不能当精确记录数；结果被截断时，不能把样本统计说成全表指标。

## 查不出来，不等于没问题

检查器本身也是一次模型调用，会超时，会输出不合法的 JSON。解析函数是这么写的：

```typescript
/** A failed check is unknown, never evidence that an answer is correct. */
export function parseAnswerCheck(text: string): AnswerCheck {
  try {
    const match = text.match(/\{[\s\S]*\}/)
    const result = match ? JSON.parse(match[0]) : null
    if (typeof result?.hasFakeResult !== 'boolean') return { hasFakeResult: null, status: 'unavailable' }
    return { hasFakeResult: result.hasFakeResult, status: result.hasFakeResult ? 'suspected' : 'not_detected' }
  } catch {
    return { hasFakeResult: null, status: 'unavailable' }
  }
}
```

返回值是三态的：`suspected`、`not_detected`、`unavailable`。以前失败时直接返回 `hasFakeResult: false`，等于把“我没查成”说成了“查过了没问题”。前端写多了会有这个习惯，接口挂了给个默认值让页面别崩。但在这里默认值是有含义的，`false` 就是一个结论。

命名上也改过：`not_detected` 而不叫 `ok` 或 `verified`。检查器没发现问题，只能说明没发现。

## 还没补上的洞

读完调用方 [`useChat.tsx`](https://github.com/annoyc/deepseek-db-chat/blob/bcca41f71a373709120cd225fd72946984417c83/src/hooks/useChat.tsx#L578) 那段，我给自己记了四条。

一，三态只做到了一半。调用方写的是 `if (classification.hasFakeResult)`，`null` 和 `false` 走的是同一条路，界面上也没有任何地方展示“本次未能检查”。类型上区分了，行为上没区分，用户感知不到。

二，SQL 证据只取了最近一次的结果。如果模型在回复里对比“这次”和“上一次”的查询，上一次的数字不在证据里，有可能被误判成编造。我还没遇到真实案例，但从代码上看这条路径是存在的。

三，只纠正一次。`retryCount < 1` 才检查，被打回重答的那条回复不会再过检查器。这是有意的，再查下去就是昨天那篇讲的无上限反馈路径。代价是第二次如果还在编，就直接到用户眼前了。

四，裁判还是模型。我的检查项里其实有一大块是 DEV 那篇说的“确定性能判的”：回复里的数字在不在结果集里。这部分完全可以先用代码过一遍：

```typescript
// 设想中的前置检查，项目里还没有
function findUngroundedNumbers(answer: string, evidence: string): string[] {
  const claimed = answer.match(/\d[\d,]*(?:\.\d+)?/g) ?? []
  const pool = new Set((evidence.match(/\d[\d,]*(?:\.\d+)?/g) ?? []).map(n => n.replace(/,/g, '')))
  return claimed
    .map(n => n.replace(/,/g, ''))
    .filter(n => n.length >= 3 && !pool.has(n))  // 太短的数字误报多，先放过
}
```

它会有误报，比如模型把两行加了个总和，或者把 0.1234 写成 12.34%。所以我的想法是让它只做粗筛：一个超出证据的数字都没有，就不用花钱调检查器；有的话，把这几个数字点名递给检查器，让它只判断这几个能不能从证据里算出来。问题范围越小，裁判越不容易自己编。

## 小结

这几轮改下来，prompt 写得多漂亮其实不太要紧，我学到的主要是一件事：检查器的上限取决于递给它的证据。给它两个布尔值，它只能猜；给它工具的真实返回，它才有东西可对。下一步先把那个数字粗筛加上，再把 `unavailable` 在界面上显示出来。

参考：

- [Hallucination is not a vibe: how to actually detect ungrounded claims in agent output（DEV Community）](https://dev.to/saurav_bhattacharya/hallucination-is-not-a-vibe-how-to-actually-detect-ungrounded-claims-in-agent-output-349l)
- [Tool Receipts, Not Zero-Knowledge Proofs: Practical Hallucination Detection for AI Agents（arXiv:2603.10060）](https://arxiv.org/abs/2603.10060)
- [数问工作台 deepseek-db-chat](https://github.com/annoyc/deepseek-db-chat)
- [src/lib/answer-check.ts](https://github.com/annoyc/deepseek-db-chat/blob/bcca41f71a373709120cd225fd72946984417c83/src/lib/answer-check.ts)
- [src/server/functions/classify-hallucination.ts](https://github.com/annoyc/deepseek-db-chat/blob/bcca41f71a373709120cd225fd72946984417c83/src/server/functions/classify-hallucination.ts)
