#!/usr/bin/env python3
import re, requests, json
from datetime import datetime

RESULT_FILE = "/Users/arnold/agent_prj/hermes-agent/.hermes/daily-commits/feishu_result.json"

try:
    with open('/Users/arnold/.hermes/profiles/kol-orchestrator/.env', 'r') as f:
        env = f.read()

    m1 = re.search(r'FEISHU_APP_ID=(\S+)', env)
    m2 = re.search(r'FEISHU_APP_SECRET=(\S+)', env)
    if not m1 or not m2:
        raise ValueError(f"Credentials not found: app_id={'found' if m1 else 'missing'}, secret={'found' if m2 else 'missing'}")

    app_id = m1.group(1)
    app_secret = m2.group(1)

    token_resp = requests.post(
        'https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal',
        json={'app_id': app_id, 'app_secret': app_secret},
        timeout=10
    )
    token_data = token_resp.json()
    if token_data.get('code') != 0:
        raise ValueError(f"Token error: {token_data}")
    tenant_token = token_data['tenant_access_token']

    def text_el(content, bold=False):
        el = {'text_run': {'content': content}}
        if bold:
            el['text_run']['text_element_style'] = {'bold': True}
        return el

    def bold_line(text):
        return {'block_type': 2, 'text': {'elements': [text_el(text, True)]}}

    def para(text):
        return {'block_type': 2, 'text': {'elements': [text_el(text)]}}

    blocks = [
        bold_line('📋 2026-06-03 无提交'),
        para('当日仓库无新增提交。'),
    ]

    doc_id = 'WRPedyPdqoaDXnxqWabcEEDDnAb'
    headers = {
        'Authorization': f'Bearer {tenant_token}',
        'Content-Type': 'application/json'
    }
    resp = requests.post(
        f'https://open.feishu.cn/open-apis/docx/v1/documents/{doc_id}/blocks/{doc_id}/children',
        headers=headers,
        json={'children': blocks},
        timeout=15
    )
    result = resp.json()

    with open(RESULT_FILE, 'w') as f:
        json.dump({
            'status': 'ok' if result.get('code') == 0 else 'error',
            'http_status': resp.status_code,
            'feishu_code': result.get('code'),
            'feishu_msg': result.get('msg', ''),
            'timestamp': datetime.now().isoformat()
        }, f, ensure_ascii=False, indent=2)

except Exception as e:
    import traceback
    with open(RESULT_FILE, 'w') as f:
        f.write(json.dumps({
            'status': 'exception',
            'error': str(e),
            'traceback': traceback.format_exc(),
            'timestamp': datetime.now().isoformat()
        }, ensure_ascii=False, indent=2))
