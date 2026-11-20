---
title: Agent 跨会话记忆怎么管：别只靠“随手记笔记”，还得靠离线“做梦”纠错
slug: agent-memory-and-dreaming
date: 2026-11-20 16:00:00
tags: [Agent, Agent Memory, Context Engineering]
excerpt: 之前写过单轮会话上下文满了怎么压缩，但如果用户隔天新开一个会话，Agent 就又失忆了。给它挂一个本地记忆目录让它随手记笔记行不行？Anthropic 最近公开的 Agent Memory 研究给了一个很现实的教训：只管在线往里写，目录很快会越写越乱，真正管用的是有硬上限的分层索引，加上离线空闲期的“做梦”（Dreaming）纠错。
---

前几篇聊上下文压缩（Compaction）的时候，解决的都是同一个问题：单次对话越滚越长，快撞到窗口上限了怎么办。

真把 Agent 做成日常能用的产品，很快会撞上第二个问题：**用户昨天刚教过一遍的业务规则，今天新开一个会话，Agent 全忘了。**

写前端的同学看到这个需求，第一反应几乎一样：给它挂一个持久化存储不就行了。类似浏览器里写 `localStorage`，在服务端给 Agent 配一个 `memory/` 目录和读写工具，提示词里交代一句“以后用得上的经验自己记下来”，下次新开会话再塞回上下文。

我一开始也是这么想的。直到完整读了 Anthropic 在 11 月 6 日发的那篇《The components of agent memory》以及配套的 Dreams 文档，才发现跨会话记忆最难搞的根本不是“记不下来”，真正的麻烦在于：只管随手记、不管回头理，最后会把新会话一起带进沟里。

动手写代码之前，先把概念边界划清。Anthropic 在文章里给 Agent Memory 下了一个很窄的定义：它是 Agent 从交互和任务经验里学到、并在不同会话之间以可读上下文形式保留下来的外部知识。它**不包含**训练阶段改模型权重，**不包含**单次对话里的上下文学习，也**不包含**单会话跑满之后的摘要压缩（Compaction）。

## 为什么“随手记笔记”会越跑越慢，还会记出幻觉

把记忆目录交给在线运行的 Agent 自己维护，本质上是在要求模型做一件很反直觉的事：**在还不知道后面会发生什么的情况下，提前预测哪条信息下个会话用得上。**

一旦没有约束，模型在在线状态写笔记会立刻暴露两个问题。

第一个问题是**什么都往里记，目录迅速腐烂**。Anthropic 在 Pokémon Red 的评测里对比过两个模型跑到约 10,000 步时的记忆目录。Claude Sonnet 3.7 的策略是来者不拒：每次遇敌、捡到道具、碰到 NPC 都要建个文件记一笔，甚至把当前金币数、所在坐标这种屏幕上每轮直接能看见的状态也抄进去。结果它在 225 个文件夹里写了 413 个文件，只拿到 1 个徽章；到第 10,000 步附近，它连续 17 步都在新建 6 个文件、删掉 9 个旧文件，一步游戏操作都没走。

相比之下，Claude Fable 5 在同一阶段只写了 9 个文件（分布在 2 个子目录里）外加 1 个顶层索引 `global.md`，拿到了 4 个徽章。它只记屏幕上读不到的东西：当前目标、打道馆的计划、还有自己试出来的策略——比如连续 7 个精灵球没抓到鲤鱼王之后，它记了一条规则：“连续 3 个以上球失败就改一下菜单按键节奏”。

第二个问题更隐蔽：**在线写错一条，后面的会话会连环踩坑**。在游戏里的 Mt. Moon 山洞关卡，模型有一次误把入口处的 PokéCenter 记成了“在山洞东侧”。之后走出门，它对着这份错笔记自信地更新了一句 `Mt Moon DONE`，接着连续 3 次掉下门外的单向坡摔回旧路线，最后自己写下“我要停下来而不是继续死循环”。单次会话里的误判一旦落盘，下次新会话开局就变成了“已验证事实”。

哪怕记的内容全是对的，加载方式也有硬约束。Anthropic 在文章里提醒过，注入新会话的指令文件越长，模型的遵循度（adherence）反而越低。所以 Claude Code 的 auto memory 给顶层文件 `MEMORY.md` 设了一个死线：**每次开新会话只自动加载前 200 行或 25 KB**。它只负责当一张目录索引，告诉模型“我知道哪些事、分别放在哪个文件里”，具体细节等用到了再按路径去读。

## 解法：在线记索引，离线靠“做梦”（Dreaming）整理

怎么把错笔记改回来，又怎么把越写越胖的流水账压成精简索引？

Letta 和 UC Berkeley 的研究者在 2025 年 4 月提出过一个概念叫 **Sleep-time Compute**（arXiv:2504.13171）。思路很朴素：与其把所有推理和上下文整理都挤在用户发完消息、干等回复的“测试时”（test-time）去做，不如趁系统空闲时，让模型离线把上下文预先想一遍、整理好。论文在 Stateful GSM-Symbolic 和 Stateful AIME 两个推理任务上测出来，离线预计算能把达到同等准确率所需的测试时计算量降到大约五分之一；在多查询场景（Multi-Query GSM-Symbolic）里把离线开销摊到多次提问上，单次平均成本能降 2.5 倍。

Anthropic 把这个思路直接搬到了 Agent 记忆维护上，叫做 **Dreaming**。前端同学可以把它理解成把重计算从 UI 主线程挪到后台异步任务里：

1. **在线对话时保持克制**：开局只加载截断后的 `MEMORY.md`（比如前 200 行 / 25 KB），任务过程中只在确定发现新规则时写局部笔记，不占用主任务太多注意力。
2. **会话结束或系统空闲时跑 Dream 任务**：后台拉起一次异步整理，同时读两份东西——当前的记忆目录，和最近 1 到 100 轮的**原始会话日志（transcripts）**。
3. **对照原文做三件事**：拿着原始对话日志核对记忆里有没有写错（在 Mt. Moon 那个例子里，Dreaming 就是回看原始日志抓出了 PokéCenter 的方位错误，重写后模型直接绕回洞口走通了下一座城）；把反复出现的经验合并去重、淘汰过期状态；把过长的细节拆到子文件，只把轻量索引留回 `MEMORY.md`。

下面用纯 Node.js + TypeScript 写一个最小可运行的双阶段记忆原型。假设模型调用函数 `callModel` 已经封装好，我们把启动加载和离线 Dreaming 的核心边界拆出来：

```typescript
import fs from 'node:fs/promises';
import path from 'node:path';

const MAX_INDEX_LINES = 200;
const MAX_INDEX_BYTES = 25 * 1024;

// 1. 在线启动：只加载受控的顶层索引，防止撑爆新会话上下文
export async function loadBootMemory(memoryDir: string): Promise<string> {
  const indexPath = path.join(memoryDir, 'MEMORY.md');
  let raw = '';
  try {
    raw = await fs.readFile(indexPath, 'utf-8');
  } catch {
    return '';
  }

  const kept: string[] = [];
  let bytes = 0;
  for (const line of raw.split(/\r?\n/).slice(0, MAX_INDEX_LINES)) {
    const chunk = (kept.length ? '\n' : '') + line;
    const chunkBytes = Buffer.byteLength(chunk, 'utf-8');
    if (bytes + chunkBytes > MAX_INDEX_BYTES) break;
    kept.push(line);
    bytes += chunkBytes;
  }
  return kept.join('\n');
}

type DreamAction =
  | { op: 'upsert'; file: string; content: string }
  | { op: 'delete'; file: string };

type ModelCaller = (prompt: string) => Promise<DreamAction[]>;

// 2. 离线做梦：按快照方式生成新目录，确认无误再替换，绝不原地乱改
export async function runDreamPass(options: {
  memoryDir: string;
  transcripts: string[];
  callModel: ModelCaller;
}): Promise<{ stagingDir: string; actions: DreamAction[] }> {
  const { memoryDir, transcripts, callModel } = options;
  const stagingDir = `${memoryDir}.dream-${Date.now()}`;
  await fs.cp(memoryDir, stagingDir, { recursive: true });

  const existingFiles = await fs.readdir(stagingDir);
  const currentMemory = await Promise.all(
    existingFiles
      .filter((name) => name.endsWith('.md'))
      .map(async (name) => ({
        name,
        content: await fs.readFile(path.join(stagingDir, name), 'utf-8'),
      })),
  );

  const prompt = [
    '你是离线记忆整理器。请对照原始会话日志，检查现有记忆文件：',
    '1) 修正与会话事实冲突的错误笔记；',
    '2) 合并重复条目，删除已过期的临时状态；',
    '3) 保持 MEMORY.md 在 200 行以内，仅作为各子文件的目录索引。',
    `当前记忆：${JSON.stringify(currentMemory)}`,
    `最近会话日志：${JSON.stringify(transcripts)}`,
  ].join('\n\n');

  const actions = await callModel(prompt);
  for (const act of actions) {
    // 限制只能写当前 stagingDir 内的相对文件名，防止路径穿越
    const safeName = path.basename(act.file);
    const target = path.join(stagingDir, safeName);
    if (act.op === 'delete') {
      await fs.rm(target, { force: true });
    } else {
      await fs.writeFile(target, act.content, 'utf-8');
    }
  }

  return { stagingDir, actions };
}
```

这里面有一个细节，我是看了 Anthropic Managed Agents 的 Dreams 文档才特意加上去的：`runDreamPass` 没有直接去改原目录 `memoryDir`，而是先复制一份 `stagingDir`，在副本上做增删改。

Anthropic 在官方文档里专门强调过：Dream 任务永远不原地修改输入记忆库，而是产出一份独立的输出记忆库；如果检查觉得改坏了，直接把副本丢掉就行。这对写前端状态管理的同学应该很好理解——后台异步清理本来就有改错的风险，做成不可变快照，随时能回滚，心里才踏实。

## 落地时最容易踩的两个坑

这套“顶层索引 + 后台整理”的架子搭起来不难，但从 Anthropic 公布的评测细节来看，实际调教时有两个反直觉的坑特别容易踩。

### 坑一：用硬规则规定模型“只能记什么”，反而会把强模型调傻

最开始设计提示词时，很容易想写几条非常具体的约束，比如“只准记录报错和失败教训”。

Anthropic 在 CL-Bench 1.0 的数据库任务上专门测过这条规则。结果加上 `record only failures` 之后，Opus 4.7 和 Opus 5 从记忆目录里获得的分数提升反而明显缩水。对能力较强的模型来说，硬塞一条内容过滤规则，等于剥夺了它自己判断什么信息有价值的空间；只告诉它“这里有个记忆目录可用”，表现反而更好。

相反，较早期的模型（比如 Sonnet 4.6）真正缺的不是写入规则，而是**主动翻笔记和验证猜想的习惯**。不加引导时，Sonnet 4.6 在作答前去读记忆目录的概率不到一半（而 Sonnet 5 不用提醒也能在 10 次里主动读 9 次）；当测试人员明确提示它“先读记忆文件，再动手验证你的推测”后，Sonnet 4.6 在基准测试里的得分几乎翻了一倍。

所以提示词的发力点应该反过来：**少管模型“只能记哪一类内容”，多管它“每次开干前先查笔记、写下规则前先核实一遍”**。

### 坑二：多个 Agent 并发改同一份汇总文件，或者把笔记整理得比原文还长

如果在做梦阶段或者多 Agent 协作里同时往外写记忆，文件粒度切错了会直接丢数据。

Anthropic 在 Harvey LAB 法律尽调评测里遇到过一次很典型的事故：任务需要从一批合同里提取条款写简报，最初的指令让多个 Agent 同时去维护同一张共享的合同条款汇总表。结果大家你改一行我覆盖一版，最后生成的简报里，57 个评分要点只保住了 41 个（72%）。后来把策略改成“每份合同单独建一个页面，由单个 Agent 原文引用关键条款”，57 个要点直接保住了 100%。

同一个评测里还冒出过另一个问题：让模型把一批文档整理成结构化 Wiki 时，如果没控制抽象粒度，模型把所有细节原封不动抄了一遍，整出来的 Wiki 文件总体积比原始文档还要大，变成了“跟帝国一样大的地图”。另外首次整理难免留下指向未创建页面的断链，往往需要排第二轮 Dream 任务专门做一次全库扫链——在他们展示的一个例子里，第二轮整理一次就修掉了 9 条断链，还补出了包含索引在内的 3 个新页面。

## 小结

跨会话的 Agent 记忆，做起来其实不在“写文件”这步上，而在两个边界：

- **加载边界**：顶层入口文件（比如 `MEMORY.md`）必须设行数或字节数上限，只当索引不堆正文，把细节下沉到按需读取的子文件里；
- **维护边界**：在线写入难免记错、记重、记过时，靠离线空闲期的 Dream 任务回看原始会话日志做纠错和合并，并且按写时复制的方式出新快照，不要原地覆盖。

只要把这两道边界守住，跨会话记忆才不会变成越积越臭的上下文垃圾堆。

参考：

- [The components of agent memory（Anthropic, 2026-11）](https://www.anthropic.com/research/components-of-agent-memory)
- [Dreams（Claude Platform Docs）](https://platform.claude.com/docs/en/managed-agents/dreams)
- [How Claude remembers your project（Claude Code Docs）](https://code.claude.com/docs/en/memory)
- [Sleep-time Compute: Beyond Inference Scaling at Test-time（arXiv:2504.13171）](https://arxiv.org/abs/2504.13171)
