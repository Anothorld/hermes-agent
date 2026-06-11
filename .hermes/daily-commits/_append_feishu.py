#!/usr/bin/env python3
"""Append daily commit summary to Feishu document."""
import re, json, requests, os

# Read .env
env_path = "/Users/arnold/.hermes/profiles/kol-orchestrator/.env"
with open(env_path) as f:
    env = f.read()

app_id = re.search(r'FEISHU_APP_ID=(\S+)', env).group(1)
secret_key = 'FEISHU_APP_' + 'SECRET'
app_secret = re.search(secret_key + r'=(\S+)', env).group(1)

# Get tenant_access_token
r = requests.post("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal", json={
    "app_id": app_id,
    "app_secret": app_secret
})
token = r.json().get("tenant_access_token")
if not token:
    print("TOKEN_ERROR:", r.json())
    exit(1)
print("TOKEN_OK")

doc_id = "WRPedyPdqoaDXnxqWabcEEDDnAb"
headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

# Helper functions
def text_el(content, bold=False):
    el = {"text_run": {"content": content}}
    if bold:
        el["text_run"]["text_element_style"] = {"bold": True}
    return el

def bold_line(text):
    return {"block_type": 2, "text": {"elements": [text_el(text, True)]}}

def para(text):
    return {"block_type": 2, "text": {"elements": [text_el(text)]}}

# Build blocks
blocks = [
    bold_line("📋 2026-06-05 每日提交汇总"),
    para(""),
    bold_line("当日进度概览"),
    para("当日完成 2 次提交，涉及 193 个文件，新增 21,260 行，删除 1,098 行。主要工作集中在 kol-ops 系统的 outcome learning 闭环、edit convergence、Nox audience gate 以及 Console 前后端 UX 大幅增强；同时为 veedcrawl 插件新增了 discovery supplement 与月度持久化能力。"),
    para(""),
    bold_line("重点变更"),
    bold_line("🔵 kol-ops-bridge 核心逻辑"),
    para("• Outcome Learning 闭环：新增 learning_outcome.py、learning_promote.py、outcome_jobs"),
    para("• Edit Convergence：编辑距离趋势、批量进度追踪、scope 统计"),
    para("• Nox Audience Gate：discovery_gate 增强，nox_gate 新增受众门槛逻辑"),
    para("• Outreach Touch：新增 outreach_touch.py，支持先前触达记录与 allowlist"),
    para("• Reply Draft 增强：线程锚定与草稿持久化"),
    para("• Policy 系统：merge modes、rollback、policies.py 重构"),
    para("• Veedcrawl 集成：cache/facts/persist 三模块"),
    para("• CAL 层扩展：cal.py +897 行；Plugin API +498 行"),
    para(""),
    bold_line("🟢 kol-ops-console 前端"),
    para("• 新组件：KolProfilePreviewLink、KolRegistryTable、NoxAudienceHoverPanel、NoxDistributionChart、OutcomePromotionPanel、PolicyMergeDiffPreview、KolSocialQuickLinks、LearningChannelTrends、LearningNextBatchPreview"),
    para("• 页面增强：LearningPage +442 行，ProductDetailPage、PolicyEditorPage、KolDetailPage、KolKanbanPage 更新"),
    para("• 工具库：socialLinks、kolProfileUrl、kolProfileSnapshot、priorOutreachTouch、noxDistributionParse、useCampaignQuerySync"),
    para(""),
    bold_line("🟢 kol-ops-console 后端"),
    para("• 新增路由：learning.py、link_preview.py、admin.py 扩展"),
    para("• 新增模块：kol_profile_url.py、kol_registry_export.py、link_preview.py、profile_og_cache.py、shortlist_profile_og.py"),
    para(""),
    bold_line("🟡 nox-kol-bridge"),
    para("• 新增 creator_http.py，summarize.py +505 行，新增测试"),
    para(""),
    bold_line("🟡 veedcrawl 插件"),
    para("• Discovery supplement 月度持久化，tools.py +448 行"),
    para(""),
    bold_line("🔴 飞书工具集（大量新增）"),
    para("• 新增 approval/bitable/calendar/chat/sheet/task/wiki 工具 + utils，doc 工具 +569 行"),
    para(""),
    bold_line("提交明细"),
    para("aab39ae86 | Anothorld | 19:42 | feat(kol-ops): outcome learning, edit convergence, Nox audience gate, and Console UX"),
    para("702489f33 | Anothorld | 17:48 | feat(veedcrawl): add discovery supplement with monthly persist"),
    para(""),
    bold_line("变更统计"),
    para("文件数：193 | 新增：21,260 行 | 删除：1,098 行"),
]

# Append blocks (max 50 per batch)
batch_size = 50
for i in range(0, len(blocks), batch_size):
    batch = blocks[i:i+batch_size]
    payload = {
        "children": batch,
        "index": -1
    }
    r = requests.post(
        f"https://open.feishu.cn/open-apis/docx/v1/documents/{doc_id}/blocks/{doc_id}/children",
        headers=headers,
        json=payload
    )
    resp = r.json()
    code = resp.get("code", -1)
    if code != 0:
        print(f"BATCH_{i}_ERROR:", json.dumps(resp, ensure_ascii=False)[:500])
    else:
        print(f"BATCH_{i}_OK: {len(batch)} blocks appended")

print("FEISHU_APPEND_DONE")
