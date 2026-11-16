---
title: 意图路由别只给模型打个标签：简单问题要短路，模糊问题缺的是参数
slug: intent-router-needs-filter
date: 2026-11-17 16:00:00
tags: [Agent, 意图路由, 交互设计, 数问工作台]
excerpt: 做自然语言查数据库时，把问题直连 Agent 循环会遇到两头堵：遇到“库里有哪些表”容易过度行动去写 SQL，遇到“看下最近订单”模型又自作主张编造时间范围。这篇讲为什么意图路由不能只输出分类枚举，以及我在数问工作台里怎么用正则短路、连续置信度门禁和软提示注入来控制 Agent 的行为边界。
---

做前端的时候，处理路由守卫和表单校验是肌肉记忆：路由跳转前要检查权限，表单提交前要拿 Schema 校验每个字段是否合法。

刚转来做 Agent 开发的时候，官方教程里讲的 Agent 循环（一个 `while` 循环加上一堆工具声明）让我产生了一种错觉：既然大模型拥有通用推理能力，前端只要提供一个自由文本输入框，后端把用户的 query 往循环里一塞，模型自己挑工具、一步步跑完不就得了？

把数问工作台的第一版接上真实数据库跑了几天之后，这个天真的直觉很快撞墙了。最折磨人的不是复杂的连表查询写不对，而是两类完全相反的极端场景把交互堵死了。

一头是极度简单的问题。用户问：“当前库里一共有多少张表？”。大模型在循环里转了两秒，先调 `get_database_overview` 拉了一大坨元数据，接着不知道哪根筋搭错了，竟然洋洋洒洒写了一句 `SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = ...`，然后走到了我们昨天讲的挂起状态，在前端弹出一个 SQL 确认窗口。用户只想知道有几张表，被弹窗搞得莫名其妙。原本调一次数据库驱动 10 毫秒就能返回的结果，绕了三四秒，花了上千个 token。

另一头是极度模糊的问题。用户输入：“看看最近的订单趋势”。这句话既没有说明“最近”是最近 7 天、30 天还是今年，也没有指定是按天汇总还是按月汇总。模型不但没有停下来询问，反而格外积极地自作主张，自己假定了一个时间范围，拼出一段带 `GROUP BY` 的复杂 SQL 提交给用户。用户点开一看就愣住了：“我这个测试库上周才建的，你从哪查出来的 2024 年数据？”

这两类情况表现虽然不同，根源却是一样的：把用户的自由文本直接丢进 Agent 循环，缺乏对输入意图的分流，更缺乏对参数完备性的前置检查。

## 为什么只输出分类枚举救不了模糊查询

发现问题后，很自然的改造思路是加一层意图路由（Router），像前端的路由守卫（Route Guard）一样，在请求进入主循环前先做一次分流。

很多教程给出的意图分类示例都很简洁，大意是先调一次轻量级模型，返回一个结构化 JSON，比如：

```typescript
type QueryIntent = 'explore_schema' | 'simple_query' | 'complex_query' | 'write_operation' | 'analysis'
```

我也照着这个思路写过一版。跑了两天我就发现，光拿一个分类枚举，下游代码根本没办法做任何严谨的控制。

问题出在：分类标签只能描述“这是一类什么操作”，却无法回答“执行这个操作所需的信息齐不齐全”。

比如同样被分类为 `simple_query`：

- “查询客户表中张三的电话”：单表查询，参数完整，模型拿到表结构就能一步写出准确的 SQL。
- “查询最近几个大额客户的电话”：也是单表查询，但关键参数全部缺失。“最近”是多久？“大额”的金额阈值是多少？“几个”是 5 个还是 10 个？

如果分类器只输出 `{ intent: 'simple_query' }`，下游循环根本无法判断该放行还是该拦截。要是直接把 query 交给模型，模型为了完成任务，只能靠“幻觉”来硬猜参数。

在前端开发里，这就像表单验证：不仅要知道用户点的是“保存”按钮，更要在发送请求前检查每个必填字段是否合法。自然语言虽然没有表单组件，但这层“参数完整性校验”不能缺席。

## 连续置信度与硬门禁：把参数补齐交给前端控件

要让下游代码能够做拦截决策，分类器就必须输出参数维度的信号。

我在 [`src/server/intent-router.ts`](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/server/intent-router.ts#L31-L44) 里重新定义了意图分类的数据协议：

```typescript
const IntentSchema = z.object({
  intent: z.enum([
    'explore_schema',
    'simple_query',
    'complex_query',
    'write_operation',
    'analysis',
  ]),
  confidence: z.number().min(0).max(1),
  needsFilter: z.number().min(0).max(1),
  needsPlanning: z.boolean(),
  reasoning: z.string(),
  suggestedTables: z.array(z.string()).optional().default([]),
})
```

这里最关键的改动是引入了 `needsFilter`，并且它不是一个布尔值，而是一个从 0 到 1 的连续浮点数：

- `0`：所有必要参数已经完全明确（例如“今天新增用户数”、“最近7天每天订单量”）。
- `0.3 ~ 0.5`：大体明确，但包含一两个可微调项或可以接受默认值的参数。
- `0.6 ~ 0.8`：关键参数缺失，出现了“趋势/统计/分析”等聚合词汇，但没有指定时间范围或聚合粒度。
- `0.9 ~ 1.0`：极度模糊（例如“订单数据分析”、“看看情况”）。

自然语言的模糊程度是一个渐进的光谱，把它建模成浮点数，下游代码才能灵活地划定门槛。在模块里我定义了一个阈值：

```typescript
export const FILTER_CONFIDENCE_THRESHOLD = 0.5
```

当分类结果打出 `needsFilter >= 0.5` 时，系统就会触发硬门禁。

最开始触发门禁时，我尝试过让模型直接用自然语言反问用户：“请问您需要看哪个时间段的数据？按天还是按月汇总？”

实际测试下来，纯文本反问的交互体验非常差：用户回了一句“看上周的”，模型又追问“上周按周一到周日算吗”，反复拉扯几个回合，首屏响应慢得令人烦躁，而且多轮对话把上下文堆得又长又乱。

最后我改成了结合前端交互控件的方式。门禁触发时，在给 Agent 的系统提示词里追加强约束（见 [`src/server/agent.ts`](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/server/agent.ts#L330)）：

```typescript
if (intent.needsFilter >= FILTER_CONFIDENCE_THRESHOLD) {
  systemPrompt += '\n提示: 获取表结构后请先用 smart_filter 确认查询参数。'
}
```

Agent 读取表结构后，一旦发现涉及日期或状态字段，就必须调用专门的交互工具 `smart_filter`。这个工具不会去查数据库，而是直接返回挂起状态（`suspend`），前端捕获事件后，就地弹出一个预设好的筛选卡片（`SmartFilterConfirmBlock.tsx`），里面列出推导出的时间范围选择器和维度单选框。

用户在界面上动动手指点选“最近 30 天，按日汇总”，点击确认，前端把选好的结构化参数作为工具结果恢复（`resume`）回 Agent 循环。大模型拿到明确的筛选条件，直接一次性生成正确的 SQL。用前端熟悉的受控组件去解决模糊输入，比在对话框里用文本相互猜谜可靠得多。

## 简单问题必须短路，但正则锚定要极度克制

模糊问题有了门禁兜底，接下来看简单问题。

既然要在每次请求前做意图分类，就意味着哪怕用户只发了一句话，在进入主 Agent 之前也必须先调一次轻量级模型。虽然我们关掉了思考模式（`thinking: { type: 'disabled' }`），温度设为 0，这趟网络往返在正常情况下依然需要 300 到 800 毫秒。

当用户问“有哪些表”或者“这个库有几张表”时，服务端本可以用连接池以几毫秒的速度直接从元数据缓存里返回结果。为了做意图分析，反而多耗了将近一秒，这显然是倒退。

对于这种高频、模式高度固定的元数据问题，最有效的办法是在调模型前做一层确定性的正则短路。

在 [`src/server/intent-router.ts`](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/server/intent-router.ts#L104-L126) 里，我写了一套严格锚定的正则：

```typescript
const CHINESE_INVENTORY_QUERY = new RegExp(
  '^(?:请)?(?:帮我)?(?:查询|查看|看看|列出|列举|显示|统计|获取)?'
  + '(?:(?:当前|这个|该|本)?(?:数据库|库)(?:中|里|内)?(?:的)?)?'
  + '(?:'
  + '(?:总共|一共|总计|共)?(?:有)?(?:多少|几)(?:张|个)?(?:基础表|数据表|表|视图)'
  + '|(?:总共|一共|总计|共)?(?:有)?哪些(?:基础表|数据表|表|视图)'
  + '|(?:所有|全部|完整)?(?:基础表|数据表|表|视图)(?:清单|列表)'
  + '|(?:基础表|数据表|表|视图)(?:的)?(?:数量|总数|个数|数目)(?:是多少|有多少)?'
  + '|(?:所有|全部)(?:基础表|数据表|表|视图)'
  + ')(?:呢|吗)?$',
)

function isTableInventoryQuery(query: string): boolean {
  const normalized = query.replace(/[?？。.!！]+$/u, '').trim()
  return CHINESE_INVENTORY_QUERY.test(normalized.replace(/\s+/g, ''))
    || ENGLISH_INVENTORY_QUERIES.some(pattern => pattern.test(normalized))
}
```

如果用户输入命中了这个正则，函数直接返回构造好的分类对象，置信度直接给 1，完全不调大模型：

```typescript
if (isTableInventoryQuery(q)) {
  return {
    intent: 'explore_schema',
    confidence: 1,
    needsFilter: 0,
    needsPlanning: false,
    reasoning: '表数量或完整清单可直接从数据库元数据获取',
    routingHint: ROUTING_HINTS.explore_schema,
    skipOverview: false,
    suggestedTables: [],
  }
}
```

这里我踩过一个非常严重的坑。

刚开始写正则的时候，我想着要尽可能覆盖用户的各种问法，条件写得比较宽松，把句子中只要出现“多少”和“表”的情况都匹配进来了。

结果在一次回归评测中翻了车：测试集里有一道题是“users 表有多少条记录”，竟然命中了这条短路规则，直接被标成了 `explore_schema`。

为什么会酿成大错？因为在 MySQL 中，数据库概览能够拿到每张表的近似行数 `table_rows`。但凡维护过 InnoDB 引擎的人都知道，`information_schema.tables` 里的 `table_rows` 只是统计抽样估算，在有并发写入的库里，这个数字和真实行数相差百分之三四十是常有的事。

Agent 拿到了短路给的元数据提示，以为这是个元数据问题，直接把元数据里的 `table_rows` 摘出来告诉用户：“users 表共有 98,324 条记录”。实际上真实执行 `SELECT COUNT(*)` 查出来的是 102,150 条。用户问的是业务真实数据，系统却自作聪明地用估算值糊弄了过去。

这个教训让我对短路规则做了严格的边界限定：

第一，正则必须在首尾完整锚定（`^...$`），只匹配询问“全库表清单、全库表总数”的孤立问句。一旦句子中出现了具体的表名、字段名或业务动词，坚决不走短路，老老实实交给模型分类。

第二，在分类器的 prompt 和下游 Agent 的核心规则里明确写死红线：

> 表数量与表内记录数不同：“一共有多少张表”是元数据查询；“users 表有多少条记录”需要真实数据查询，不能拿元数据中的近似行数代替 COUNT(*)。

## 动态裁剪与软提示注入：给模型留一条退路

分类拿到了结果，下游的主 Agent 应该怎么消费它？

很多工程师的第一直觉是做“硬分支执行”，写出类似这样的代码：

```typescript
// 容易踩坑的硬分支写法
if (classification.intent === 'explore_schema') {
  return handleDirectSchema(userQuery)
} else {
  return runAgentLoop(userQuery)
}
```

在实际业务里，这种非黑即白的硬隔离很容易出问题。用户的表达往往是复合的，比如“帮我看下一共有几张表，顺便查一下订单表最新的 5 条记录”。如果被强行扔进纯元数据处理分支，后半句需求就直接被吞掉了。

在数问工作台里，我们对路由结果的处理分成两部分：一部分做系统提示词的动态裁剪，另一部分做软提示注入。

### 1. 动态裁剪 Few-Shot 示例

在 [`src/server/agent.ts`](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/server/agent.ts#L300-L308) 中，有这样一段逻辑：

```typescript
// Few-shot examples: skip for schema exploration and analysis (saves ~2000 tokens)
if (intentType !== 'explore_schema' && intentType !== 'analysis') {
  promptSections.push(SYSTEM_PROMPT_FEW_SHOT)
}

// Analysis format guide: only when there's execution history or complex queries
if (intentType === 'analysis' || intentType === 'complex_query' || intentType === 'simple_query' || hasHistory) {
  promptSections.push(SYSTEM_PROMPT_ANALYSIS)
}
```

为了让模型学会正确的连表逻辑，我们的 System Prompt 里放了几个典型的复杂 SQL Few-Shot 范例。这部分内容大约占了 2000 个 token。

如果不加区分地把这些范例塞给所有请求，大模型天然的“模式模仿”倾向就会起作用。当用户问“有哪些表”时，模型看到提示词里全是长篇大论的 SQL 示例，就会潜意识里觉得“我必须写一条 SQL 才能交差”，于是倾向于去拼一段查询系统表的复杂 SQL。

把意图判断为 `explore_schema` 时，我们直接把这 2000 token 的示例切掉。提示词里没有 SQL 范例的诱导，模型就会老老实实地调用 `list_tables` 等轻量工具。既降低了首字延迟和 token 消耗，又从源头抑制了模型的过度行动。

### 2. 软提示注入（Soft Routing Hint）

对于具体的行为引导，路由模块并不强行剥离 Agent 的工具，而是把建议以提示的形式拼入 Prompt 底层（见 [`src/server/intent-router.ts`](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/server/intent-router.ts#L211-L225)）：

```typescript
export function buildRoutingPrompt(classification: IntentClassification): string {
  const lines: string[] = [
    `\n【路由】${classification.routingHint}`,
  ]

  if (classification.suggestedTables && classification.suggestedTables.length > 0) {
    lines.push(`可能涉及: ${classification.suggestedTables.join(', ')}`)
  }

  if (classification.skipOverview) {
    lines.push('可跳过 get_database_overview。')
  }

  return lines.join('\n')
}
```

这些提示会附在 System Prompt 的动态上下文部分。它明确告诉模型：“建议优先使用元数据工具”，“可能涉及某几张表”，“追问场景下可跳过全局概览”。

但请注意：所有工具依然完整注册在模型手里。如果用户的真实问题确实需要兼顾多步操作，模型依然有能力随时调用其他工具。这也是 11 月 7 日那篇讲 Schema 裁剪时强调过的原则：先缩小候选，但必须留一条退路。

## 小结

从前端转做 Agent，很容易把自然语言交互想象得过于简单，以为只要把输入塞进 `while` 循环，大模型就能自行处理所有细节。

真实工程落地时，意图路由扮演的恰恰是前端体系中“路由守卫”与“受控表单校验”的复合角色：

- 对极高频、确定的简单问句，用严格的正则短路放行，保证零延迟与零 token 消耗；
- 对语义模糊、参数缺失的问题，用连续置信度在门禁处拦下，借助前端交互控件将参数补齐后再交给模型；
- 对进入循环的模型，通过动态裁剪上下文消除诱导，用软提示指明路径，同时保留完整的工具退路。

别寄希望于一个简单的分类枚举能解决所有分流问题。看清下游环节真正需要什么控制信号，Agent 才能在敏捷与安全之间找到平衡。

参考：

- [Building effective agents（Anthropic Engineering，2024-12）](https://www.anthropic.com/engineering/building-effective-agents)
- [Structured Outputs（OpenAI API Docs）](https://platform.openai.com/docs/guides/structured-outputs)
- [数问工作台 deepseek-db-chat](https://github.com/annoyc/deepseek-db-chat)
- [src/server/intent-router.ts](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/server/intent-router.ts#L31-L44)
- [src/server/agent.ts](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/server/agent.ts#L300-L332)
- [src/server/functions/chat.ts](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/server/functions/chat.ts#L56-L67)
- [src/components/chat/SmartFilterConfirmBlock.tsx](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/components/chat/SmartFilterConfirmBlock.tsx)
