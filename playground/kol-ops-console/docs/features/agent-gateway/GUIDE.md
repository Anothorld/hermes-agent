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
| FE | `agent-dock/`, `AgentTranscriptPanel.tsx`, `GatewayApprovalProvider.tsx`, `hooks/useGatewayApprovals.ts` |
| BE | `gateway_client.py`, `run_registry.py`, `gateway_approval_watcher.py`, `routers/campaigns.py`（stream）, `routers/gateway_approvals.py` |
| 配置 | `bridge_runtime.py`（bridge key 注入 gateway env） |

## 主要 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/campaigns/{id}/agent-stream` | SSE 日志（并行代理上限 `KOC_AGENT_STREAM_MAX_RUNS`，默认 10） |
| GET | `/campaigns/run-launch-status` | 启动队列深度（Dock「排队 N」） |
| GET | `/campaigns/launch-jobs/{job_id}` | 轮询 202 异步 launch（campaign / rediscover / discover-email）；FE 用 `lib/launchJobs.ts` |
| GET | `/admin/perf-snapshot` | 运行时指标（owner/operator） |
| GET/POST | `/gateway-approvals` | 列表/resolve（FE 经 `GatewayApprovalProvider` 全应用只拉一次 snapshot） |

## Agent 爆发期：队列 + 排水

Gateway 硬顶为 **10 并发 run**。Console 通过 `run_launch_queue` 将有效 inflight 压在
`KOC_GATEWAY_LAUNCH_MAX_INFLIGHT`（默认 8）以下，并对 `kol-email-discover:*` **全局串行**
（`max_inflight=1`）。

每次 `start_run` 成功后，`gateway_client.ensure_run_drained(run_id)` 在后台消费
终态 SSE，帮助 Gateway 尽快释放槽位（幂等，每 run 至多一条 drain 任务）。

操作员在 **Agent Session Dock** 看到「排队 N」时表示有启动请求在等槽位，无需重复点击。
详见 [performance](../performance/GUIDE.md)。

Approval watcher 在 open runs >5 时自动切换 `poll_aggregate`，减少上游 SSE 长连数量
（`KOC_APPROVAL_WATCH_MODE=auto`）。

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
   - **`kol-email-discover:*` 额外拦截**：`veedcrawl_*`（视频/Profile 补充工具，非邮箱发现）、
     `delegate_task`（子代理空参误调 veedcrawl 浪费 iteration）、`execute_code` 浏览器绕过、
     `terminal` DuckDuckGo/HTML 抓取。Tier 2 只允许内置 `browser_*`。
3. **Draft / redraft session 隔离**（2026-06 token 优化）：单 KOL 起草/跟进/精修 run 使用
   `kol-campaign-draft:{env}:{campaign_id}:{identity_id}`，避免同活动多 KOL 共用长 transcript。
   Agent Dock 仍按 `kol-campaign-draft:` 前缀着色。
4. **Bridge dispatch 读取**：gateway brief / SKILL Procedure 使用
   `get-dispatch-context --view agent`（compact JSON；省略 lanes，嵌入 identity）。
   - **`kol-campaign:*` 发现 run 额外拦截 `delegate_task`**（2026-06-08 SEB8010）：
     模型曾把「公网搜 150 handles」整包 `delegate_task` 出去，子代理空参循环 `veedcrawl_*`
     且 LLM 挂起，父 run 同步卡在 `delegate_task` 数分钟。发现必须在**当前 run** 用
     `browser_*` + CAL 持久化；数量不足靠 Console `/rediscover` 自动重试，不靠子代理。
5. **运维提醒**：机器重置或重做 WSL 配置后，若再 `hermes mcp add chrome_devtools`，务必指向**可达**的 CDP；
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

## 第三层：`cloud_provider: browser-use` 在国内机器上无限挂死（POVISON 701）

删死 MCP、修 CDP 退化后，701 仍卡空标签页。`sample` 网关线程拿不到 Python 帧，但证据收敛到
**profile `config.yaml` 的 `browser.cloud_provider: browser-use`**（`cdp_endpoint: wss://cloud.browser-use.com/v1`）：

- 本机在国内（LLM 走国内 IP），而 **Browser-Use 是美国云**；
- `browser_navigate` 一旦回落到 `BrowserUseProvider.create_session()`（`tools/browser_tool.py` `_get_session_info` 的云分支），
  就发起到美国云的网络调用，**这条路径没有超时 → 无限阻塞**；
- 现象与之吻合：卡死在 `_get_session_info`、**没有 spawn 任何 agent-browser 子进程、没有创建 socket 目录**。

### 修复（运维配置）

把 `browser.cloud_provider` 改为 **`local`**：

```yaml
browser:
  cloud_provider: local   # 原 browser-use；本机有本地调试 Chrome，不该走美国云
```

- 命中 tab-pool seed → page-level CDP 驱动本地调试 Chrome（健康时实测 ~0.3s）；
- 万一没命中 → `_create_local_session`（agent-browser 自带 Chromium，受 `command_timeout: 120` 约束，
  缺 Chromium 则秒失败）——**无论如何不再有无超时的云调用**。
- 改完需**重启网关**生效。

> 系统性加固建议（核心受限区，需批准）：在 `tools/browser_tool.py` 的云 `create_session()` 外层加硬超时，
> 这样即便日后误把 `cloud_provider` 切回云端，也不会再无限挂死。

## 第四层：错误的工具名让模型弃用 browser/web 工具（POVISON 701）

空标签页不卡了，但模型**通篇用 `terminal` + `python urllib`/`curl` 抓网页**，嘴上说
`browser_navigate` 却从不真正调用。根因不是工具缺失（`browser_*`、`web_search`、
`web_extract` 都已在 api_server toolset 注册），而是 **skill / brief 引用了不存在的工具名**：

- `kol-email-discovery/SKILL.md` 与 `kols.py` 的 brief 写的是 `WebSearch` / `WebFetch` /
  `GoogleSearch`；
- 系统里**根本没有这些工具**，真实名是 `web_search`（搜索）/ `web_extract`（取页）；
- 模型找不到这些名字 → 它甚至去试 `kol-bridge-cli web-search`（报 `invalid choice`）→
  退而用 `terminal` urllib/curl 自己抓 beacons/bio.link/Instagram，整个流程退化成「全 terminal」，
  Tier 2 的 `browser_navigate` 也只剩口头。

### 修复

1. **改对工具名**：skill + brief 全部 `WebSearch/WebFetch/GoogleSearch` → `web_search`/`web_extract`，
   并显式声明「没有 WebSearch/WebFetch，找不到搜索工具就是用 `web_search`，不要退回 terminal HTTP」。
2. **guard 扩展**（`kol-bridge-agent-guard`）：对 `kol-email-discover:*` 拦 `terminal` 里的
   `curl`/`wget`/`urllib`/`requests`/`httpx` 取页与搜索引擎/link-in-bio HTML 抓取，提示改用
   `web_search`/`web_extract`/`browser_navigate`。
3. **skills_sync（已修）**：`tools/skills_sync.py` 曾把 `SKILLS_DIR`/`MANIFEST_FILE` 在 import 时绑定首个
   `HERMES_HOME`，导致 `playground/learning/sync_skills.py` 循环改 `HERMES_HOME` 时 profile 目录不同步。
   现已改为每次调用时通过 `get_hermes_home()` 解析路径。同步后 agent 的 `skill_view` 读
   `~/.hermes/profiles/kol-orchestrator/skills/**`（或当前 profile 的 skills 目录）。

> 经验法则：skill/brief 里出现的每个工具名都必须与运行时真实工具名**逐字一致**；名字错了模型不会报错，
> 而是悄悄改用 terminal/execute_code 等通用工具绕路。

## 第五层：browser_* / web_* 工具被可用性检查整组剔除（POVISON 701 终因）

改对工具名后，模型直接说「我的 toolset 里没有 browser_navigate」——而且是真的。用 agent 的真实
`get_tool_definitions(enabled_toolsets=api_server)` 复现，发现 toolset「已启用」但**最终工具表里
browser_\* 和 web_\* 全被剔除**。原因是每个工具的 `check_fn` 返回 False：

- `tools/browser_tool.py::check_browser_requirements()` → False：local 模式下它只认
  ① `BROWSER_CDP_URL`（tab-pool 模式不设）② cloud provider（已设 `local`）③ agent-browser **自带
  Chromium**（`_chromium_installed()` 为 False——我们用的是真实 debug Chrome 走 CDP，从没装自带 Chromium）。
  **它完全不认 tab-pool**，于是判 browser 不可用 → 整组 `browser_*` 从工具表消失。
- `tools/web_tools.py::check_web_api_key()` → False：exa/tavily/firecrawl/searxng/brave-free/**ddgs**
  没有任何一个后端可用 → `web_search`/`web_extract` 也被剔除。

结果：模型既没有 Tier 2 的 `browser_*`，也没有 Tier 1 的 `web_*`，只能退回 `terminal` urllib/curl。
（前几层修的工具名/guard 都对，但工具压根没被暴露，是更底层的原因。）

### 修复

1. **browser 可用性认 tab-pool**（核心 `check_browser_requirements`，已评审）：local 模式下新增
   `_local_debug_chrome_available()` —— ① debug Chrome 已在线（`_probe_local_cdp()`）或 ② 启动脚本
   `start-debug-chrome.sh` 存在且 `LOCAL_CHROME_TAB_POOL` 未显式关闭 → 判可用（tab-pool 会在首个
   `browser_*` 调用时自启 Chrome 并 seed page-level CDP）。普通安装（无脚本、无 Chromium）行为不变。
2. **web 后端**：给 **gateway 实际使用的 framework Python 3.14** 装 `ddgs`（DuckDuckGo，无需 API key），
   使 `web_search`/`web_extract` 可用。验证：`get_tool_definitions` 现在含 `browser_navigate`、
   `browser_snapshot`、`web_search`、`web_extract`。

> 注意：gateway 跑的是系统 framework Python（**不是** repo venv），依赖要装到那个解释器。
> 关键自检法：用 `get_tool_definitions(enabled_toolsets=_get_platform_tools(cfg,'api_server'))` 打印
> 「Final tool selection」，确认目标工具真的在最终表里——「toolset 已启用」不等于「工具已暴露」。
>
> **check 顺序**：`check_browser_requirements()` 必须在 cloud-provider 分支**之前**检查
> `_local_debug_chrome_available()`。否则 default profile 里残留的 `browser-use`（未配置 API key）
> 会让 `provider.is_configured()` 返回 False 并提前退出，即使 kol-orchestrator profile 已设
> `cloud_provider: local` 且 tab-pool 可用。

## 第六层：tab-pool seed 死锁（POVISON 701 空 tab 后永久卡住）

日志表现为：`Tab pool acquired tab …` 之后**再也没有** `tool browser_navigate completed`，run 一直
running 直到 gateway 重启。根因是 `local-chrome-tab-pool/hooks.py::_seed_browser_session` 在持有
`browser_tool._cleanup_lock` 时调用了 `_update_session_activity()`（它也会抢同一把非可重入锁）→
**死锁**。tab 已在 Chrome 里打开（about:blank），但 browser 工具 handler 永远进不去。

### 修复

在锁内直接写 `_session_last_activity[session_key] = time.time()`，不再嵌套调用
`_update_session_activity()`。

## 第七层：Tier 1 改为本地 Chrome Google（非 web_search）

**策略变更（2026-06）：** `kol-email-discovery` Tier 1 不再使用 `web_search` / `web_extract`。
Google 搜索与结果页抓取统一走 **本地 debug Chrome**：

- `browser_navigate` → `https://www.google.com/search?q=<encoded_query>`
- `browser_snapshot` 读 SERP → 打开 creator-owned 结果 URL（同样 `browser_*`）
- Tier 2 仍为 JS 页面（Instagram bio、Linktree/Beacons 懒加载）

**guard：** `kol-bridge-agent-guard` 对 `kol-email-discover:*` 拦截 `web_search`/`web_extract` 以及
terminal HTTP 抓取，提示改用 `browser_navigate` + Google URL。

**Nox Gate B：** 仍由 Console `discover-email` 在 gateway 前同步跑（需 `campaign_id` + LIVE +
`nox_quota_enabled`）。brief 带 `gate_b_attempted: true` 时 gateway 不再重复 Nox。详见
[kols GUIDE §Nox Gate B](../kols/GUIDE.md)。

## Bridge CLI（所有 gateway run）

Console brief / instructions 注入 `bridge_agent_contract.py`（经 `bridge_agent_contract_loader`，默认带 `hermes-agent` 绝对路径）：

- **`terminal_safety`** + **`approval_cli_checklist`** / **`resume_cli_checklist`**：同一绝对路径 `python3 -u .../kol_bridge_tool.py`；禁止对 `kol-bridge-cli` 套 `python3`、禁止相对 `plugins/…`、禁止对 get-* 读命令使用 `>` 重定向（会导致 terminal 空 stdout）
- **stdout 错误契约**：CLI 失败输出 JSON 到 stdout；agent 若看到空 terminal + exit 2，应解析 stdout 的 `error`/`hint`，不得改用 `execute_code`（guard 会拦）

详见 `agent_prj/docs/kol-bridge-agent-tooling.md`。

## 注意

Agent 状态 **不在** SQLite；以 Gateway + `run_registry` 为准。
