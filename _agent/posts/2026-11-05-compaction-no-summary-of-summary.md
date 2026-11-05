---
title: 上下文快满了怎么压：别压一份已经压过的东西
slug: compaction-no-summary-of-summary
date: 2026-11-05 14:00:00
tags: [Agent, Context Engineering, 上下文压缩]
excerpt: 上一篇留了个尾巴，说上下文管理单独写一篇。这篇只讲其中一个具体问题：上下文到阈值了要压缩，最常见的做法是让模型写摘要，但摘要滚几轮就会失真。CMU 最近一篇论文的做法很朴素：只截断不改写，而且从不压缩已经压缩过的内容。
---

上一篇结尾我说过，messages 只增不减会出问题，Context Engineering 之后单独写。这篇就接着讲，但只讲一个点：**上下文到阈值了，怎么压。**

上一篇我写的 `compact`，思路是旧的工具结果换成摘要，当时觉得挺合理。直到看到 9 月 CMU 的一篇论文（CliffCompaction），才发现这里面有两个我完全没想过的问题。

## 摘要的摘要

先看一般的做法。Agent 跑到上下文快满了，把前面一大段交给模型，让它写一份摘要，然后用摘要替换原文，继续跑。

跑一次压缩没什么问题。问题在第二次、第三次。第二次压缩的时候，上下文开头是第一次的摘要，后面是新产生的内容。很自然的做法是把它们一起再交给模型总结。于是第二份摘要里，一部分内容是“对摘要的摘要”。每滚一轮，细节就丢一层，而且丢了什么，模型自己也不知道。

论文里还提了一个我觉得更要命的点。摘要写得越像样，模型越觉得“前面的事我都知道了”，就不会再回头去读那个文件、翻那段文档。可摘要里恰好漏了的那个函数签名，它就只能凭印象编。论文管这个叫 compaction precision（压缩后保留下来的信息有多准）低。

## 他们的做法：只删，不写

CliffCompaction 的规则很简单，我直接用代码复述一遍。它盯着的是占空间最多的东西：在他们统计的 Terminal-Bench 2.0 任务里（模型是 GLM 5.1），不压缩的情况下，工具返回占 token 的 56.0%，工具调用占 28.0%，加起来 84%。所以只动这两样：

- 工具返回超过 500 个字符的，整个丢掉；500 以内的原样保留（grep 结果、退出码这种，短，又常常有用）。
- 工具调用只留一个签名：工具名、目标路径、关键参数。比如写文件时内联的整个文件内容，删掉，文件本来就在磁盘上。
- 模型的思考截到 300 字符。
- system prompt 和第一条用户消息（任务描述）完整保留，最近 K 轮也原样保留。

没有任何一步是“让模型重新表述”。留下来的每个字都是原话，要么整段在，要么被删掉。

被删掉的东西怎么办？因为工具调用的签名还在，模型想看的话，照着签名再调一次就行。这对前端同学应该很好理解，有点像把大图换成占位图加一个 URL，要用的时候再请求。

用 TS 写大概是这样（我按论文的算法简化的，不是官方实现）：

```typescript
type Msg =
  | { role: 'assistant'; thinking: string; call: { name: string; args: Record<string, string> } }
  | { role: 'tool'; content: string }
  | { role: 'compacted'; content: string };

const clip = (s: string, n: number) => (s.length > n ? s.slice(0, n) + '…' : s);

function signature(call: { name: string; args: Record<string, string> }) {
  // 只留名字和短参数，长内容（比如要写入的文件正文）不要
  const args = Object.entries(call.args)
    .filter(([, v]) => v.length <= 150)
    .map(([k, v]) => `${k}=${v}`)
    .join(', ');
  return `${call.name}(${args})`;
}

function compact(turns: Msg[], keepRecent = 4): { summary: string; recent: Msg[] } {
  const old = turns.slice(0, -keepRecent * 2);
  const recent = turns.slice(-keepRecent * 2);
  const parts: string[] = [];

  for (const m of old) {
    if (m.role === 'compacted') continue;            // 上一次压缩的产物，直接扔
    if (m.role === 'assistant') {
      parts.push(clip(m.thinking, 300), signature(m.call));
    } else if (m.role === 'tool' && m.content.length <= 500) {
      parts.push(m.content);                         // 短结果原样留
    }                                                // 长结果：丢
  }
  return { summary: parts.join('\n'), recent };
}
```

注意 `if (m.role === 'compacted') continue` 那一行。这就是论文最核心的一条：**从不压缩一份压缩**。每次触发压缩，只处理上一次压缩之后新长出来的那一段，上一次压缩留下的块整个丢掉，不嵌套进新的块里。所以不会出现摘要的摘要，每一次压缩的保真程度是一样的。

## 代价是什么

直觉上会担心：上上次的信息不就彻底没了吗？

论文自己承认了这点，它说是拿 recall（能回忆起多少）换 precision。只有最近的一个窗口能活过一次压缩。但他们认为损失没有看上去那么大，因为每一轮模型的行动都是在看着上一个压缩块的情况下做出的，它的决策、写下的文件里，已经带着早期信息的影响。这个说法我觉得合理但不好验证，我只能说论文里的结果是支持的：在 Terminal-Bench 2.0 上，Kimi K2.6 全上下文跑的成功率是 59.16%，阈值压到 32K 和 16K 都是 61.42%，反而略高；对照的 LLM 摘要方案在 32K 是 58.36%，16K 掉到 55.45%。花费上，Kimi 全上下文每题 0.40 美元，压到 32K 是 0.24，16K 是 0.19。

表里的标准差不小（几个点），所以“略高”不能当成压缩让模型变强了，更稳妥的读法是：压到 16K，成功率没掉，钱省了一半以上。

## 为什么不是每轮都瘦身

还有一个我之前没意识到的点，是它为什么要等到阈值才压，而不是像我写的 `compact` 那样每轮都改旧消息。

原因是 prompt cache。API 对命中缓存的输入 token 便宜很多，论文里测的这些模型，没命中缓存的输入大约是命中的 5 到 6 倍价格。缓存按前缀匹配，你一旦改动了前面某条消息，从改动处往后的缓存全部失效，要重新算。

我上一篇那个每轮都做一次的 `compact`，正好踩在这个坑上：每一轮都在改历史，缓存几乎永远命不中。CliffCompaction 的办法是让上下文自然往下长，中间一个字都不动，缓存一直有效，只在撞到阈值的那一刻压一次。上下文长度的曲线就是一个锯齿，涨到阈值，断崖式掉下来，再涨。名字里的 cliff 就是这么来的。压缩的那一次缓存确实会失效，但相对于很长一段不动的区间，是摊得开的。

## 小结

我打算在自己的小 Agent 里改两处：压缩改成阈值触发，不再每轮都改；先不让模型写摘要，用最笨的截断加签名。这两处我还没来得及跑对比，改完再记。

还有一个没想清楚的地方：这篇论文的场景是写代码和终端任务，工具返回里大头是读文件，丢了可以重读。如果你的 Agent 调的是不可重复的工具（下单、发消息、查一个会变的接口），“再调一次就能找回来”就不成立了，这种返回我觉得不能按长度一刀切丢掉。这一块论文没讲，我也还没想清楚怎么处理。

参考：

- [CliffCompaction: Cost-Efficient Compaction for Long-Horizon Coding Agents（arXiv:2609.26779）](https://arxiv.org/abs/2609.26779)
- [作者开源的实现](https://github.com/nguyenvuthientrang/cliffcompaction)
- [Less Context, Better Agents（上一篇引用的论文）](https://arxiv.org/html/2606.10209)
