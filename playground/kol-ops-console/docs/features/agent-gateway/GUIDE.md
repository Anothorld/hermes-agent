# Agent 运行与 Gateway 审批

## 功能说明

- **Agent**：通过 Gateway 启动 Hermes run，登记 `run_id`，SSE 看 transcript，浮层 **Agent Session Dock** 多会话切换。
- **Gateway 审批**：YOLO/工具审批类提示，**Gateway Approval Dock** 全局浮层处理。

## 操作员路径

| 入口 | 组件/页面 |
|------|-----------|
| 活动页「启动 Agent」等 | `ProductDetailPage`, `KolDetailPage` |
| `/campaigns/:cid/transcript` | `AgentTranscriptPage.tsx`, `AgentTranscriptPanel.tsx` |
| 顶栏 **命令待批** 徽章 | `gateway-approval/GatewayApprovalNavBadge.tsx` |
| 全局浮层（右侧 amber 面板） | `components/agent-dock/*`, `gateway-approval/GatewayApprovalDock.tsx` |

当 Agent 的 `terminal` 命令命中 Hermes dangerous-command 规则时，gateway 会把 run 设为 `waiting_for_approval` 并发出 `approval.request` SSE 事件。Console 后端 watcher 订阅这些事件；若 SSE 漏帧（Console 重启、订阅竞态），每 5s 还会轮询 gateway run 状态做兜底。操作员在顶栏点 **命令待批** 或等浮层自动弹出，选择「本次允许 / 本会话允许 / 永久允许 / 拒绝」。

## 关键文件

| 层 | 文件 |
|----|------|
| FE | `agent-dock/`, `AgentTranscriptPanel.tsx`, `hooks/useGatewayApprovals.ts` |
| BE | `gateway_client.py`, `run_registry.py`, `gateway_approval_watcher.py`, `routers/campaigns.py`（stream）, `routers/gateway_approvals.py` |
| 配置 | `bridge_runtime.py`（bridge key 注入 gateway env） |

## 主要 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/campaigns/{id}/agent-stream` | SSE 日志 |
| GET/POST | `/gateway-approvals` | 列表/resolve |

## 关联模块

- [campaigns](../campaigns/GUIDE.md) — 启动参数、brief
- [nox](../nox/GUIDE.md) — `nox_dispatch.py` 组 brief
- [live-events](../live-events/GUIDE.md) — gateway 审批 WS 事件

## Run 防挂死（核心三层兜底，POVISON 686 修复）

为防止单个 run「永久 running」占住 gateway 槽位（686：浏览器只开了空 `about:blank` 标签页、run 卡 ~6 分钟才被手动停），核心侧加了三层兜底（均属受限 core，已评审改动）：

| 层 | 文件 | 行为 |
|----|------|------|
| **A 工具批次时钟** | `agent/tool_executor.py` | 并发工具批次有 wall-clock 上限（默认 1200s，`HERMES_TOOL_BATCH_TIMEOUT_S`）。超限即合成 timeout 工具结果、**停止 30s 心跳**、`shutdown(wait=False)` 放弃挂死 future，让 run 继续而非空转。心跳曾掩盖 gateway 回收，这是根因。 |
| **B 导航硬校验** | `tools/browser_tool.py` `browser_navigate` | `open` 报成功但仍停在 `about:blank`/空 → 判失败（`blank_tab_no_op`），让模型记 miss/换策略，不把空标签页当已加载页。 |
| **C 无进展看门狗** | `gateway/platforms/api_server.py` `_watchdog_stuck_runs` / `_stuck_run_ids` | 每 60s 扫描；`running` 且 `agent._last_activity_ts`（事件 **或** 工具心跳）超时无进展（默认 1800s，`HERMES_RUN_NO_PROGRESS_TIMEOUT_S`，0 关闭）→ interrupt + 置 `failed` + 发 `run.failed`。**跳过 `waiting_for_approval`**（合法等操作员）与终态/queued。 |

注：浏览器子进程本身有 60s 硬超时、CDP supervisor 10/15s 上限；A/B/C 是覆盖「工具内超时之外」的批次/导航/整 run 层兜底。长工具（构建、子代理）靠心跳保活，看门狗只杀真正静默的 run。

## 浏览器并发与「卡死」真因（POVISON 686/690 最终定位）

**真因不是 page-level CDP，也不是 tab-pool 设计** —— 实测并发 page-level daemon 导航均成功（~3s）。真因是 **`agent-browser` 没装成本地二进制**：`_find_agent_browser()` 回退到 `npx agent-browser`，而 npx **每次调用都重新安装** agent-browser（`not found and will be installed`，每次 ~3.5s）。一次发现要几十次 browser 调用 × 重装，并发 run 同时 npm 安装 → 锁/网络竞争 → 卡死。

### 修复

1. **装成真二进制**（关键）：`npm install -g agent-browser`（落在 gateway PATH 的 `~/.nvm/.../bin`）。`_find_agent_browser()` 优先取 PATH → 不再每次 npx 重装（`--version` 从 ~3.5s 降到 ~0.9s）。**这是浏览器可靠运行的前提，机器重置后需重装。**
2. **tab-pool 恢复**（每 run 独占页签、并发不串台）：`LOCAL_CHROME_TAB_POOL=1`，不设 `BROWSER_CDP_URL`。

### 两种互斥模式（由 `BROWSER_CDP_URL` 决定）

| 模式 | 配置 | 适用 | 隔离 |
|------|------|------|------|
| **tab-pool（推荐）** | `LOCAL_CHROME_TAB_POOL=1`，**不设** `BROWSER_CDP_URL` | 并发 run | 每 run 一个 page 页签，共享同一登录 Chrome；agent 自动起/复用 9222 |
| **共享连接** | `BROWSER_CDP_URL=http://127.0.0.1:9222`（**http 发现式**，非 ws GUID） | 单 run 串行 | 单页签共享，需串行 |

- `BROWSER_CDP_URL` 用 **`http://127.0.0.1:9222`**（不是 `ws://…/devtools/browser/<GUID>`）：GUID 每次启动会变、易失效；http 形式由 hermes 在连接时解析为实时 ws，Chrome 同端口重启后**无需 rebind、无需重启 gateway**。`start-debug-chrome.sh` 现在写 http 形式。
- 防互相踩：tab-pool 检测到外部 `BROWSER_CDP_URL`（http 或 browser-level ws）时**自动让位**；其 autostart 用 `DEBUG_CHROME_SKIP_ENV=1` 启动 Chrome、**不写** `BROWSER_CDP_URL`，避免下次重启误切到共享模式。
- tab-pool 关闭/让位时仍会 **autostart Chrome**（`hooks.py` 共享模式下也调 `ensure_chrome_running`），所以 agent 都能自主开浏览器。

并发发现仍建议串行（见 kols GUIDE）以降低 IG 风控/槽位压力。

## 「开了空标签页就卡死」的真正元凶（chrome_devtools MCP 指向死地址）

> 这是 686/690/694 反复卡死的**实际根因**。前面 A/B/C + agent-browser/tab-pool
> 全部针对内置 `browser_*` 路径，但卡死的 run 走的是**另一条路**。

`kol-orchestrator` profile 的 `config.yaml` 里曾挂着一个 `chrome_devtools` MCP server，
`--browserUrl http://10.30.80.118:9223`——一个早期 WSL/Windows 环境遗留的**远程不可达地址**。
每次 gateway 启动都会注册它的 **29 个 `mcp_chrome_devtools_*` 工具**，模型常优先选 `mcp_chrome_devtools_navigate_page`：

```
Tool mcp_chrome_devtools_navigate_page returned error:
  "Failed to fetch browser webSocket URL from
   http://10.30.80.118:9223/json/version: fetch failed"
```

撞 MCP `timeout: 180s` × 反复重试 + GLM 偶发空响应 → run「只开了空 `about:blank` 就卡几分钟」。
（tab-pool 在本地 9222 开的空标签页只是表象，真正的导航发去了那个死地址。）

### 根治

1. **删除死掉的 MCP server**（根因）：从 profile `config.yaml` 的 `mcp_servers` 移除 `chrome_devtools`，
   重启 gateway。重启后日志应为 `MCP: registered 6 tool(s) from 1 server(s)`（仅 `veedcrawl`），
   29 个 `mcp_chrome_devtools_*` 工具从工具集消失，模型只能走已验证可用的 `browser_*`。
   - **不要**改指向本地 9222：同一 CDP 端口不能被两个客户端共用（tab-pool 已占用），见 `skills/mcp/native-mcp/SKILL.md`。
2. **guard 纵深防御**（`plugins/kol-bridge-agent-guard/hooks.py`）：对所有 KOL session 拦截
   `mcp_chrome_devtools_*`。注意前缀用裸 `kol-campaign`（无冒号），否则 `kol-campaign-draft:` /
   `kol-campaign-outreach:` 会漏拦（曾导致 redraft run 仍能撞死 MCP）。
3. **运维提醒**：机器重置或重做 WSL 配置后，若再 `hermes mcp add chrome_devtools`，务必指向**可达**的 CDP；
   本机标准浏览器路径就是内置 `browser_*` + tab-pool，不需要 chrome-devtools MCP。

## 调试 Chrome「CDP 退化」也会卡空标签页（POVISON 694 第二层）

删掉死 MCP 后 694 仍卡空标签页——这次是**本地调试 Chrome 的 CDP 退化**：长时间运行（本例 3h）后，
Chrome 仍能回 `/json/version`（HTTP），但**任何 CDP WebSocket 升级都返回 `HTTP 500`**。
现象链：

- tab-pool 的 `probe_chrome()` 只看 HTTP `/json/version` → 误判 Chrome 健康 → 照常开 `about:blank` 页签；
- 但 `agent-browser` 连 page-level CDP ws 时拿到 500 → 网关侧 daemon 路径**卡住**（CLI 一次性调用则秒失败）；
- run 停在空标签页，要等 A/C 兜底 20–30 分钟才被回收。

### 修复（`plugins/local-chrome-tab-pool/internal/tab_pool.py`）

- 新增 `cdp_ws_healthy()`：用 stdlib `socket` 对 browser-level ws 做一次 RFC-6455 握手，只看状态行——
  `101`=健康，`500`/拒绝/超时=退化（不依赖 `websockets` 库）。
- `ensure_chrome_running()` 改为「HTTP 通 **且** CDP ws 健康」才放行；探测到退化（HTTP 通但 ws 不健康）
  即对启动脚本执行 **`restart`**（先杀掉坏实例再起），起完再校验 CDP 健康，仍不健康则报错。

### 关键运维约束（务必知道）

调试 Chrome 必须由**能长存的属主**启动：用户终端、launchd，或**网关进程内的 tab-pool autostart**
（`ensure_chrome_running` 在网关进程里 spawn，Chrome 成为网关后代而存活）。**不要**用一次性脚本/工具调用去起它——
那种子进程会随调用结束被进程组清理杀掉（只活几秒），正是「起来又没了」的原因。手动恢复：

```bash
playground/local-chrome-debug/start-debug-chrome.sh restart   # 在持久终端里
```

## Bridge CLI（所有 gateway run）

Console brief / instructions 注入 `bridge_agent_contract.py`：

- **`terminal_safety`**：绝对路径 `kol-bridge-cli`；禁止 bare `cd hermes-agent`、相对 `plugins/…`
- **stdout 错误契约**：CLI 失败输出 JSON 到 stdout；agent 若看到空 terminal + exit 2，应解析 stdout 的 `error`/`hint`，不得改用 `execute_code`（guard 会拦）

详见 `agent_prj/docs/kol-bridge-agent-tooling.md`。

## 注意

Agent 状态 **不在** SQLite；以 Gateway + `run_registry` 为准。
