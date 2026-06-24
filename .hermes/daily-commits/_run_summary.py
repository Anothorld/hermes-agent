import re, json, requests, os

# ── Step 1: Parse git data ──
date = "2026-06-11"
commit_hash = "5e2f578ce"
author = "Anothorld"
time = "2026-06-11 17:45:03 +0800"
message = "feat(kol): CDP tab ownership, llm_compress learning, and console UX"
files_changed = 70
insertions = 4007
deletions = 622

# ── Step 2: Generate summary ──
summary = f"""# 📋 {date} 每日提交汇总

## 当日进度概览
当日有 1 个提交，涉及 70 个文件，净增 3385 行（+4007 / -622）。主要工作集中在三个方向：CDP 浏览器 Tab 所有权机制、LLM 压缩学习管线（learning_discover/distill/store）、以及 KOL 运营控制台 UX 优化（审批流程、产品详情页、升级标签体系）。

## 重点变更

### 🔧 CDP Tab 所有权 & Chrome Tab Pool
- `local-chrome-tab-pool/hooks.py` 重构 tab 所有权模型
- 新增 CDP tab-pool workaround 参考文档
- `tab_pool.py` 精简，移除冗余代码

### 🧠 Learning 管线（llm_compress）
- `learning_discovery.py` — 发现逻辑增强
- `learning_distill.py` — 大幅扩展蒸馏策略（+676 行）
- `learning_store.py` — 存储层重构，支持 edit learning 去重与策略
- 新增 `learning_overview.py` 模块
- 新增测试：`test_discovery_decision_learning`、`test_edit_learning_dedupe`、`test_edit_learning_strategy`、`test_policy_delta_patch`

### 🖥️ Console 前端 UX
- `ApprovalsPage.tsx` — 审批页面大幅优化
- `ProductDetailPage.tsx` — 产品详情页重构
- `KolDetailPage.tsx` — KOL 详情页调整
- `EscalationConsolePage.tsx` — 升级控制台改进
- `LearningPage.tsx` — 学习页面更新
- 新增 `escalationLabels.ts` 常量文件（+105 行）
- `PolicyMergeDiffPreview.tsx`、`ApprovalDetailPanel.tsx` 等组件优化

### 🔌 Kol-Ops-Bridge 插件
- `cal.py` — CAL 层大幅扩展（+232 行）
- `gmail_client.py` / `gmail_reconcile.py` — Gmail 集成改进
- `plugin_api.py` — API 层扩展
- 新增 `reply_draft_kind.py`、`sku_prior_approval.py`、`discovery_gate.py`
- 删除废弃模块 `email_discover_dispatch.py`、`nox_contacts_sync.py`

### 🔀 Campaign 合并
- 新增 `merge_campaigns.py` 脚本（+180 行）及测试

### 📚 文档 & 测试
- 多个 feature guide 更新（approvals、campaigns、learning、products、gmail）
- 新增 Gmail reconcile backfill 测试（+271 行）
- 测试覆盖：bridge approval timeout、campaign config completeness、shortlist router

## 提交明细
| 短Hash | 作者 | 时间 | Message |
|--------|------|------|---------|
| 5e2f578ce | Anothorld | 17:45 +0800 | feat(kol): CDP tab ownership, llm_compress learning, and console UX |

## 变更统计
- 📁 文件数：70
- ➕ 新增行：4,007
- ➖ 删除行：622
- 📊 净增：+3,385
"""

# ── Step 3: Write local markdown ──
out_dir = "/Users/arnold/agent_prj/hermes-agent/.hermes/daily-commits"
os.makedirs(out_dir, exist_ok=True)
with open(os.path.join(out_dir, "README.md"), "w") as f:
    f.write(summary)
print("Local markdown written")

# ── Step 4: Append to Feishu ──
env_path = "/Users/arnold/.hermes/profiles/kol-orchestrator/.env"
with open(env_path) as f:
    env_text = f.read()

app_id = re.search(r'FEISHU_APP_ID=(\S+)', env_text).group(1)
secret_key = 'FEISHU_APP_' + 'SECRET'
app_secret = re.search(secret_key + r'=(\S+)', env_text).group(1)

print(f"APP_ID: {app_id}")
print(f"APP_SECRET length: {len(app_secret)}")

# Get tenant_access_token
token_resp = requests.post(
    "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
    json={"app_id": app_id, "app_secret": app_secret},
    timeout=15
)
token_data = token_resp.json()
print(f"Token response code: {token_data.get('code')}")
if token_data.get('code') != 0:
    print(f"Token error: {token_data}")
    raise SystemExit(1)

tenant_token = token_data['tenant_access_token']
print("Got tenant_access_token")

# Build blocks
def text_el(content, bold=False):
    el = {"text_run": {"content": content}}
    if bold:
        el["text_run"]["text_element_style"] = {"bold": True}
    return el

def bold_line(text):
    return {"block_type": 2, "text": {"elements": [text_el(text, True)]}}

def para(text):
    return {"block_type": 2, "text": {"elements": [text_el(text)]}}

blocks = [
    bold_line(f"📋 {date} 每日提交汇总"),
    para(""),
    bold_line("当日进度概览"),
    para("1 个提交，70 文件，净增 +3385 行（+4007 / -622）。主要工作：CDP Tab 所有权机制、LLM 压缩学习管线、KOL 运营控制台 UX 优化。"),
    para(""),
    bold_line("🔧 CDP Tab 所有权 & Chrome Tab Pool"),
    para("• hooks.py 重构 tab 所有权模型"),
    para("• 新增 CDP tab-pool workaround 参考文档"),
    para(""),
    bold_line("🧠 Learning 管线（llm_compress）"),
    para("• learning_distill.py 大幅扩展（+676 行）"),
    para("• learning_store.py 存储层重构，支持 edit learning 去重"),
    para("• 新增 learning_overview.py、4 个测试文件"),
    para(""),
    bold_line("🖥️ Console 前端 UX"),
    para("• ApprovalsPage / ProductDetailPage / KolDetailPage 大幅优化"),
    para("• 新增 escalationLabels.ts（+105 行）"),
    para(""),
    bold_line("🔌 Kol-Ops-Bridge 插件"),
    para("• cal.py 扩展 +232 行"),
    para("• 新增 reply_draft_kind / sku_prior_approval / discovery_gate"),
    para("• gmail_reconcile.py 重构"),
    para(""),
    bold_line("🔀 Campaign 合并 & 文档"),
    para("• 新增 merge_campaigns.py（+180 行）及测试"),
    para("• 多个 feature guide 更新"),
    para(""),
    bold_line("提交明细"),
    para("5e2f578ce | Anothorld | 17:45 +0800 | feat(kol): CDP tab ownership, llm_compress learning, and console UX"),
    para(""),
    bold_line("变更统计"),
    para("📁 70 文件 | ➕ +4,007 | ➖ -622 | 📊 净增 +3,385"),
]

doc_id = "WRPedyPdqoaDXnxqWabcEEDDnAb"

# Split into batches of 50
batch_size = 50
for i in range(0, len(blocks), batch_size):
    batch = blocks[i:i+batch_size]
    resp = requests.post(
        f"https://open.feishu.cn/open-apis/docx/v1/documents/{doc_id}/blocks/{doc_id}/children",
        headers={
            "Authorization": f"Bearer {tenant_token}",
            "Content-Type": "application/json"
        },
        json={"children": batch},
        timeout=30
    )
    result = resp.json()
    print(f"Feishu append batch {i//batch_size + 1}: code={result.get('code')}, msg={result.get('msg', '')}")
    if result.get('code') != 0:
        print(f"Error detail: {json.dumps(result, ensure_ascii=False)[:500]}")

print("\nDone! Feishu doc: https://bytedance.feishu.cn/docx/WRPedyPdqoaDXnxqWabcEEDDnAb")
