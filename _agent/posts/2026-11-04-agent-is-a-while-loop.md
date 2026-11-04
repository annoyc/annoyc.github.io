---
title: Agent 拆开看，就是一个 while 循环
slug: agent-is-a-while-loop
date: 2026-11-04 14:00:00
tags: [Agent, Agent Loop]
excerpt: 刚开始看 Agent 相关资料时，满屏都是“自主规划”“多步推理”这类词，看得人发虚。后来自己动手写了一个最小版本，才发现核心代码不到 30 行，就是一个 while 循环。今天就把这个循环拆开讲清楚，顺便聊聊它最容易出问题的两个地方：什么时候停，以及上下文越堆越长怎么办。
---

刚开始看 Agent 相关资料时，满屏都是“自主规划”“多步推理”这类词，看得人发虚。后来自己动手写了一个最小版本，才发现核心代码不到 30 行，就是一个 while 循环。今天就把这个循环拆开讲清楚，顺便聊聊它最容易出问题的两个地方：什么时候停，以及上下文越堆越长怎么办。

## 先看普通的一次调用

平时我们调大模型接口，是一问一答：把 messages 发过去，拿回一段文本，结束。

Agent 多了一样东西：**工具**。你在请求里告诉模型“我这边有这几个函数可以用”，模型回复的时候，可以不直接回答，而是说“请帮我调用一下 `readFile`，参数是 `src/App.vue`”。

注意，模型自己是执行不了任何东西的，它只是**提出**要调用什么。真正去读文件、发请求的，是我们写的代码。执行完以后，把结果再塞回 messages 里，发给模型，让它接着想。

然后模型可能又要调一个工具，我们再执行、再塞回去……直到它觉得信息够了，直接给出最终回答。

这一来一回，就是 Agent Loop。

## 30 行代码写出来

用 JS 写一个最小版本（`callModel` 换成你用的任何一家 SDK 都行，结构是一样的）：

```javascript
const tools = {
  readFile: async ({ path }) => fs.readFile(path, 'utf-8'),
  listDir: async ({ dir }) => (await fs.readdir(dir)).join('\n'),
};

async function runAgent(task) {
  const messages = [{ role: 'user', content: task }];

  while (true) {
    const reply = await callModel({ messages, tools: toolSchemas });
    messages.push(reply);

    // 模型没要求调工具，说明它认为可以交卷了
    if (!reply.toolCalls?.length) {
      return reply.content;
    }

    // 把模型要的工具挨个执行，结果原样塞回去
    for (const call of reply.toolCalls) {
      let result;
      try {
        result = await tools[call.name](call.args);
      } catch (err) {
        result = `调用失败：${err.message}`;
      }
      messages.push({ role: 'tool', toolCallId: call.id, content: String(result) });
    }
  }
}
```

就这些。所谓“自主规划”，其实是模型在每一轮根据 messages 里已有的信息，决定下一步调哪个工具。我们的代码只负责三件事：**转发、执行、追加**。

有个细节值得留意：工具报错的时候，我没有 throw，而是把错误信息当成结果还给模型。这样模型能看到“路径不存在”，然后自己换个路径再试。对模型来说，错误信息也是信息。

写前端的同学可以这么类比：这有点像一个 reducer，messages 是 state，每一轮模型给出一个 action，我们执行 action 产生新的 state，再喂回去。区别在于这个 reducer 什么时候停，是模型自己说了算。

而问题恰恰出在这里。

## 坑一：只靠模型自己说“做完了”

上面的代码只有一个出口：模型不再调工具。

听着合理，但实际跑起来会遇到两种情况。一种是模型陷入死循环，同一个工具、同样的参数反复调，因为它没意识到结果不会变；另一种更隐蔽，模型说“已完成”，其实测试根本没跑，或者跑了没过。

所以真实项目里，至少要给这个循环加几道闸：

```javascript
const MAX_TURNS = 20;
let turns = 0;
const seen = new Set();

while (true) {
  if (++turns > MAX_TURNS) throw new Error('超过最大轮数，强制停止');

  const reply = await callModel({ messages, tools: toolSchemas });
  messages.push(reply);

  if (!reply.toolCalls?.length) {
    // 别急着信它，用代码验一遍
    if (await checkDone()) return reply.content;
    messages.push({ role: 'user', content: '检查没通过，请继续。' });
    continue;
  }

  for (const call of reply.toolCalls) {
    const key = call.name + JSON.stringify(call.args);
    if (seen.has(key)) {
      messages.push({ role: 'tool', toolCallId: call.id, content: '这个调用刚才做过了，结果不会变，换个思路。' });
      continue;
    }
    seen.add(key);
    // ...正常执行
  }
}
```

三道闸分别是：**最大轮数**（兜底，防止烧钱），**重复调用检测**（发现原地打转就提醒它），**完成校验**（`checkDone` 用代码判断，比如跑一下测试、看看文件是不是真的生成了）。

最后一条最重要。我看到一篇讲 loop 的文章里有句话说得很直白：“做完了”应该意味着测试通过，而不是模型说它做完了。

## 坑二：messages 只增不减

再看一眼那个循环，`messages.push` 出现了好几次，但从来没有删过东西。

每调一次工具，结果就整个塞进去。读一个 500 行的文件，这 500 行就永远留在上下文里了。跑个十几轮，token 数会涨得很夸张，速度变慢、费用变高，而且模型面对一大坨旧信息，注意力反而会被稀释。

今年 6 月微软有篇论文（*Less Context, Better Agents*）专门测了这件事。他们让 Agent 通过 MCP 在 Dynamics 365 里处理酒店报销单，对比了三种做法：

| 做法 | 完全处理成功率 | 总 token |
| --- | --- | --- |
| 保留全部历史 | 71.0% | 约 148 万 |
| 只保留最近 5 轮工具调用 | 79.0% | 约 53.5 万 |
| 最近 5 轮 + 更早的部分做摘要 | 91.6% | 约 55.3 万 |

这组数据挺反直觉的：删掉旧的工具结果，效果不降反升；再给删掉的部分留一段摘要，成功率又涨了十几个点，token 只多了一点点。

落到代码上，思路很简单：每轮调用模型之前，对 messages 做一次“瘦身”。最近几轮的工具结果原样保留，更早的替换成一句话摘要，或者干脆只留个“已读取 xxx 文件”的占位。

```javascript
function compact(messages, keep = 5) {
  const toolMsgs = messages.filter(m => m.role === 'tool');
  const old = new Set(toolMsgs.slice(0, -keep));
  return messages.map(m =>
    old.has(m) ? { ...m, content: summarize(m.content) } : m
  );
}
```

`summarize` 可以先用最笨的办法，截取前 200 个字符；之后再换成让一个便宜的小模型来写摘要。

## 小结

如果只记住一件事，那就是：**Agent = 模型 + 工具 + 一个循环**。模型负责决定下一步做什么，循环负责执行和反馈。

真正花工夫的地方不在循环本身，而在循环的边界上：什么时候停（不能只听模型的），以及每一轮给模型看什么（不是越多越好）。后面这件事现在有个专门的叫法，叫 Context Engineering，之后单独写一篇。

建议动手写一遍上面的代码，给它两个读文件的工具，让它回答“这个项目用的是什么构建工具”。看着它一步步 `listDir`、`readFile`、再给出答案，比看十篇概念文章都管用。

参考：

- [Less Context, Better Agents: Efficient Context Engineering for Long-Horizon Tool-Using LLM Agents](https://arxiv.org/html/2606.10209)
- [Loop Engineering, Clearly Explained](https://www.dailydoseofds.com/p/loop-engineering-clearly-explained/)
