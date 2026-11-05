# Agent 日课：写作与发布说明

本仓库是 Hexo 的构建产物（GitHub Pages 从 `master` 发布）。Agent 系列文章的 Markdown 源文件放在 `_agent/posts/`，由 `_agent/publish.py` 渲染成和站点一致的 NexT 页面。

## 发布流程

1. 在 `_agent/posts/` 新建 `YYYY-MM-DD-<slug>.md`，front matter 格式：

   ```
   ---
   title: 文章标题
   slug: lowercase-ascii-slug
   date: 2026-11-04 14:00:00
   tags: [Agent, 具体主题]
   excerpt: 首页卡片上显示的摘要，一两句话
   ---
   ```

2. `pip install markdown`（首次）后运行 `python3 _agent/publish.py`。脚本会重建所有 Agent 文章页、`/agent/` 列表页、首页置顶、归档、搜索索引和导航菜单，可重复运行。
3. 提交 `_agent/posts/` 下的新文件以及生成的 HTML，推送到 `master`。

## 选题

- 每篇只讲**一个**具体知识点，讲透，不做综述、不罗列新闻。
- 先看 `_agent/index.json` 里已经写过的题目，不要重复；可以在前文基础上往深走（文末“之后单独写一篇”的承诺优先兑现）。
- 选题从最近一两周网上的 Agent 技术文章、论文、官方工程博客里找，要有可靠来源，数据要核实过原文。
- 读者是前端开发者，正在转 Agent 开发。能用 JS/TS 示例就用 JS/TS，能类比前端概念就类比，但别硬凑。

### 结合博主自己的项目

博主的开源项目 [deepseek-db-chat（数问工作台）](https://github.com/annoyc/deepseek-db-chat) 是一个自然语言查数据库的 Agent（TypeScript）。选题时先浏览一下它的代码，如果当天的知识点在项目里有对应的、值得单独拿出来讲的模块，就以“我在自己项目里是怎么做的”的视角来写，可以直接引用项目里的真实代码片段（注明文件路径，附 GitHub 链接）。值得写的方向举例：

- `src/core/generate/generate-loop.ts`：Agent 循环本身
- `src/core/context/compact.ts`：上下文压缩
- `src/server/functions/confirm-sql.ts`、`src/server/sql-policy.ts`：人工确认与执行边界的 SQL 安全策略
- `src/server/schema-subsetting.ts`：大库表结构裁剪，只把相关表给模型
- `src/server/intent-router.ts`：意图路由
- `src/server/functions/classify-hallucination.ts`：幻觉检测
- `src/core/client/retry.ts`、`src/server/langfuse.ts`：重试与可观测性
- DeepSeek 前缀缓存与确定性消息构建

原则：

- 一定要先读真实代码再写，不能凭文件名猜实现。描述和代码必须与仓库当前版本一致。
- 同一个模块不要重复写；已写过的项目模块会记录在 `_agent/index.json` 对应文章的 tags 里（打上 `数问工作台` 标签）。
- 项目里没有合适的、或者模块太平淡不值得单独写，就照常写通用知识点，不要硬凑。大约每 3～4 篇里结合项目写一篇比较合适。

## 文风

以第一人称写，像一个前端工程师在记录自己学 Agent 的过程。

- 口语化、句子短。允许“我一开始也没想明白”“这里踩过坑”这类真实的表达。
- 不用这些套话：“在当今……时代”“让我们深入探讨”“值得注意的是”“总而言之”“综上所述”“赋能”“颠覆”“不仅……更是……”“这不是……而是……”。
- 不用 emoji，不加粗一大片，不要每段都是列表。正文以段落为主，列表只在真正并列的时候用。
- 不要每节结尾都来一句总结升华。说完就收。
- 结构大致是：一个具体问题或场景引入 → 最小可运行的代码/例子 → 一两个真实的坑 → 简短小结 → 参考链接。篇幅 1500～3000 字。
- 文中不出现“AI 生成”“作为 AI”之类的字样。
