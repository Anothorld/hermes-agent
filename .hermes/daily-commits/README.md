# 📋 每日 Git 提交汇总

## 2026-06-08（周一）

### 当日进度概览

当日共 5 个提交，全部由 Anothorld 完成，涵盖 KOL 运维流程稳定性、浏览器工具健壮性、技能同步修复三大方向。总计 77 个文件变更，+3826 / -711 行，核心目标是消除 KOL campaign 执行中的卡死、审批盲区和浏览器 CDP 挂起问题。

### 重点变更

**KOL 运维流程稳定性**
- 阻止 `delegate_task` 在 campaign 发现阶段被误调用，防止运行卡死
- 纯文本草稿替代 HTML 草稿，修复审批盲区（approval watcher 无法感知 HTML 草稿）
- veedcrawl 参数校验加固，新增 `arg_validate.py` 模块 + 96 行单测
- kol-ops-bridge 回复草稿持久化、CLI 错误输出标准化

**浏览器工具健壮性**
- 云端 `create_session` 加硬超时，避免不可达云端无限挂起
- 移除废弃的 chrome-devtools MCP 路径，消除 degraded CDP 场景下的挂起
- local-chrome-tab-pool 连接逻辑大幅重写（+218 行），增强调试 Chrome 支持

**技能同步与发现修复**
- `skills_sync.py` 修复 HERMES_HOME 解析时机（同步时动态读取而非 import 时硬编码）
- kol-email-discovery 工具声明与技能描述对齐修正
- instagram-kol-discovery SKILL.md 大幅更新（+156/-部分），明确 browser_navigate 调用流程

### 提交明细

| 短 Hash | 时间 | 提交信息 |
|---------|------|---------|
| `661889a95` | 21:06 | fix(kol): block delegate_task on campaign discovery and harden veedcrawl args |
| `195ebaa7d` | 15:38 | fix(skills): resolve HERMES_HOME at sync time and correct email-discovery tools |
| `fcb226195` | 15:12 | fix(browser): hard-timeout cloud create_session so unreachable cloud can't hang runs |
| `95b3f8d3a` | 14:49 | fix(browser): eliminate dead chrome-devtools MCP path and degraded-CDP hangs |
| `04d5a9736` | 14:03 | fix(kol-ops): prevent stuck runs, plain-text drafts, and approval blind spots |

### 变更统计

- 文件数：77
- 新增行：+3,826
- 删除行：-711
- 净增：+3,115
