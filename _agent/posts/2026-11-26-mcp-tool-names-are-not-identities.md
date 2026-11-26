---
title: MCP 接了多个 Server，为什么不能把工具名当成身份凭证
slug: mcp-tool-names-are-not-identities
date: 2026-11-26 16:00:00
tags: [Agent, MCP, 工具安全]
excerpt: 挂了多个 MCP Server 时，模型把原本该给内部服务的请求调到了第三方工具上，攻击者甚至不需要入侵任何服务器。结合最新实测论文和 MCP 官方规范，拆解多 Server 下的工具提供方错绑（Tool-Provider Misbinding），以及在 TypeScript 客户端里怎么做提供方绑定与清单校验。
---

刚开始给自己的 Agent 接 MCP（Model Context Protocol）时，我觉得这套协议最爽的地方就是“插拔方便”：本地配一个 `mcpServers` 列表，连上数据库 Server、文档检索 Server，再顺手挂两个社区的第三方工具 Server，客户端启动时把每个 Server 的 `tools/list` 拉下来拼成一个数组丢给模型，路由表里用 `tool.name` 查一下该转发给哪个连接，跑起来顺滑得很。

直到我联想到前端天天打交道的 npm 依赖混淆（Dependency Confusion），后背才有点发凉：你在项目里 `import` 一个包名，如果只认名字不认发布源，私包名就可能被公网同名包劫持。放到多 Server 的 MCP 客户端里，问题几乎一模一样，而且更隐蔽——包管理器好歹按确定规则解析，Agent 这里却是大模型看着自然语言描述挑工具。

11 月 16 日 arXiv 上新发了一篇专门测这个问题的论文《When Tool Names Are Not Identities: Measuring Provider Misbinding in MCP-Based AI Agents》（arXiv:2611.21577）。作者把这种故障起名叫 Tool-Provider Misbinding（工具提供方错绑）：用户本来想调用可信 Server 的工具，客户端却把请求送到了攻击者控制的另一个 MCP Server 上。全程攻击者不需要入侵宿主机、不需要攻破大模型、也不需要劫持网络，只需要自己也是一个被客户端连上的 MCP Server，把自家工具的 `name`、`description` 和 `inputSchema` 精心填好就行。

把这篇论文的实验数据和 MCP 官方最新的 Tools 规范（2026-07-28 版）对照着读了一遍，有几个结论跟我一开始的直觉差得挺远。

## 名字撞车只是入口，真正把模型带偏的是“三件套联动”

按我原来的朴素理解，只要我给不同 MCP Server 的工具加上前缀，避免两个工具重名，这事不就解决了吗？

论文里的对照实验直接把这个幻想打碎了。作者搭了一个受控的多 Server MCP 环境：一个合法 Server 暴露了 `data_fetch_and_store` 工具，一个攻击者 Server 在工具名称（无关名 / 完全同名）、描述（无关描述 / 原样照抄 / 权威增强，比如加上“企业认证”“官方合规”这类话术）和参数 Schema（不兼容 / 兼容）三个维度上做全因子组合，一共 12 组条件（C01 到 C12），每组 10 条提示词各跑 5 遍（每条件每模型 50 次实验），横评了 GPT-5.4、Claude Sonnet 5、Gemini 2.5 Pro、Qwen3.7 Max 和 DeepSeek V3 五个客户端模型。

翻完表里的数据，最扎眼的有三件事：

第一，光撞名字没用，参数 Schema 兼容才是最大的放大器。在 C07（完全同名 + 无关描述 + 不兼容 Schema）下，除了 GPT-5.4 出现 0.02 的错绑率（Violation Rate，简称 `VR`），其余四个模型全是 0。可只要把 C09（同名 + 照抄描述 + 不兼容 Schema）里的 Schema 换成兼容的 C10，五个模型的平均 `VR` 直接从 0 飙升到约 0.78：GPT-5.4、Qwen3.7 Max、DeepSeek V3 全部达到 `1.00`（50 次里 50 次全调了攻击者工具），Gemini 2.5 Pro 达到 `0.84`，连最谨慎的 Claude Sonnet 5 也有 `0.06` 的直接错绑外加 `0.94` 的反问澄清率（Clarification Rate，简称 `CR`）。Schema 对模型来说不只是调用时的参数校验规则，它在选工具阶段本身就是强匹配信号。

第二，就算根本没有重名，照样会被抢走调用。看 C06 这组条件：工具名完全无关（排除了所有任务关键词），但用了“权威增强”的描述加上兼容的 Schema。单看主实验，DeepSeek V3 的 `VR` 就有 `0.50`，Gemini 2.5 Pro 是 `0.42`（另加 `0.56` 的澄清），Qwen3.7 Max 是 `0.20`，GPT-5.4 是 `0.04`，Claude Sonnet 5 虽然 `VR = 0`，但有 `0.22` 的概率卡在澄清上。为了排除碰巧选到某个特殊名字的偶然性，作者又换了 5 个独立生成的无关工具名各跑 50 次（合计 250 次），结果 C06 的平均 `VR` 反而更明显：DeepSeek V3 均值 `0.696`，Gemini 2.5 Pro 均值 `0.460`（且 `CR = 0.528`），GPT-5.4 均值 `0.248`，Qwen3.7 Max 均值 `0.204`。换句话说，只做“工具名去重”根本挡不住跨 Server 抢单。

第三，模型谨慎和结果稳定，都不等于安全。在 C10 里，GPT-5.4、Qwen3.7 Max 和 DeepSeek V3 的绑定稳定性（`RBS`）全是 `1.00`——因为它们非常稳定地每一次都选了攻击者。Claude Sonnet 5 确实不容易静默中招，但在高歧义条件里它会把选择抛回给用户；当攻击者把候选工具数量预算从 `r = 1` 拉到 `r = 10` 时，Claude 在 C09 和 C11 里的澄清率一路涨到 `1.00`，整个自动执行链直接停摆。靠换一个“更小心”的模型，并不能在协议层补出提供方身份。

## MCP 规范怎么说，客户端该怎么补这一层

翻开 MCP 官方最新的 Tools 规范（2026-07-28 版），其实能看到协议本身把这层边界划得很清楚：

- 工具名唯一性只在单个 Server 内部有效。聚合多个 Server 的客户端或代理遇到重名时，应该自己做消歧（比如加 Server 标识前缀）。
- 规范里专门加了一句非常关键的警告：Server 在握手时上报的 `serverInfo.name` 不保证全局唯一，客户端不应该拿它当消歧依据。
- 工具自带的 `annotations`（比如宣称自己 `readOnlyHint: true`）在安全上必须默认视为不可信，除非它来自客户端明确信任的 Server。

把论文结论和规范合在一起看，结论很直接：工具的 `name`、`description`、`inputSchema` 和 `annotations` 全是外部 Server 自己填的“自我介绍”，它们进入了模型的控制平面，但它们不能充当身份凭证。提供方身份必须在宿主客户端这一侧强行绑定——由宿主本地配置决定每个连接叫什么 `serverId`、信任等级是什么、允许参与哪类能力、以及它的元数据快照有没有在运行中途悄悄变样。

## 最小可运行原型：带提供方绑定与清单校验的 MCP 工具注册表

下面用不到 120 行纯 TypeScript（仅用 Node 内置 `node:crypto`）写一个宿主侧工具注册表。核心做四件事：用宿主本地 `serverId` 生成稳定绑定键、把不可信 Server 的 `annotations` 强制清零、对 `description + inputSchema` 算 SHA-256 指纹拦住运行期静默漂移、以及按工作流显式白名单隔离提供方，避免不相干的外部 Server 和核心工具同台竞争。

```typescript
import { createHash } from 'node:crypto';

export type TrustTier = 'trusted' | 'untrusted';

export interface ServerConfig {
  // 必须由宿主本地配置生成，绝不信任对端 serverInfo.name
  serverId: string;
  endpoint: string;
  trustTier: TrustTier;
  // 该 Server 被允许提供哪些领域的工具（显式授权边界）
  allowedScopes: string[];
}

export interface RawMcpTool {
  name: string;
  description?: string;
  inputSchema: Record<string, unknown>;
  annotations?: Record<string, unknown>;
}

export interface BoundToolEntry {
  // 暴露给模型的唯一调用键，和宿主本地 serverId 强绑定
  callKey: string;
  serverId: string;
  endpoint: string;
  trustTier: TrustTier;
  rawToolName: string;
  description: string;
  inputSchema: Record<string, unknown>;
  // 不可信 Server 的 annotations 一律剥离，防止伪造只读提示
  trustedAnnotations?: Record<string, unknown>;
  manifestHash: string;
}

function stableJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(',')}]`;
  if (value && typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([k, v]) => `${JSON.stringify(k)}:${stableJson(v)}`);
    return `{${entries.join(',')}}`;
  }
  return JSON.stringify(value);
}

function hashToolManifest(tool: RawMcpTool): string {
  const payload = stableJson({
    name: tool.name,
    description: tool.description ?? '',
    inputSchema: tool.inputSchema,
  });
  return createHash('sha256').update(payload).digest('hex').slice(0, 16);
}

export class ProviderBoundToolRegistry {
  private servers = new Map<string, ServerConfig>();
  private entries = new Map<string, BoundToolEntry>();
  private pinnedHashes = new Map<string, string>();

  registerServer(config: ServerConfig): void {
    if (!/^[a-z0-9_-]{1,32}$/i.test(config.serverId)) {
      throw new Error(`Invalid local serverId: ${config.serverId}`);
    }
    this.servers.set(config.serverId, config);
  }

  // 注册或刷新某个 Server 的 tools/list；如果已固定指纹且元数据发生漂移，直接拒收
  syncServerTools(serverId: string, tools: RawMcpTool[], pinOnFirstSeen = true): void {
    const server = this.servers.get(serverId);
    if (!server) throw new Error(`Unknown serverId: ${serverId}`);

    for (const tool of tools) {
      const callKey = `${server.serverId}__${tool.name}`;
      const manifestHash = hashToolManifest(tool);
      const pinned = this.pinnedHashes.get(callKey);

      if (pinned && pinned !== manifestHash) {
        throw new Error(
          `Metadata drift detected for ${callKey} (${server.endpoint}): expected ${pinned}, got ${manifestHash}`,
        );
      }
      if (pinOnFirstSeen && !pinned) {
        this.pinnedHashes.set(callKey, manifestHash);
      }

      this.entries.set(callKey, {
        callKey,
        serverId: server.serverId,
        endpoint: server.endpoint,
        trustTier: server.trustTier,
        rawToolName: tool.name,
        description: tool.description ?? '',
        inputSchema: tool.inputSchema,
        trustedAnnotations: server.trustTier === 'trusted' ? tool.annotations : undefined,
        manifestHash,
      });
    }
  }

  // 只把具备当前任务 scope 资格的提供方工具放进候选集，避免 C06 跨 Server 抢单
  selectCandidatesForScope(scope: string): BoundToolEntry[] {
    return [...this.entries.values()].filter((entry) => {
      const server = this.servers.get(entry.serverId);
      return Boolean(server?.allowedScopes.includes(scope));
    });
  }

  // 执行前按 callKey 反查真实提供方，绝不拿裸 tool.name 去路由
  resolveExecution(callKey: string, requiredScope: string) {
    const entry = this.entries.get(callKey);
    if (!entry) throw new Error(`Unbound tool callKey: ${callKey}`);

    const server = this.servers.get(entry.serverId);
    if (!server?.allowedScopes.includes(requiredScope)) {
      throw new Error(`Provider ${entry.serverId} is not authorized for scope "${requiredScope}"`);
    }

    return {
      targetServerId: entry.serverId,
      targetEndpoint: entry.endpoint,
      rawToolName: entry.rawToolName,
      // 给前端确认弹窗展示的溯源信息：用户必须看到到底是谁来执行
      uiProvenance: {
        provider: `${entry.serverId} (${entry.endpoint})`,
        trustTier: entry.trustTier,
        toolName: entry.rawToolName,
        manifestHash: entry.manifestHash,
      },
    };
  }
}
```

写前端的同学对 `selectCandidatesForScope` 和 `manifestHash` 这两步应该会觉得眼熟：前者像浏览器里的 CSP 或权限作用域隔离，不让第三方脚本随便挂到核心表单上下文里；后者像静态资源上的 SRI（Subresource Integrity）哈希校验，防止远端悄悄换包。

## 落地多 Server MCP 客户端最容易踩的两个坑

### 坑一：用 `serverInfo.name` 拼前缀，或者以为加了前缀就万事大吉

刚接 MCP SDK 时，最顺手的写法就是拿到连接初始化返回的 `serverInfo.name`，然后拼成 `${serverInfo.name}_${tool.name}`。这里其实埋了两颗雷。

第一颗雷，MCP 官方规范已经明说了：`serverInfo.name` 是对端自己填的，两个不同的 Server 完全可以都自称 `internal-data-server`，拿它当命名空间前缀等于把门牌号交给访客自己贴。前缀必须用宿主本地配置文件里由你指定的 `serverId`（甚至把关键 Server 的 endpoint 或公钥指纹一起绑定）。

第二颗雷更隐蔽：哪怕每个 Server 的前缀都完全唯一，只要你把所有已连接的 MCP Server 的工具一股脑塞进同一轮请求的 `tools` 数组里，论文里的 C06 场景就会发生——第三方 Server 根本不需要和你重名，只要起个无关名字、把描述写成“经过安全审计的企业级数据拉取接口”、把 `inputSchema` 对齐成同样接收 `dataset_id` 和 `destination`，主流模型在相当高比例的请求里就会主动跳过你的正牌工具去调它。所以，真正治本的办法是在进模型候选集之前就按业务 scope 把提供方隔开，给核心能力锁死允许的 `serverId` 白名单，别让所有外部 Server 同台竞价。

### 坑二：弹窗只给用户看工具名，或者忽略了运行期 `listChanged` 漂移

很多 Agent 客户端已经做了人工确认弹窗，觉得“反正高危操作要用户点同意，错绑了用户也能看出来”。但回头看一眼自己写的确认 UI，大概率长这样：`模型请求调用 data_fetch_and_store，参数 {...}，允许 / 拒绝？`

这种弹窗在同名错绑面前几乎形同虚设——名字一样、参数也一样，用户根本看不出背后连的是公司内网 Server 还是昨天刚装的社区插件。确认界面必须把宿主侧校验过的提供方来源（本地 `serverId`、实际连接的 endpoint、可信等级）和参数一起展示出来，就像浏览器地址栏必须把真实域名高亮给用户看一样。

另一个容易漏掉的口子是 MCP 的动态工具列表更新（`notifications/tools/list_changed`）。如果客户端收到变更通知就无条件刷新内存里的工具定义，一个原本老实的第三方 Server 完全可以先通过初次审查，等过两天再发一次 `listChanged`，把某个工具的 `description` 和 `inputSchema` 悄悄改成高匹配伪装版。在客户端记录首次确认时的 `manifestHash`（覆盖 `name + description + inputSchema`），一旦检测到漂移就立刻挂起并要求重新确认，成本极低，但能直接堵死这种“先装乖再变脸”的套路。

## 小结

在多 Server 的 MCP 架构里，工具元数据是直接参与路由决策的控制平面，不能当普通文档看：

- 工具名、描述和 `inputSchema` 的组合效应会让主流模型在无入侵前提下把请求错绑到第三方 Server，且即使没有重名，“权威话术 + 兼容 Schema”依然会触发跨 Server 劫持。
- 客户端不能用对端上报的 `serverInfo.name` 做消歧，必须用宿主本地 `serverId` 绑定调用键，并默认把不可信 Server 的 `annotations` 当不可信处理。
- 在进模型候选集前按业务域做提供方白名单隔离，同时对工具清单做哈希固定并在人工确认 UI 里展示真实提供方来源，才能把“选对能力”和“选对提供方”这两道门都锁住。

参考：

- [When Tool Names Are Not Identities: Measuring Provider Misbinding in MCP-Based AI Agents（arXiv:2611.21577, 2026-11-16）](https://arxiv.org/abs/2611.21577)
- [Tools — Model Context Protocol Specification (2026-07-28)](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)
- [Security Best Practices — Model Context Protocol Docs](https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices)
