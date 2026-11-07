---
title: 工具太多、表太多，别全塞给模型：先缩小候选，但要留一条退路
slug: candidate-narrowing-keep-escape-hatch
date: 2026-11-07 14:00:00
tags: [Agent, 工具选择, Schema 裁剪, 数问工作台]
excerpt: 工具有几千个，或者数据库有几百张表，全部放进 prompt 既贵又容易选错。10 月中旬的一篇论文讲了工具这边怎么做，我自己的数据库 Agent 里正好有表这边的同款做法，一个很土的关键词打分，外加一条不能省的退路。
---

这篇起因是看论文时撞上的，看完发现自己的项目里正好有个土味版本。

先说一个场景。你给 Agent 接了几个 MCP server，每个 server 往外暴露十来个工具。接到第五个的时候，工具定义本身就占了好几万 token，用户还没开口。我在前端做过类似的事：一个页面一次性把全站所有组件的配置都加载进来，只因为“万一用到呢”。

## 论文怎么说

10 月 16 日有篇 arXiv 论文，[Tool Recommendation for Multi-Step Agents: Finding Every Tool a Task Needs](https://arxiv.org/abs/2610.23707)（arXiv:2610.23707）。它讲的是一个已经上线的网关：背后大约 8200 个工具，定义加起来比测试用的模型的上下文窗口还长，没法列给 Agent 看。

做法是只暴露三个固定的工具给 Agent：SelectTools 接收自然语言的任务描述，返回排名前五的工具定义（带完整参数 schema）；UseTool 去执行选出来的那个；ListApps 告诉 Agent 网关后面有哪些应用、用户连没连上。也就是说，Agent 不是“看到所有工具再挑”，而是“先描述我要干什么，再拿到几个候选”。

几个数字，都是论文摘要和正文里写的：在公开检索基准 ToolRet 上，让所需工具全部进前十的比例比 BM25 高 57%；在多步骤的编程 Agent 会话里，和固定的 100 个工具列表比，输入 token 在 Codex 下少 17%，在 Claude Code 下少 40%；14 次运行总费用都降了，最多降 40%，任务完成情况没变。这是他们自己的部署和测试，我没法复现，数字只当参考。

我觉得更有价值的是它没有一味说检索好。论文里明确写了：工具少、任务短的时候，静态列表更合适。成本模型算出来，工具数低于大约 25 到 28 个，或者每次工具调用平均搜索次数超过约 0.63 次，静态列表更划算。在 8 到 37 个工具的短查询里，用检索反而让输入中位数涨了 22%，因为每次搜索都要多花一轮。

这和前端的直觉一致：数据少的时候别上虚拟列表。

另外一篇 9 月的 [Toollery](https://arxiv.org/abs/2609.22218)（arXiv:2609.22218，EMNLP 2026）思路类似：给每个工具预先生成一批“用户可能怎么说”的查询，建索引，线上先把候选压到 top-K 再让模型做最后的选择。它的摘要里自己也写了，收益取决于负载覆盖面和服务商的缓存情况。这个“缓存”我后面会回到。

## 我项目里的同款：选表

[数问工作台](https://github.com/annoyc/deepseek-db-chat)是我的自然语言查数据库项目。数据库的“工具”不是函数，而是表：一个库有两三百张表，模型要写对 SQL，就得知道有哪些表。如果把整个库的结构都塞进去，和塞几千个工具定义是一个问题。

我写了个模块 `src/server/schema-subsetting.ts`，数据库表数量不超过 40 张就原样全给；超过了才裁。入口是这样的：

```typescript
const MAX_OVERVIEW_TABLES = 40
// ...
export function subsetTables(
  tables: TableMeta[],
  userQuery: string,
  fkEdges: ForeignKeyEdge[],
  seedTables?: string[],
): SubsetResult {
  if (tables.length <= MAX_OVERVIEW_TABLES) {
    return { tables, hiddenCount: 0, totalCount: tables.length }
  }
  // ...
}
```

[源码](https://github.com/annoyc/deepseek-db-chat/blob/d7a9caf477daa1056e32df8763542774c91abcae/src/server/schema-subsetting.ts#L10-L82)。第一个 `if` 就是论文说的那条：小库不裁。

打分特别朴素，就是用户问题的分词和表名、表注释的分词做重叠：

```typescript
function scoreTable(table: TableMeta, queryTokens: string[]): number {
  if (queryTokens.length === 0) return 0

  const nameTokens = tokenize(table.name)
  const commentTokens = tokenize(table.comment)
  const allTableTokens = new Set([...nameTokens, ...commentTokens])

  let matchCount = 0
  for (const qt of queryTokens) {
    for (const tt of allTableTokens) {
      if (tt.includes(qt) || qt.includes(tt)) {
        matchCount++
        break
      }
    }
  }

  return matchCount / queryTokens.length
}
```

[源码](https://github.com/annoyc/deepseek-db-chat/blob/d7a9caf477daa1056e32df8763542774c91abcae/src/server/schema-subsetting.ts#L50-L68)。分词也是土办法：英文按下划线、驼峰切开，中文直接一个字一个字切。没有向量，没有 embedding，没有 BM25。所以它的准确率肯定比不上论文里那种系统，我也没测过一个具体数字，这里不编。

这种做法的好处很实际：表名和表注释通常就是用户会用的词，比如“订单”“用户”；而且它不需要额外的模型调用，不增加延迟。

## 只打分不够，补了三件事

关键词重叠有明显的漏洞：用户说“上个月卖了多少”，表叫 `t_biz_order_fact`，没有一个字对得上。所以打分之外还有三层兜底，都在同一个函数里：

1. 种子表。前面有一个意图分类器（[`intent-router.ts`](https://github.com/annoyc/deepseek-db-chat/blob/d7a9caf477daa1056e32df8763542774c91abcae/src/server/intent-router.ts)），它能根据问题猜出“可能涉及哪些表”，这些表不管打分多少都强制放进去（代码里的 Phase 0）。这相当于让模型来做第一轮召回，关键词打分只是补充。
2. 外键扩展。选中的表，沿着外键往外走一跳，把直接 JOIN 的表也带上。预算上留了一部分给这个，大概四分之一。没有这条的话，会出现只选中了 `orders`、却缺它要 JOIN 的用户表的情况，模型拿不到那张表，就只能猜列名。
3. 补位。名额还有剩的话，按行数从多到少补，我的假设是行数多的表更可能是核心业务表。

三件事的顺序和细节我就不全贴了，想看直接翻源码。

## 退路比打分本身更重要

说到这，其实最重要的不是上面这些，而是下面这一小段。在 `src/server/database.ts` 里，拼完概览之后：

```typescript
if (hiddenCount > 0) {
  overview += `\n注意：由于表数量较多，以上仅展示与问题可能相关的 ${visibleTables.length} 张表。如果所需的表未在上方列出，请调用 list_tables 查看完整列表，再对具体表调用 get_table_schema。`
}
```

[源码](https://github.com/annoyc/deepseek-db-chat/blob/d7a9caf477daa1056e32df8763542774c91abcae/src/server/database.ts#L709-L711)。同一文件里，概览开头还会写一句“共 N 张基础表……另有 M 张可通过 list_tables 查看完整列表”。

也就是说，裁剪是有损的，而我让这种损失对模型是可见的：它知道自己看到的是子集，知道总数，也知道怎么看全部。打分漏掉了 `t_biz_order_fact`，模型还有机会自己去调 `list_tables` 找。

这和论文里的设计思路接近：候选只是入口，Agent 手里始终有能继续查找的工具（ListApps 之类）。如果裁剪之后模型根本不知道有东西被藏起来了，它就只会在残缺的信息里硬凑，这比一开始给全量还糟。

最坏的情形我能想象得到：只返回裁剪后的表、不说还有多少没展示，模型遇到被漏掉的表，就会很笃定地说“库里没有这个表”。

## 缓存也要算进去

Toollery 的摘要里提了缓存，我之前没想过这一点，是写这篇时才回头看自己的代码。我用的 DeepSeek 有前缀缓存，消息的前面一段完全一样就能便宜地复用。如果每个问题的表概览都不同，那这一段就没法复用了。

我现在的处理是：概览在一次对话里只算一次，工具里有个 `overviewCache`，同一轮里重复调用直接返回缓存的字符串。但跨问题它还是会变，因为裁剪依赖用户当前的问题。这笔账我没认真算过，省下的 token 和丢掉的缓存命中到底谁多，我不确定。小库不裁的那个 40 张的阈值，也是拍脑袋定的。

## 小结

候选太多，就先缩小再让模型选，缩小的办法可以很粗糙，粗糙到关键词匹配也行。但有三条我觉得不能省：小规模时别裁；裁剪的结果要让模型知道“这是子集”；给它一条能看全部的路。

参考：

- [Tool Recommendation for Multi-Step Agents: Finding Every Tool a Task Needs（arXiv:2610.23707）](https://arxiv.org/abs/2610.23707)
- [Toollery: Scaling LLM Agents to Thousands of Skills and Tools（arXiv:2609.22218）](https://arxiv.org/abs/2609.22218)
- [数问工作台 deepseek-db-chat](https://github.com/annoyc/deepseek-db-chat)
- [src/server/schema-subsetting.ts](https://github.com/annoyc/deepseek-db-chat/blob/d7a9caf477daa1056e32df8763542774c91abcae/src/server/schema-subsetting.ts)
