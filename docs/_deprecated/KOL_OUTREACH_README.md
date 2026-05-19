> ⚠️ **DEPRECATED — v1.1 Kanban + review-gate pattern. DO NOT FOLLOW.**
> Superseded by `hermes-agent/skills/social-media/kol-outreach-orchestrator-flow/SKILL.md` and its `SETUP.md`.
> Do **not** recreate any task described below (no `campaign anchor`, no `review campaign brief and assumptions`, no `review creator shortlist`, no `safety / mode config`, no `kol-scout` assignee). Kanban cards under the new design are per-KOL indexes only (`title: kol:<handle>`). Kept for history.

# KOL Outreach Orchestrator — 使用文档（v1.1.0）

> 这套流程把"一个商品 brief → 一组人工审核完毕的 KOL 邀约"自动化掉，落地在 Hermes Kanban 上。
> 当前文档对应 skill `kol-outreach-orchestrator` v1.1.0，使用 **anchor + review-gate 双卡模式**。
> Skill 路径：`~/.hermes/profiles/kol-orchestrator/skills/social-media/kol-outreach-orchestrator/SKILL.md`

---

## 0. 我应该读哪段？

| 你想做的事 | 看这一节 |
|---|---|
| 第一次跑这个流程 | §1 概念 + §2 角色 + §3 一次性配置 |
| 启动一个新 campaign | §4 启动新 campaign |
| 当前 TS8125 campaign 怎么往下走 | §5 推进当前 campaign |
| **触发 Layer B（建单达人 pipeline）** | **§5.5 `APPROVE:` 协议** |
| 看懂任务图、状态机、哪里卡住了 | §6 流程结构 + §7 状态机 |
| 切换正式发送（LIVE） | §8 LIVE MODE 切换 |
| 出错了 / 想重来 | §9 故障恢复 |
| 速查命令 | §10 命令速查 |

---

## 1. 核心概念

### 双层任务图

- **Layer A — Campaign 层**：商品 brief、达人 shortlist 发现、人工审 brief、人工审 shortlist。**5 张卡，campaign 启动时全部一次性创建。**
- **Layer B — 单达人 pipeline 层**：每个被批准的达人一条独立流水线（10 张卡）：调研 → 审 → 找邮箱 → 审 → 草稿 → 拟人化 → 审 → 发信 → 检查回信 → 审下一步。**只在某个达人被批准后才创建。**
- **事件驱动支线**：谈价、寄品、收视频。**只在收到真实事件时一张一张地创建**，永不预建。

### Anchor + Review Gate 双卡模式（v1.1 新设计）

旧版 v1.0 把"商品摘要"和"商品摘要审核"塞同一张卡（root），导致 unblock 后变 ready 却不会变 done，下游永远卡住。v1.1 把它们拆开：

| 卡类型 | assignee | 创建后状态 | 角色 |
|---|---|---|---|
| **anchor** | 无 | `done`（自动完成） | 纯数据锚点。所有信息都写在它的 body 里。永远不阻塞。 |
| **safety** | 无 | `done`（自动完成） | 安全/模式信息卡。永远不阻塞。 |
| **review gate** | 无 | `blocked`（人工解锁） | 唯一的人工审核卡。只做一件事：等人工 unblock + complete。 |
| **worker card** | profile 名 | `todo` → `ready` → `running` → `done` | 实际执行任务的卡。 |

**铁律：anchor 永不 block；想做审核就建一张独立 review 卡。**

### 人工审核点（这套流程里你必须出手的地方）

Layer A 有 2 个 gate，每个被批准达人的 Layer B 有 4 个 gate：

```
Layer A:
  Gate 1: T_brief_review        → 你审商品摘要 + 假设
  Gate 2: T_shortlist_review    → 你审达人名单（comment 里给出推进 handle 列表）

Layer B (per creator):
  Gate 3: T_research_review     → 你审单个达人的深度调研
  Gate 4: T_email_review        → 你审找到的邮箱（来源/可信度）
  Gate 5: T_review_initial      → 你审初邀邮件最终稿（拟人化后）
  Gate 6: T_review_next         → 收到回信后，你决定下一步策略
```

每个 gate 的解锁动作都一样：

```bash
hermes kanban unblock <id>
hermes kanban complete <id>
```

如果你想否决：

```bash
hermes kanban comment <id> "原因：xxx"
# 然后让 orchestrator 重做上一步
```

---

## 2. 角色（Profiles）

3 个 profile 协作，全部都是 Hermes 的子 profile：

| Profile | 职责 | 谁来跑 |
|---|---|---|
| **kol-orchestrator** | 你正在用的这个。负责分解任务图、把事情挂到 Kanban、监控状态、给你做汇报。**不发邮件、不抓数据。** | 主对话窗口 |
| **kol-scout** | 跑 Instagram 数据：discovery / 单达人调研 / 找邮箱 | dispatcher 自动 spawn |
| **outreach-operator** | 起草、humanizer、发信、检查回信、收视频。用 Google Workspace（Gmail）。 | dispatcher 自动 spawn |

review gate 的 assignee 永远是空，所以 dispatcher 不会 spawn 任何人——人工是它的"worker"。

---

## 3. 一次性配置

只在第一次跑、或者机器换了之后做一次：

### 3.1 确认 profile 都在

```bash
hermes profile list
# 必须看到 kol-orchestrator / kol-scout / outreach-operator
```

如果有缺失，看 `hermes profile setup` 文档。

### 3.2 确认 board

```bash
hermes kanban boards list
# 推荐使用 kol-outreach board
hermes kanban boards switch kol-outreach
```

如果不存在：

```bash
hermes kanban boards create kol-outreach --name "KOL Outreach"
hermes kanban boards switch kol-outreach
```

### 3.3 Google Workspace token

outreach-operator 用 Gmail 发信和读信。**每次开 campaign 之前**最好刷新一下 token：

```bash
gws gmail send --to <you> --subject "test" --body "ping"
# 如果 401/403，按工作流文档跑 oauth 重新授权
```

token 坏了的话会在 `T_send_initial` 这张卡上失败，但 dispatcher 会把卡设为 blocked 并 comment 报错，所以不会静默失败。

### 3.4 测试邮箱

TEST MODE 下所有发件都收件方都是 **`[REDACTED]`**。如果你想换测试邮箱，在 anchor 卡的 body 里改 `test_mailbox` 字段，并通知 outreach-operator 刷新这个值。

---

## 4. 启动一个新 campaign

把这段当成模板用。改商品摘要、改 slug、把 anchor body 写满。

### 4.1 把 brief 喂给 orchestrator

最简单：直接在主对话里贴产品文档（markdown 也行），并告诉 orchestrator："用 kol-outreach-orchestrator skill 启动 campaign，slug 用 xxx"。

orchestrator 会：
1. Load `kanban-orchestrator` 和 `kol-outreach-orchestrator` 两个 skill
2. 跑 Step 0 preflight（检查 profile / board / token）
3. 解析 brief，把模糊点写进 `assumptions`
4. 创建 5 张 Layer A 卡（按下面的图）
5. 自动 complete anchor 和 safety
6. 自动 block brief_review（gate 1）
7. 把 ID 列表汇报给你，让你审 brief

### 4.2 5 张 Layer A 卡的样子

```
T_anchor          [done]      campaign anchor — 数据锚点
  ├── T_brief_review  [blocked]   review campaign brief & assumptions   ← Gate 1
  │     └── T_discovery  [todo]   shortlist creator discovery (kol-scout)
  │           └── T_shortlist_review [todo]  review creator shortlist  ← Gate 2 (parent 还没 done 所以是 todo)
  └── T_safety      [done]      safety / mode config — 数据锚点
```

> **Gate 2 (`T_shortlist_review`) 创建时为什么是 `todo` 而不是 `blocked`？**
> Kanban 状态机只有当卡的 parent 已 `done` 时才能从 `todo` 进 `blocked`。Gate 2 的 parent (`T_discovery`) 还在 todo，所以 Gate 2 也只能在 todo。等 discovery 跑完变 done，orchestrator 应当立刻把 Gate 2 改成 blocked。这种"延迟 block"的写法是 v1.1 的标准做法（详见 SOP "State-machine note on `block` timing"）。

### 4.3 orchestrator 的下一步是什么？

它会汇报给你（中文），然后停下来等你审 brief。它不会自动往下走。

---

## 5. 推进当前 TS8125 campaign

当前看板状态（slug = `ts8125`，board = `kol-outreach`）：

| 卡 | ID | 状态 | 下一步 |
|---|---|---|---|
| T_anchor | `t_f24b0817` | done | — |
| T_brief_review | `t_4fe14b4f` | **blocked** | **你审 brief，然后 unblock + complete** |
| T_discovery | `t_5d301990` | todo (kol-scout) | brief_review done 后自动 ready，dispatcher 自动 spawn kol-scout |
| T_shortlist_review | `t_956aee46` | todo | discovery done 后由 orchestrator 改成 blocked，等你审 |
| T_safety | `t_a8d44a72` | done | — |

### 5.1 当前你需要做的事（gate 1 — brief 审核）

打开 brief_review，看里面的内容：

```bash
hermes kanban show t_4fe14b4f
```

读 anchor 卡的 body（商品摘要在那里）：

```bash
hermes kanban show t_f24b0817
```

#### 当前 anchor 中明确的 assumptions（必须由你确认或修正）

- `product_url` 未提供（源文件里没有）
- `budget_per_creator` 未提供
- `buyer_persona = 25-44 岁北美城市家庭/新婚夫妇/公寓与独立屋住户`（推断）
- `primary_purchase_driver = A+B+C`（审美 + 收纳 + 即开即用）（推断）
- `key_features` 含一条："现有素材仅 1 套可用，需重点评估达人二创能力"

#### 同意 brief（→ 启动 discovery）

```bash
hermes kanban unblock t_4fe14b4f
hermes kanban complete t_4fe14b4f
hermes kanban dispatch       # 让调度器立即把 t_5d301990 spawn 到 kol-scout
```

执行后 `t_5d301990` 应该变成 `running` 或 `ready`。可以用：

```bash
hermes kanban show t_5d301990
hermes kanban log t_5d301990 -f    # 实时看 kol-scout 的输出
```

#### 拒绝 / 修正 brief

```bash
# 在 brief_review 卡上 comment 你想改的点，举例：
hermes kanban comment t_4fe14b4f "请把 budget_per_creator 设为 \$200-400；buyer_persona 改成 30-40 岁有娃家庭"
```

然后告诉主对话里的 orchestrator："按 brief_review 的 comment 更新 anchor，然后保持 brief_review 在 blocked，等我重审"。orchestrator 会用 `hermes kanban comment` 在 anchor 上记录变更（anchor 的 body 不可变，但 comment 是可加的）。

### 5.2 Discovery 跑完之后（gate 2 — shortlist 审核）

orchestrator 会做：

1. 把 shortlist_review (`t_956aee46`) 从 `todo` 改成 `blocked`
2. 把 discovery 输出的达人名单（5-10 个）作为 comment 贴在 shortlist_review 上
3. 通知你审

你的动作：

```bash
hermes kanban show t_956aee46
# 读它最近的 comment，里面应该有结构化的达人名单
```

审完后**必须**用结构化 comment 触发 Layer B（`APPROVE:` 协议，详见 §5.5）：

```bash
hermes kanban comment t_956aee46 "APPROVE: @handle_a, @handle_b, @handle_c"
hermes kanban unblock t_956aee46
hermes kanban complete t_956aee46
# 然后回主对话说 "build Layer B"
```

orchestrator 会读到 `APPROVE:` 那行，为每个 handle 起一条 Layer B 流水线（10 张卡）。

### 5.3 Layer B 流水线（每个达人）

```
T_research                [kol-scout]    parent: T_shortlist_review
  └── T_research_review   [blocked]      ← Gate 3
        └── T_find_email  [kol-scout]
              └── T_email_review [blocked] ← Gate 4
                    └── T_draft_initial      [outreach-operator]
                          └── T_humanize_initial [outreach-operator + humanizer skill]
                                └── T_review_initial [blocked] ← Gate 5
                                      └── T_send_initial [outreach-operator]
                                            └── T_check_reply [outreach-operator]
                                                  └── T_review_next [blocked] ← Gate 6
```

每过一个 gate 都用同样的命令对：

```bash
hermes kanban unblock <id>
hermes kanban complete <id>
```

否决时用 `comment` 给反馈。

### 5.4 Gate 6 之后（收到回信）

收到达人真实回信后，你决定方向：

| 你说 | orchestrator 做什么 |
|---|---|
| "继续谈价 \$X" | 创建一组 4-5 张谈判卡（summarize → review → draft → humanize → review → send） |
| "寄样品" | 创建寄品确认卡（draft product-share email → review → send） |
| "结案，让对方拍视频" | 创建 final-delivery 跟进卡 |
| "Pass，找下一个" | 把 `T_review_next` complete，pipeline 终止 |

**永远一张一张地创建。** 不预建 3 轮谈价。

### 5.5 Layer B 触发协议（`APPROVE:` 约定）

**为什么需要协议**：Hermes Kanban 没有"卡 done 自动回调"的钩子机制。`T_shortlist_review` 完成后，orchestrator 只在你下一次回主对话时才会接手，需要一个明确的、机器可解析的信号告诉它"用谁、怎么做"。

**协议定义**：在 `T_shortlist_review` 卡的 comment 里写一行**严格**以 `APPROVE:` 开头（大小写敏感）：

```
APPROVE: @handle_a, @handle_b, @handle_c
```

**可选的同一条 comment 后续行**（覆盖整批 Layer B 默认值）：

```
APPROVE: @handle_a, @handle_b
ANGLE: 拍摄角度 / 视频概念，例：dusk lighting / cozy dad-cave makeover
BUDGET: $250-400 per creator
NOTES: 任何想让 orchestrator 在调研、起稿时考虑的 free-form 说明
```

**完整 6 步操作**：

```bash
# 1. 读 discovery 给的达人名单
hermes kanban show t_956aee46

# 2. 写 APPROVE comment（一条 comment 多行也行）
hermes kanban comment t_956aee46 "APPROVE: @handle_a, @handle_b, @handle_c
ANGLE: 客厅夜景 + LED 灯带氛围
BUDGET: \$300-500
NOTES: 优先 home decor 真实用户，避开纯设计师账号"

# 3. 解锁 + 完成
hermes kanban unblock t_956aee46
hermes kanban complete t_956aee46

# 4. 回主对话告诉 orchestrator
# > build Layer B

# 5. orchestrator 解析 comment、为每个 handle 建 10 张卡 + 4 个 review gate
# 6. orchestrator 自动 hermes kanban dispatch，kol-scout 立即开跑第一张 research 卡
```

**orchestrator 解析规则**（你可以预期）：

| 规则 | 行为 |
|---|---|
| 没有 `APPROVE:` 行 | orchestrator 不猜，直接问你要 |
| `APPROVE:` 里 handle 不在 discovery 输出名单里 | orchestrator 在建卡前确认 |
| 有 `ANGLE:` / `BUDGET:` / `NOTES:` | 这些值会被注入每个达人的 research / draft 卡 body 里 |
| 多次 `APPROVE:` comment（增量批准） | 只为还没建过 pipeline 的 handle 建新卡 |
| 想撤回某个达人 | block 该达人的 `T_research`，理由 `removed per user request`；不要删卡（保留审计） |

**简化版**（最常用的最小指令）：

```bash
hermes kanban comment t_956aee46 "APPROVE: @h1, @h2"
hermes kanban unblock t_956aee46 && hermes kanban complete t_956aee46
# 回主对话："build Layer B"
```

**协议设计权衡**：
- 严格前缀避免聊天里随口说的"批准 xxx"被误解析
- comment 形式不需要新工具，命令行普通 `kanban comment` 即可
- 可选字段（ANGLE/BUDGET/NOTES）让你不用单独配 yaml 也能精细化指挥

---

## 6. 流程结构 — 完整任务图

```
[Layer A]                                                                    
T_anchor (done)                                                              
  ├── T_brief_review (blocked → done)                                        
  │     └── T_discovery (todo → ready → running → done)                      
  │           └── T_shortlist_review (todo → blocked → done)                 
  │                 ├── T_research [creator A]                               
  │                 │     └── T_research_review                              
  │                 │           └── T_find_email                             
  │                 │                 └── T_email_review                     
  │                 │                       └── T_draft_initial              
  │                 │                             └── T_humanize_initial     
  │                 │                                   └── T_review_initial 
  │                 │                                         └── T_send_initial
  │                 │                                               └── T_check_reply
  │                 │                                                     └── T_review_next
  │                 ├── T_research [creator B] ...                          
  │                 └── T_research [creator C] ...                          
  └── T_safety (done)                                                        
```

---

## 7. 状态机

Hermes Kanban 的状态：

```
              ┌──────────────────── unblock ─────────┐
              │                                       │
   triage → todo ←→ ready → running → done            │
              │      │           │                    │
              │      └─ block ───┴── block ─→ blocked ┘
              │                                       │
              └────────── ─── ─── block ───────── ────┘
```

#### 关键规则

1. **`todo` → `ready`** 自动发生当 **所有 parents 都为 `done`**。
2. **`ready` → `blocked`** 通过 `hermes kanban block <id> "reason"`。
3. **`blocked` → `ready`** 通过 `hermes kanban unblock <id>`。
4. **`ready` → `running`** 当 dispatcher 给一个 worker profile 派活。**只有有 assignee 的卡才会被 spawn。**
5. **`running` → `done`** 当 worker 主动调用 `kanban_complete`，或人工 `hermes kanban complete <id>`。
6. **`todo` 状态上调用 block** 只产生 comment，状态不变（这是 v1.1 SOP 中"延迟 block"的根因）。

#### 反直觉点

- **anchor unblock 不会变 done**——v1.0 的 bug 就是把 anchor block 起来，然后 unblock 后期望它"完成"，结果它只是变 ready，dispatcher 又不能 spawn（无 assignee），永远卡住。
- 对策（v1.1）：anchor 用 `complete` 而不是 `block`，gate 用独立 review 卡。

---

## 8. LIVE MODE 切换

**默认永远是 TEST MODE**，TEST 收件人 `[REDACTED]`。

### 切换前必检

1. 已经跑通至少一次完整 TEST MODE pipeline 端到端
2. 该达人的 `T_find_email` 已 done，且邮箱**有可验证来源**（IG bio / link-in-bio / 公开商务邮箱），不是 pattern 猜的
3. Google Workspace token 当天能正常发信
4. 预算和达人仍在批准范围内

### 三种切法

**Option 1 — 单达人切换（首次实战推荐）**

只对你点名的达人切，其他 pipeline 仍在 TEST：

- 在 anchor body 里加注释：`Switched <handle> to LIVE on <date>`
- 该达人的 send 卡 body 改：
  - `recipient: <real_verified_email>`（来源也写明）
  - subject 去掉 `[TEST MODE]` 前缀
  - 安全行改：`SAFETY: LIVE MODE — verified <email> from <source> on <date>. Approved at T_review_initial.`

**Option 2 — campaign-wide 切换**

更新 anchor 的 mode 字段为 `LIVE`，并 comment 记录。**之后**新建的所有 pipeline 默认 LIVE，已在跑的保持原 mode 不动。

**Option 3 — 紧急回退**

```bash
# 凡是涉及发信的卡，全部 block 掉
hermes kanban block <send_id> "rollback to TEST per user"
# 然后回到 anchor 改 mode 为 TEST
```

### LIVE 模式的额外铁律

1. send 卡 body 必须包含 verified email + 来源 + 日期 + 审批 ID
2. send 卡的 review parent 必须显式标 LIVE 已审
3. outreach-operator 发信工具必须从 card body 读 recipient，**不允许**从配置 / 缓存 / 推理来源读
4. LIVE send 失败（auth / bounce / 拒绝）**绝不静默重试**——直接 block + 创建审核卡让你决定

---

## 9. 故障恢复

### worker 卡死或挂了

```bash
hermes kanban reclaim <task_id>                        # 回到 ready，等下次 dispatch
hermes kanban reassign <task_id> <new-profile> --reclaim  # 换 profile 重跑
```

### worker 报错 `created_cards` 里有不存在的 ID

`kanban_complete` 会拒绝完成。修正 worker 输出后重跑。

### 看 worker 日志

```bash
hermes kanban log <task_id>           # 静态查看
hermes kanban log <task_id> -f        # 跟踪
```

### 板子搞乱了，想推倒重来

```bash
hermes kanban boards rm kol-outreach --delete   # 硬删
hermes kanban boards create kol-outreach --name "KOL Outreach"
hermes kanban boards switch kol-outreach
# 然后让 orchestrator 重新跑 Step 0 + 创建 Layer A
```

### 误把 review 卡给了 assignee

只能：删卡（如果它还没 ready）+ 重建。或者在 dashboard 里清空 assignee 字段。

### 在 LIVE 中发现 send 卡没 review parent（严重 bug）

立即 block 该 send 卡。补一张 review 卡作为它的 parent，审完再 unblock。

---

## 10. 命令速查

| 操作 | 命令 |
|---|---|
| 看当前 board 所有卡 | `hermes kanban list` |
| 看单卡详情 | `hermes kanban show <id>` |
| 看单卡 worker 日志 | `hermes kanban log <id> [-f]` |
| 看板状态分布 | `hermes kanban stats` |
| 切换 board | `hermes kanban boards switch <slug>` |
| 创建卡 | `hermes kanban create "<title>" --body "..." --assignee <profile> --parent <id> --json` |
| 加 comment | `hermes kanban comment <id> "<text>"` |
| 阻塞（gate） | `hermes kanban block <id> "<reason>"` |
| 解除阻塞 | `hermes kanban unblock <id>` |
| 完成 | `hermes kanban complete <id>` |
| 让 dispatcher 立刻派活 | `hermes kanban dispatch [--json]` |
| 回收 worker | `hermes kanban reclaim <id>` |
| 换 assignee | `hermes kanban reassign <id> <new-profile> [--reclaim]` |

### 一些常用组合

**审完一个 gate（标准）**

```bash
hermes kanban unblock <id> && hermes kanban complete <id> && hermes kanban dispatch
```

**否决一个 gate**

```bash
hermes kanban comment <id> "拒绝原因 + 你希望怎么改"
# 主对话告诉 orchestrator 重做上一步
```

**当前 TS8125 推进 gate 1**

```bash
hermes kanban show t_f24b0817        # 先读商品摘要 (anchor)
hermes kanban show t_4fe14b4f        # 再读 brief_review checklist
# 同意：
hermes kanban unblock t_4fe14b4f && hermes kanban complete t_4fe14b4f && hermes kanban dispatch
```

---

## 11. 设计回顾（为什么是这样）

### v1.0 → v1.1 的痛点

| 问题 | v1.0 行为 | v1.1 修复 |
|---|---|---|
| root unblock 后无人 spawn | root 是 anchor + gate 二合一，unblock 后 ready 但永不 done | 拆 anchor (auto-done) + brief_review (blocked) |
| 干脆 complete root 又能跑 | 但 SOP 里 root 是"campaign 标识"，complete 它感觉不对 | anchor 设计上就是要立即 complete，定义清晰 |
| review 节点能否复用模式？ | 没有 explicit pattern，每次都靠记忆 | hard rule: 每个 review 独立卡 + 独立 block |

### v1.1 的核心约定

1. **数据 vs 控制分离**：anchor/safety 卡只存数据，review 卡只控制流程。
2. **每个 gate 是一张卡**：你审什么，看哪张卡的 body 和 comment。审批日志天然结构化。
3. **dispatcher 不会被 review 卡误派活**：assignee 为空，自动跳过。
4. **block 是临时操作不是结构**：parent 关系才是结构。block 加在 review 卡的"已 ready"瞬间。

---

## 12. 改了 skill 想看 diff

Skill 文件路径：

```
~/.hermes/profiles/kol-orchestrator/skills/social-media/kol-outreach-orchestrator/SKILL.md
```

版本信息在 frontmatter 里：当前 `version: 1.1.0`。

如果你以后再改这套流程，只 patch SKILL.md，不要硬复制粘贴。orchestrator 每次 load skill 都读最新文件。

---

## 13. 下一步（针对当前 TS8125）

按下面这个顺序就能把流程跑下去：

1. **现在**：阅读 §5.1，决定 brief 是否同意。同意就执行：
   ```bash
   hermes kanban unblock t_4fe14b4f && hermes kanban complete t_4fe14b4f && hermes kanban dispatch
   ```
2. discovery 跑大约 5-15 分钟。`hermes kanban log t_5d301990 -f` 看进度。
3. 跑完后告诉主对话："discovery 完成了，把 shortlist_review 改成 blocked 并贴上达人名单"。orchestrator 会做。
4. 你审名单（§5.2 + §5.5），用 `APPROVE: @h1, @h2, ...` 协议提交。
5. orchestrator 创建 Layer B（每个达人 10 卡），你逐个 gate 审过去。
6. 收到回信后，按 §5.4 决定方向。

需要我现在直接帮你执行第 1 步吗？
