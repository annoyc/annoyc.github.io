---
title: 权限别只在前面拦：用户点了“确认”之后，执行入口还要再查一遍
slug: authorize-at-execution-boundary
date: 2026-11-08 14:00:00
tags: [Agent, 工具安全, 权限, 数问工作台]
excerpt: 我的数据库 Agent 里，SQL 在生成时校验过一次，用户点确认后，执行前又校验了一次，连“能不能写”这个权限都是服务端重新算的。10 月底有篇论文把这个思路讲成了一条原则：授权决定放在模型外面，并且贴着工具执行的那一刻。
---

前几篇写的都是怎么让 Agent 更好用，这篇换个方向，讲一件偏防守的事：权限检查放在哪一层。

我在前端写过很多“按钮置灰”。管理员才能删除，非管理员的按钮就 disabled。后来被后端同事教育过：前端的置灰只是体验，真正的权限必须在接口里再查一遍，因为请求可以不经过你的按钮直接发出来。

Agent 里是同一个道理，只是“不可信的前端”多了一个：模型。

## 论文怎么说

10 月 31 日挂出的一篇 arXiv 论文，[AgentCap: Capability-Based Runtime Authorization for Secure LLM Agent Execution](https://arxiv.org/abs/2611.00729)（arXiv:2611.00729，NeurIPS 2026 Agents in the Wild 工作坊，非存档）。它的出发点是：提示词过滤和模型对齐都是概率性的，而且发生在攻击者要操纵的那个推理过程里。一条注入进来的指令可以说服一个本来就有合法凭证的 Agent，所以防线不能只在模型脑子里。

它的做法是在模型和工具之间放一个确定性的授权层。论文把核心约束写成一句话：任何模型产生的工具调用，没有独立可验证的授权，就到不了 MCP server。具体是一个 MCP 包装层加一个授权网关：Agent 提一个调用，同时声明意图，策略引擎按规则检查（工具在不在目录里、敏感参数的来源是否可信、前置步骤做没做、有没有互斥的权限组合），通过了发一个短期的、带调用次数预算的签名令牌，网关在转发给工具之前验证它。

数字我从原文核对过，都是论文自己的测试：AgentDojo 上，GPT-4o 的攻击成功率从 45.63% 降到 5.09%，GPT-5.4 从 33.23% 降到 0.00%；InjecAgent 上分别从 23.24% 降到 8.63%、从 11.95% 降到 0.00%。验证一次令牌的中位耗时是 79.9 微秒，几乎可以忽略。

代价它也老实写了：没攻击的正常任务里，可用性掉了 3.1 到 11.7 个点，原因是拒绝得比较保守，缺前置步骤、权限组合冲突的调用直接被拒。所以这不是白送的，规则越严，误伤越多。

我没有它那么重的令牌体系。但“授权决定放在模型之外、放在执行的那一刻”这条，我在自己的项目里正好有个很朴素的版本。

## 数问工作台里的四道关

[数问工作台](https://github.com/annoyc/deepseek-db-chat)是我的自然语言查数据库项目。模型生成 SQL，用户在界面上看过、点“确认”，才真正执行。默认只读，写入要服务器显式开。

一开始我以为有“用户确认”这一步就够了。后来想明白，用户确认的是“这条 SQL 看起来对”，不是“这条 SQL 有权限执行”。用户看不出一条 SQL 里有没有 `LOAD_FILE()`，也不会去检查浏览器发给服务器的 `sqlPermission` 是不是被人改成了 `write`。

所以同一件事被查了四次。

第一次，一轮对话开始时，服务端用连接信息重新算出这次到底允许什么权限：

```typescript
const decryptedConnection = resolveDatabaseConnection(data.connection)
const effectiveSqlPermission = resolveSqlPermission(decryptedConnection, data.sqlPermission)
```

第二次，模型调用 `execute_sql` 工具时，工具函数里先过一遍校验，不通过就把原因当成工具结果还给模型，让它自己改：

```typescript
const validation = validateSql(sql, sqlPermission)
if (!validation.allowed) {
  const errorMsg = `SQL 被安全策略拦截: ${validation.reason}。当前模式仅允许: ${allowedLabel}。`
  pushError('execute_sql', new Error(errorMsg))
  return errorMsg
}
```

这一步是给模型看的，省得一条注定被拒的 SQL 白白让用户确认。

第三次，用户点确认，请求打到 `confirm-sql.ts`。注意这里不信任前一次算出来的权限，重新算：

```typescript
const decryptedConnection = resolveDatabaseConnection(data.connection)
const permission = resolveSqlPermission(decryptedConnection, data.sqlPermission)
const validation = validateSql(data.sql, permission)
if (!validation.allowed) {
  return { success: false, error: validation.reason || 'SQL 校验失败' }
}
const result = await executeQuery(decryptedConnection, data.sql, permission, getRequest().signal)
```

[源码](https://github.com/annoyc/deepseek-db-chat/blob/d7a9caf477daa1056e32df8763542774c91abcae/src/server/functions/confirm-sql.ts#L42-L56)。这个接口是个普通的服务端函数，谁都能直接 POST，它不会因为“前面一定过了确认界面”就放松。

第四次，在 `executeQuery` 里面，真正摸到数据库之前：

```typescript
const prepared = prepareSqlForExecution(
  sql,
  resolveSqlPermission(connection, requestedPermission),
  MAX_RESULT_ROWS,
)
```

[源码](https://github.com/annoyc/deepseek-db-chat/blob/d7a9caf477daa1056e32df8763542774c91abcae/src/server/database.ts#L230-L234)。又算一遍权限，又解析一遍 SQL。以后哪天有人新加一个调用 `executeQuery` 的入口，忘了校验，这一层兜底。

重复了四次听着啰嗦，但检查本身很便宜，就是一次 AST 解析，而漏掉一次的后果是写生产库。

## 权限不是“请求里写的”

第二个要点是权限怎么算。看 `sql-policy.ts`：

```typescript
export function resolveSqlPermission(connection, requested = 'readonly') {
  if (isDemoConnectionId(connection.id) || isDemoDatabaseTarget(connection)) return 'readonly'
  if (requested !== 'write' || connection.env === 'prod' || process.env.SQL_WRITE_ENABLED !== 'true') return 'readonly'
  const allowedTargets = (process.env.SQL_WRITE_ALLOWLIST ?? '').split(',') /* 规整成 host:port/db */
  const target = `${connection.host.toLowerCase()}:${connection.port}/${connection.database}`
  return allowedTargets.includes(target) ? 'write' : 'readonly'
}
```

[源码](https://github.com/annoyc/deepseek-db-chat/blob/d7a9caf477daa1056e32df8763542774c91abcae/src/server/sql-policy.ts#L6-L21)。函数头上我写了一行注释：浏览器设置或环境标签，自己不能授予写权限。

浏览器传来的 `requested` 只是“我想要写”，是一个请求，要同时满足：连接不是演示库、环境标签不是 prod、服务器环境变量打开了写入、这个具体的 host:port/库名在白名单里，才会变成 `write`。任何一个条件不满足，一律退回只读，没有报错，也没有协商。

这和论文里“能力由独立的策略引擎签发，Agent 只能申请”是一个形状，我这里的“令牌”退化成了每次现算的一个字符串，策略就是环境变量和白名单。

实现时踩过一个小坑：白名单比较时，主机名我转小写，数据库名不转。MySQL 的库名在 Linux 上区分大小写，如果统一转小写，`DevDB` 和 `devdb` 会被当成同一个，白名单就放宽了。代码里那行注释就是为了防止以后自己手欠把它“统一一下”。

还有一点：校验失败是**默认拒绝**。`sql-guard.ts` 里 SQL 解析失败时不会退回去用关键词匹配糊弄一下，直接返回“无法可靠解析，为安全起见已拒绝”。这点和论文里的 fail-closed 一致，代价也一样：偶尔会拒掉一条其实没问题的 SQL。我接受了，用户改下写法就行。

## 它挡不住什么

说清楚边界，免得说大了。

这套东西管的是“这条语句能不能执行”，不管“这条语句该不该是这个意思”。模型被诱导写了一条合法的 `SELECT * FROM users`，每一道关都会放行，因为它确实是只读的。这种问题要靠结果脱敏、行数限制之类的别的手段，论文里管这个叫敏感参数的来源追踪，我的项目没做到那一步。

另外，应用层的校验不能替代数据库自己的权限。README 里我也写了，生产环境还是应该给 Agent 一个数据库层面的只读账号。AST 解析器会有我没想到的绕法，账号权限是最后一道，不依赖我的代码写对没有。

## 小结

确认界面是给人看的，不是授权。授权是服务端的事，而且要在每个真正产生副作用的入口各查一次，用的权限要从服务端能信的东西里现算，不要读请求里带的那个字段。模型只能提议，浏览器也只能提议。

参考：

- [AgentCap: Capability-Based Runtime Authorization for Secure LLM Agent Execution（arXiv:2611.00729）](https://arxiv.org/abs/2611.00729)
- [数问工作台 deepseek-db-chat](https://github.com/annoyc/deepseek-db-chat)
- [src/server/sql-policy.ts](https://github.com/annoyc/deepseek-db-chat/blob/d7a9caf477daa1056e32df8763542774c91abcae/src/server/sql-policy.ts)
- [src/server/functions/confirm-sql.ts](https://github.com/annoyc/deepseek-db-chat/blob/d7a9caf477daa1056e32df8763542774c91abcae/src/server/functions/confirm-sql.ts)
