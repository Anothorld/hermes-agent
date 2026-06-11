#!/usr/bin/env python3
"""Append daily commit summary to Feishu document."""
import re, json, urllib.request, urllib.error

# --- Step 1: Read Feishu credentials from .env ---
env_path = "/Users/arnold/.hermes/profiles/kol-orchestrator/.env"
with open(env_path, "r") as f:
    env_text = f.read()

m1 = re.search(r'FEISHU_APP_ID=(\S+)', env_text)
app_id = m1.group(1) if m1 else ""

secret_key = 'FEISHU_APP_' + 'SECRET'
m2 = re.search(secret_key + r'=(\S+)', env_text)
app_secret = m2.group(1) if m2 else ""

print(f"APP_ID: {app_id}")
print(f"SECRET length: {len(app_secret)}")

if not app_id or not app_secret:
    print("ERROR: Missing Feishu credentials")
    exit(1)

# --- Step 2: Get tenant_access_token ---
token_url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
token_body = json.dumps({
    "app_id": app_id,
    "app_secret": app_secret
}).encode("utf-8")

req = urllib.request.Request(token_url, data=token_body, method="POST")
req.add_header("Content-Type", "application/json; charset=utf-8")

try:
    with urllib.request.urlopen(req, timeout=10) as resp:
        token_data = json.loads(resp.read().decode("utf-8"))
    print(f"Token response code: {token_data.get('code')}")
    if token_data.get("code") != 0:
        print(f"Token error: {token_data}")
        exit(1)
    tenant_token = token_data["tenant_access_token"]
    print(f"Got tenant token, length: {len(tenant_token)}")
except Exception as e:
    print(f"Token request failed: {e}")
    exit(1)

# --- Step 3: Build blocks ---
doc_id = "WRPedyPdqoaDXnxqWabcEEDDnAb"

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
    bold_line("📋 2026-06-10 提交汇总"),
    para(""),
    bold_line("当日进度概览"),
    para("今日共 3 个提交，主要围绕 kol-ops 决策学习闭环、邮件发现后自动起草、以及升级提示中文化三大方向。新增 9,886 行代码，删除 842 行，涉及 112 个文件，覆盖后端核心逻辑、前端交互组件和插件测试。"),
    para(""),
    bold_line("重点变更"),
    para(""),
    bold_line("1. 发现决策学习闭环 (discovery decision learning loop)"),
    para("• 新增 discovery_decision_learning.py（379行）与 learning_discovery.py（751行），实现发现阶段的决策学习与蒸馏"),
    para("• 新增 discovery_decision_tags.py（215行），支持行级 approve 标签"),
    para("• 新增 DiscoveryLearningPanel.tsx（482行）、ShortlistDecisionFeedbackDialog.tsx（435行）、KolApproveAnnotationDialog.tsx（228行）等前端组件"),
    para("• 后端新增 learned_criteria.py、gate_metrics_audit.py、gate_metrics_trends.py，支撑指标审计与趋势分析"),
    para("• 前端新增 MetricTrendSparkline.tsx、ApprovalContextCard.tsx、ProductCategoryField.tsx"),
    para(""),
    bold_line("2. 邮件发现后自动起草 (auto-draft after email discover)"),
    para("• 新增 email_discover_dispatch.py（477行）与 post_email_discover_draft.py（300行）"),
    para("• gmail_console.py 扩展 141 行，gmail_reconcile.py 更新"),
    para("• cal.py 大幅扩展（+615行），plugin_api.py 扩展（+307行）"),
    para("• inbound_reply 模块更新（orchestrator、processor、recovery）"),
    para(""),
    bold_line("3. 升级提示中文化 & 其他优化"),
    para("• mailbox_escalation.py 更新，升级操作提示改为中文"),
    para("• local-chrome-tab-pool 修复与优化（hooks.py、tab_pool.py）"),
    para("• veedcrawl 新增 search.py（148行）搜索模块及测试"),
    para("• skills 目录下多个 SKILL.md 更新（instagram-kol-discovery、email-discovery 等）"),
    para(""),
    bold_line("提交明细"),
    para("• cfd9cdde9 Anothorld 16:03 feat(kol-ops): discover decision learning loop and row-level approve tags"),
    para("• 66f620151 Anothorld 11:19 feat(kol-ops): auto-draft after email discover and harden outreach prep"),
    para("• d94dc672f Anothorld 10:14 feat(kol-ops): show escalation operator prompts in Chinese"),
    para(""),
    bold_line("变更统计"),
    para("• 文件数：112 | 新增行：9,886 | 删除行：842"),
    para(""),
    para("---"),
    para(""),
]

# --- Step 4: Append blocks to Feishu doc (max 50 per batch) ---
api_url = f"https://open.feishu.cn/open-apis/docx/v1/documents/{doc_id}/blocks/{doc_id}/children"

def append_blocks(blocks_batch, token):
    body = json.dumps({"children": blocks_batch}).encode("utf-8")
    req = urllib.request.Request(api_url, data=body, method="POST")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        print(f"Append result code: {result.get('code')}, msg: {result.get('msg', '')}")
        return result.get("code") == 0
    except urllib.error.HTTPError as e:
        print(f"HTTP error {e.code}: {e.read().decode('utf-8', errors='replace')[:500]}")
        return False
    except Exception as e:
        print(f"Append error: {e}")
        return False

# Split into batches of 50
batch_size = 50
success = True
for i in range(0, len(blocks), batch_size):
    batch = blocks[i:i+batch_size]
    print(f"Appending batch {i//batch_size + 1} ({len(batch)} blocks)...")
    if not append_blocks(batch, tenant_token):
        success = False
        break

if success:
    print("Successfully appended all blocks to Feishu document!")
    print(f"Doc URL: https://bytedance.feishu.cn/docx/{doc_id}")
else:
    print("Failed to append some blocks to Feishu document.")
