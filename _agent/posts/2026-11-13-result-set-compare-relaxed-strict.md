---
title: 比对 SQL 结果集：strict 44%，relaxed 89%，差的那一半全是模型多给的列
slug: result-set-compare-relaxed-strict
date: 2026-11-13 14:00:00
tags: [Agent, 评测, Text-to-SQL, 数问工作台]
excerpt: 同一批运行、同一份结果，要求列数相同时执行准确率 44.4%，允许多出列时 88.9%。这篇讲数问工作台的评测是怎么比两个结果集的：列怎么对齐、值怎么归一，以及放宽之后单值题上开的那个口子。
---

昨天那篇结尾留了个尾巴：查数题的结果集比对，relaxed 和 strict 两个口径差了一倍。今天把它写掉。

先说数。基线是 15 道查数题跑 3 轮，一共 45 次运行。执行准确率 relaxed 口径 88.9%，也就是 40 次通过；strict 口径 44.4%，20 次通过。两个口径用的是同一批 SQL、同一份执行结果，区别只有一条：strict 要求模型返回的列数和标准答案一样多。

我把这 20 次“relaxed 过、strict 不过”的运行一条条翻了，原因全部一样：模型多给了列。

## 模型很爱多给列

问“评分低于 3 分的评价都是针对哪些商品的？列出商品名称和评分”，标准 SQL 是：

```sql
SELECT p.name, r.rating FROM product_reviews r
JOIN products p ON p.id = r.product_id WHERE r.rating < 3
```

模型三轮写的都是：

```sql
SELECT p.name AS 商品名称, r.rating AS 评分, r.created_at AS 评价时间
FROM product_reviews r JOIN products p ON r.product_id = p.id
WHERE r.rating < 3 ORDER BY r.rating ASC, p.name ASC
```

多了一列评价时间，还顺手排了序。问各订单状态有多少笔，它会再加一列占比。问没库存的商品，它会带上上架状态。从产品角度看，这些多出来的列大多是加分项，用户看到结果表多半觉得挺贴心。但要是按“结果集完全相等”打分，这些全算错。

这个问题在 Text-to-SQL 评测里是老问题了。执行准确率（EX）就是把预测 SQL 和标准 SQL 各跑一遍比结果，[FLEX 那篇论文](https://arxiv.org/abs/2409.19014)专门挑过它的毛病。他们举的假阴性例子里有一条特别冤：预测 SQL 被判错，只因为 `lname` 排在了 `fname` 前面。他们请专家人工标注后算一致性，EX 和专家的 Cohen's kappa 是 62.00，他们用模型当裁判的 FLEX 是 87.04。

我没走模型裁判那条路。昨天说过，我这套评分器从头到尾不调模型，我想先看看纯代码能把比对做到什么程度。

## 列怎么对齐

列名不能用。模型起的别名是“商品名称”，标准 SQL 里是 `name`，没法按名字配。列序也不能用，上面 FLEX 那个例子就是教训。

我的做法是按值配。先把结果转成按列存，每个格子归一成字符串：

```typescript
function columns(rows: Row[]): string[][] {
  if (!rows.length) return []
  const keys = Object.keys(rows[0])
  return keys.map(key => rows.map(row => normalizeCell(row[key])))
}

function multisetKey(values: string[]): string {
  return JSON.stringify([...values].sort())
}
```

`multisetKey` 是一列的“指纹”：把这列所有值排序后序列化。两列指纹相同，说明它们装的是同一堆值，只是行序可能不同。

然后给标准答案的每一列，在预测结果里找一个指纹相同、且没被占用的列。这是个回溯搜索，在 [`evals/scorer.ts`](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/evals/scorer.ts#L90)：

```typescript
function alignColumns(gold: string[][], pred: string[][]): string[][][] {
  const results: string[][][] = []
  const used = new Set<number>()
  const chosen: string[][] = []
  const search = (gi: number) => {
    if (results.length >= 32) return
    if (gi === gold.length) { results.push([...chosen]); return }
    const target = multisetKey(gold[gi])
    pred.forEach((col, pi) => {
      if (used.has(pi) || multisetKey(col) !== target) return
      used.add(pi); chosen.push(col)
      search(gi + 1)
      used.delete(pi); chosen.pop()
    })
  }
  search(0)
  return results
}
```

它返回的是所有可行的配法（最多 32 种），每种配法是从预测结果里挑出来、按标准答案列序排好的几列。

光指纹相同还不够。每列各自对上，不代表整张表对上，反例很好造：

```typescript
const gold    = [{ city: '上海', n: 9 }, { city: '北京', n: 8 }]
const swapped = [{ city: '上海', n: 8 }, { city: '北京', n: 9 }]
```

两列的指纹都对得上，但上海和北京的数字反了，这是个错答案。所以配好列之后还要把行拼回去，整行整行地比：

```typescript
export function compareResults(goldRows: Row[], predRows: Row[], orderMatters = false) {
  if (goldRows.length !== predRows.length) return { strict: false, relaxed: false }
  if (!goldRows.length) return { strict: true, relaxed: true }
  const gold = columns(goldRows)
  const pred = columns(predRows)
  const candidates = alignColumns(gold, pred)
  const relaxed = candidates.some(projected => tablesEqual(gold, projected, orderMatters))
  return { strict: relaxed && gold.length === pred.length, relaxed }
}
```

（省略了返回类型标注。）`tablesEqual` 把每行序列化成一个字符串，默认按多重集比（不看行序，但重复行的数量要一样），题目明确要求排序时才逐行比。20 道题里只有“价格最高的 3 个商品，按价格从高到低”这一道开了 `order_matters`。模型自己加的 `ORDER BY` 不扣分。

所以 relaxed 的意思是：预测结果里能挑出几列，和标准答案是同一张表。strict 再加一条，挑完不能有剩的。

## 值怎么归一

指纹能不能对上，全看 `normalizeCell`。规则不多，每一条都对着一类具体的题。

数字统一四舍五入到 2 位小数。标准 SQL 写 `AVG(r.rating)`，出来是 `4.3333`；基线里凡是求平均的题，模型写的都是 `ROUND(AVG(...), 2)`。不归一的话，这些题全挂。驱动那边我开了 `bigNumberStrings`，`DECIMAL` 回来是字符串 `'120.00'`，所以长得像数字的字符串也要走同一条路，变成 `'120'`。

日期只做了一件事：`2026-09-01 00:00:00` 和 `2026-09-01` 视为相同。`NULL` 映射成一个固定占位符，不然 `String(null)` 会和文本 `'null'` 撞上。

有一类差异归一救不了，只能在题目上解决。问“各个订单状态分别有多少笔”，返回状态码 `10` 和返回字典标签“待支付”都算对，但这两个值没法靠归一变成相等。所以 `gold_sql` 可以是数组，命中任意一条就过：

```json
"gold_sql": [
  "SELECT order_status, COUNT(*) FROM orders GROUP BY order_status",
  "SELECT d.dict_label, COUNT(*) FROM orders o JOIN sys_dict_data d ON ... GROUP BY d.dict_label"
]
```

有意思的是模型三轮都把状态码和中文标签两列一起返回了，再加订单数和占比，一共四列。relaxed 下它配上了第一条标准 SQL。

## 放宽之后开的口子

relaxed 把假阴性压下去了，代价是假阳性的门开大了。FLEX 论文里的假阳性例子是 SQL 多写了个 `age < 19` 的条件，刚好库里没有超过 18 岁的学生，结果一样，判对。我这边有同款，而且 relaxed 把它放大了。

第一个口子在单值题上。“北京的客户一共下了多少笔订单？”，标准答案是一行一列，值是 21。模型有一轮返回的是：

```sql
SELECT COUNT(o.id) AS 订单总数,
       COUNT(DISTINCT o.customer_id) AS 下单客户数,
       COUNT(DISTINCT c.id) AS 北京客户数
FROM customers c LEFT JOIN orders o ON o.customer_id = c.id
WHERE c.city = '北京'
```

relaxed 的规则落到这道题上，就变成了“三列里有任意一列等于 21 就算对”。我拿比对函数试了一下：

```typescript
compareResults([{ n: 21 }], [{ a: 30, b: 21 }])
// { strict: false, relaxed: true }
```

第一列是错的，第二列碰巧是 21，照样通过。评分器不知道模型认为哪一列才是答案。模型返回的列越多，蒙中的机会越大。基线里这样“单值题、多列返回”的通过有 5 次，我逐条读了 SQL，对上的那一列确实都是正确的算法，但这是我人读出来的，分数本身保证不了。

第二个口子是数据太干净。还是这道题，有一轮模型写的是 `c.city LIKE '北京%'`；上海那道题更过分，写了 `LIKE '%上海%'`。演示库里城市字段都是规整的“北京”“上海”，所以结果一样。换个有“上海路街道”的真实库就不一样了。这类题我在数据集的 `notes` 里标了“单值答案，偶然命中风险较高”，目前也只是标了一下。

第三个是小数归一。两位小数对金额、评分够用，但 `0.004` 和 `0.001` 归一后都是 `0`，会被判成相等。现在的题里没有比率类的小数，以后加转化率之类的题，这条规则得改成按相对误差比。

还有一个方向相反的。“哪个商品分类的平均评分最高？”，标准 SQL 带 `LIMIT 1`，返回一行。模型有两轮没加 `LIMIT`，把 6 个分类按评分倒序全列出来了。比对函数第一行就是行数检查，直接不通过。这算不算错可以争。第一行确实是正确答案，但用户问的是“哪个”，给一张 6 行的表让人自己看第一行，我倾向于算没答到点上，就没为它放宽。

另外有一条是靠流程兜的：两边都是空结果时，`compareResults` 返回通过。这意味着标准 SQL 要是查不出数据，任何同样查不出数据的错 SQL 都能过。所以 runner 有个 `--check-gold` 模式，不调模型，只把所有标准 SQL 跑一遍，结果为空就报错。加新题后先跑这一步。

## 两个数都报

最后报告里两个口径都留着。只报 strict，44.4% 这个数会让人以为模型一半的题不会做，实际上它交了 SQL 的 42 次里只有 2 次没过，就是上面没加 `LIMIT` 的那两次。只报 relaxed，又看不出“模型有一半的时候在多给列”这件事，而这件事对产品是有信息量的：多出来的列会原样出现在用户看到的结果表里。

9 月有篇论文 [The Stochastic Shift](https://arxiv.org/abs/2609.21133) 讲 SQL 里带 AI 算子（结果不确定）时怎么评测，摘要里说传统 EX 对正确翻译的识别率最低只有 25%。场景和我不一样，但说明的是同一件事：EX 报出来的数，很大一部分取决于“相等”是怎么定义的。看别人报的执行准确率时，我现在会先去找他们的比对规则。

## 小结

比结果集这件事，代码量不大，一百行以内。花时间的地方是决定哪些差异不算差异：列名、列序、行序、多出的列、小数位、日期写法，每放宽一条都会少冤枉一些对的，也会多放过一些错的。我现在的选择是放宽到 relaxed 为主，strict 一起报，单值题的口子先靠人看。要堵的话，一个办法是给单值题多准备一两份数据不同的库，让碰巧相等没那么容易。

参考：

- [FLEX: Expert-level False-Less EXecution Metric for Reliable Text-to-SQL Benchmark（arXiv 2409.19014）](https://arxiv.org/abs/2409.19014)
- [The Stochastic Shift: A New Evaluation Paradigm for Text-to-SQL with AI Operators（arXiv 2609.21133）](https://arxiv.org/abs/2609.21133)
- [数问工作台 deepseek-db-chat](https://github.com/annoyc/deepseek-db-chat)
- [evals/scorer.ts](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/evals/scorer.ts)
- [evals/runner.ts](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/evals/runner.ts)
- [evals/dataset/questions.jsonl](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/evals/dataset/questions.jsonl)
- [evals/results/baseline-deepseek-flash.json](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/evals/results/baseline-deepseek-flash.json)
