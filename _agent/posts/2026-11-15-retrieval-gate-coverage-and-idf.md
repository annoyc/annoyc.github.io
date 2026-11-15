---
title: 检索别凑够 topK 就往里塞：相关性门槛只看覆盖率，把 17.7% 的真问题也拦了
slug: retrieval-gate-coverage-and-idf
date: 2026-11-15 16:00:00
tags: [Agent, RAG, 检索评测, 数问工作台]
excerpt: 问一份上传的简历“其博客链接是什么”，回答却引用了两份电力市场规则，因为“个人”二字撞上了规则里的“任何单位和个人”，而检索默认塞满 6 个槽位。这篇讲数问工作台给 BM25 加相关性门槛时踩的两个坑：为什么不能靠提示词让模型自己忽略噪音，以及只看覆盖率为什么会误杀 17.7% 的口语化问题。
---

前几天给数问工作台加上传文档问答的时候，撞见一个挺荒诞的现场。我传了一份简历上去，问“其博客链接是什么”，回答里规规矩矩标了角标 `[1][2]`，点开一看，引用的居然是内置知识库里的两份电力市场规则。

去翻检索日志，链路是这样的：问题改写那一步补了个检索词“个人博客”，分词切出二元组“个人”，去内置的电力规则库里跑 BM25，正好撞上好多条款里都有的套话“任何单位和个人”。虽然分数很低，但检索器当时的逻辑是按分数排序后直接 `.slice(0, topK)`，6 个证据槽位非塞满不可。简历在浏览器端算出来的切块和这两条电力规则一起进了提示词，模型顺手就都用上了。

## 别指望模型自己忽略凑数的片段

我第一反应是改提示词，加一句“无关的参考资料不要引用，资料不足以回答时直接拒答”。试了几次就会发现，只要无关片段真塞进了上下文，模型很难完全当它不存在。

今年 8 月有篇论文 [Prompt-Based Abstention Fails Under Misleading Context](https://arxiv.org/abs/2608.22228) 专门测过这件事。作者把同一个问题放在四种上下文里测：证据充足、证据残缺、完全没有上下文、塞入误导片段。结果很直接：当上下文干脆为空的时候，模型很听话，基本都能拒答；一旦上下文里混进了似是而非的片段，哪怕提示词里明确写了拒答要求，模型还是在 41.6% 的题目上硬答了，其中 63% 直接照抄了片段里埋的错误实体。Google 去年那篇 [Sufficient Context](https://arxiv.org/abs/2411.06037) 结论也差不多：Gemini 1.5 Pro、GPT-4o、Claude 3.5 这些大模型在上下文充足时表现很好，但拿到不充分的检索结果时，往往倾向于硬编一个答案而不是拒答。

两篇论文指向同一个工程结论：模型最擅长在“手里完全没材料”的时候拒答，不擅长在一堆凑数的材料里挑出“这些其实都不挨着”。

所以在 [`src/server/knowledge/pipeline.ts`](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/server/knowledge/pipeline.ts#L187) 里，我把“没证据就拒答”做成了代码短路，根本不调模型：

```typescript
// 5. Strict mode with nothing retrieved: refuse without spending a model call.
if (!citations.length && !webResults.length && !input.useBaseModel) {
  yield { type: 'text', content: NO_EVIDENCE_ANSWER }
  return
}
```

但这段短路能生效的前提是：面对无关问题，检索器得敢返回空数组，或者只返回真正够格的那一两条，别为了凑够 `topK` 把底下的噪音捞上来。

## 第一版门槛：只看查询词覆盖率

要让 BM25 敢丢掉弱匹配，直觉上是卡一个分数阈值。但 BM25 的原始分没有固定上限，问题长短不同、词频不同，分数差好几倍，很难定一个通用的死数。

我第一版换了个归一化的指标：看文档覆盖了问题里多少权重的词。在 [`src/lib/retrieval/bm25.ts`](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/lib/retrieval/bm25.ts#L75) 里，扫倒排表的时候顺手算两个数，一个是问题所有不重复词的 IDF 总和 `totalIdf`，一个是当前文档命中的那些词的 IDF 总和 `matchedIdf`，相除就是覆盖率 `coverage`。

这里有个关键细节：语料库里根本不存在的词（比如电力规则库里的“博客”），也要算进分母 `totalIdf`。`idf(term)` 在词频为 0 时返回的是最高权重 `Math.log(1 + (N + 0.5) / 0.5)`。如果分母只算库里有的词，那问“个人博客”的时候，“博客”被丢掉，分母只剩“个人”，命中“个人”的条款覆盖率就变成 100% 了。把库外词留在分母里，只撞上“个人”一个常见词时，`coverage` 算出来只有 0.12 左右。

我当时拍脑袋定了个 `minCoverage: 0.2`，低于 0.2 的命中直接过滤掉。简历那个复现用例马上修好了。

## 跑完 89 道评测题，发现误杀了 17.7%

要不是后来补了离线检索评测集（[`evals/knowledge/`](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/evals/knowledge/results/baseline-retrieval.md)，5 份规则共 89 道题，其中 79 道库内题、10 道库外题），我还以为这事就结了。

一跑消融实验，数字很难看。不设门槛（`no-gate`）时，10 道库外题只有 20.0% 能检索为空，另外 80% 都会捞出几条带公共词的规则；加上 `coverage >= 0.2`（`coverage-only`）之后，库外题空检索率确实提到了 90.0%，但库内题的 Hit@5 从 91.1% 掉到了 79.7%，更要命的是“库内空检索率”从 0.0% 飙到了 17.7%。也就是说，有将近六分之一的真问题，直接被那几行短路代码当成“未检索到相关条款”给拒答了。

把被误杀的题捞出来看，几乎全是 `paraphrase`（口语化提问）这一类。

原因想通了其实特简单：用户用大白话提问的时候，句子往往比较长，里面带了一堆规则原文里根本不会出现的日常用词。按我刚才的设计，这些库外词每一个都顶着最高档的 IDF 往分母 `totalIdf` 里加。结果就是，哪怕一条规则实打实命中了问题里的两个核心业务词，只要用户前面铺垫得啰嗦一点，分母一大，`matchedIdf / totalIdf` 的比例就被稀释到 0.2 以下了。

覆盖率是个相对值。问题越短，越容易混过去；问题越长、越口语化，门槛反而越苛刻。

## 加上绝对 IDF 兜底

回头看被拦掉的噪音和被误杀的真问题，它们在绝对量上其实分得很开。

我的默认规则库一共 325 个切块，单个词的最高 IDF 大约是 6.5。那些乱入的噪音，基本都是只撞上了“个人”“电价”“市场”这种在很多条款里都出现过的高频词，单个词的 IDF 普遍在 5.4 以下。而被误杀的口语化真问题，虽然覆盖率不到 0.2，但通常至少命中了两个具体的业务词或二元组，加起来的 `matchedIdf` 都在 6.3 以上。

所以我把 [`src/lib/retrieval/bm25.ts`](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/lib/retrieval/bm25.ts#L28) 里的门槛改成了“或”的关系：

```typescript
export const KEYWORD_RELEVANCE: RelevanceGate = { minCoverage: 0.2, minMatchedIdf: 6 }
```

对应的过滤逻辑在 [`search`](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/lib/retrieval/bm25.ts#L67) 方法里只有几行：

```typescript
    let totalIdf = 0
    for (const term of new Set(queryTokens)) {
      const idf = this.idf(term)
      totalIdf += idf
      const list = this.postings.get(term)
      if (!list) continue
      for (const { doc, tf } of list) {
        if (include && !include(doc)) continue
        const norm = this.k1 * (1 - this.b + this.b * (this.lengths[doc] / (this.averageLength || 1)))
        scores.set(doc, (scores.get(doc) ?? 0) + idf * (tf * (this.k1 + 1)) / (tf + norm))
        matched.set(doc, (matched.get(doc) ?? 0) + idf)
      }
    }
    return [...scores]
      .map(([index, score]) => {
        const matchedIdf = matched.get(index)!
        return { index, score, coverage: totalIdf ? matchedIdf / totalIdf : 0, matchedIdf }
      })
      .filter(hit => hit.coverage >= gate.minCoverage || hit.matchedIdf >= gate.minMatchedIdf)
      .sort((a, b) => b.score - a.score || a.index - b.index)
      .slice(0, limit)
```

短问题靠 `coverage >= 0.2` 放行，长而口语化的问题靠 `matchedIdf >= 6` 兜底。两边都不沾的——既没占到问题多少比重、命中的又只是单个大路货词——才会被丢掉。

我在评测集上把两个参数拉了个网格扫了一遍（见 [`baseline-retrieval.md`](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/evals/knowledge/results/baseline-retrieval.md)）。定在 `0.2 / 6` 这一格时，库外空检索率保持在 90.0%，库内空检索率从 17.7% 压回了 6.3%，Hit@5 从 79.7% 回升到 89.9%，平均每次返回 8.5 个切块而不是硬凑满 10 个。

## 这个数字身上的两个坑

虽然线上现在跑的就是这组参数，但它有两个明显的局限，写代码的时候我在注释里标了。

第一，`minMatchedIdf: 6` 是跟语料库大小绑死的。BM25 的 IDF 公式是 `Math.log(1 + (N - df + 0.5) / (df + 0.5))`，分子里直接带着总切块数 `N`。现在内置库是 325 个切块，最高 IDF 约 6.5，设成 6 刚好意味着“至少得命中两个词，或者一个极其罕见的词”。要是以后往库里塞进几千个切块，`N` 放大十倍，所有词的 IDF 都会整体往上抬 `Math.log(10)`（约 2.3），那时候随便撞上一个稍微少见点的单字词就能过 6。这也是为什么用户临时上传的文档不能直接套这组常数，得在浏览器端对着全部上传切块单独算。

第二，0.2 和 6 是在这 89 道题上看着网格挑出来的。用调参的数据集报出来的 89.9% 和 90.0%，肯定带着乐观偏差。真要验这组门槛稳不稳，还得另外写一批没参与挑数的新题。

## 小结

做 RAG 的时候，“检索不到东西”其实是个好状态，代码直接拦截就能干净利落地拒答；坏状态是检索器为了填满 `topK` 把一两个词碰巧撞上的噪音塞进上下文，把难题甩给后面的生成模型。在检索出口加一道相关性门槛能挡住大部分噪音，但别只用覆盖率这一个相对指标，长问题会吃亏，配一个绝对的信息量下限（比如命中词的 IDF 总和）才能把口语化的真问题保住。

参考：

- [Prompt-Based Abstention Fails Under Misleading Context: A Controlled Study of Small Frozen RAG Models（arXiv:2608.22228）](https://arxiv.org/abs/2608.22228)
- [Sufficient Context: A New Lens on Retrieval Augmented Generation Systems（arXiv:2411.06037）](https://arxiv.org/abs/2411.06037)
- [数问工作台 deepseek-db-chat](https://github.com/annoyc/deepseek-db-chat)
- [src/lib/retrieval/bm25.ts](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/lib/retrieval/bm25.ts)
- [src/server/knowledge/pipeline.ts](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/src/server/knowledge/pipeline.ts)
- [evals/knowledge/results/baseline-retrieval.md](https://github.com/annoyc/deepseek-db-chat/blob/7fc6c9f0bc10feddcf63978e2b89b1be5a83b774/evals/knowledge/results/baseline-retrieval.md)
