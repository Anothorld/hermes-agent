#!/usr/bin/env python3
"""
QuickCEP 意图标签筛选工具
专门针对意图标签 (Inquiry Nature) 进行会话筛选
"""

import json
import subprocess
import sys
import os

# 意图标签ID
INTENT_TAG_ROOT = "1714833809199919106"  # 主题Inquiry Nature
PRODUCT_INQUIRY_INTENT = "1715241229806047233"  # Product inquiry产品咨询
LOGISTICS_INQUIRY_INTENT = None  # 需要查找物流相关的意图标签

def get_sessions_with_intent_tag(intent_tag_id, max_pages=5):
    """获取包含指定意图标签的会话"""
    cli_path = "/Users/arnold/.hermes/profiles/povison-cs/skills/social-media/quickcep/scripts/quickcep_cli.py"
    
    all_sessions = []
    
    for page in range(1, max_pages + 1):
        result = subprocess.run([
            sys.executable, cli_path, "sessions",
            "--email-only", "--compact",
            "--page", str(page),
            "--page-size", "100"
        ], capture_output=True, text=True)
        
        if result.returncode != 0:
            print(f"Error on page {page}: {result.stderr}")
            continue
            
        try:
            data = json.loads(result.stdout)
            sessions = data.get("sessions", [])
            
            if not sessions:
                break
                
            # 筛选包含指定意图标签的会话
            filtered = [s for s in sessions if intent_tag_id in s.get("subSessionTags", [])]
            all_sessions.extend(filtered)
            
            print(f"  Page {page}: 找到 {len(filtered)} 个匹配会话")
            
            # 检查是否还有更多页
            if not data.get("hasNextPage"):
                break
                
        except json.JSONDecodeError as e:
            print(f"JSON解析错误: {e}")
            continue
    
    return all_sessions

def filter_by_intent_tags(tag_ids, logic="OR"):
    """按意图标签逻辑筛选会话"""
    cli_path = "/Users/arnold/.hermes/profiles/povison-cs/skills/social-media/quickcep/scripts/quickcep_cli.py"
    
    all_sessions = []
    page = 1
    
    while True:
        result = subprocess.run([
            sys.executable, cli_path, "sessions",
            "--email-only", "--compact",
            "--page", str(page),
            "--page-size", "200"
        ], capture_output=True, text=True)
        
        if result.returncode != 0:
            print(f"Error: {result.stderr}")
            break
            
        try:
            data = json.loads(result.stdout)
            sessions = data.get("sessions", [])
            
            if not sessions:
                break
            
            if logic == "OR":
                # 任一标签匹配
                filtered = [s for s in sessions if any(tag in s.get("subSessionTags", []) for tag in tag_ids)]
            else:  # AND
                # 所有标签都匹配
                filtered = [s for s in sessions if all(tag in s.get("subSessionTags", []) for tag in tag_ids)]
            
            all_sessions.extend(filtered)
            
            # 检查是否还有更多页
            if not data.get("hasNextPage"):
                break
                
            page += 1
            
        except json.JSONDecodeError as e:
            print(f"JSON解析错误: {e}")
            break
    
    return all_sessions

def get_all_intent_tags():
    """获取所有意图标签"""
    cli_path = "/Users/arnold/.hermes/profiles/povison-cs/skills/social-media/quickcep/scripts/quickcep_cli.py"
    
    result = subprocess.run([
        sys.executable, cli_path, "tags-tree"
    ], capture_output=True, text=True)
    
    if result.returncode != 0:
        return {}
        
    try:
        tree = json.loads(result.stdout)
        
        intent_tags = {}
        
        def find_intent_node(node, path=""):
            name = node.get("name", "")
            tag_id = node.get("id")
            
            if "inquiry" in name.lower() or "intent" in name.lower() or "意图" in name or "主题" in name:
                intent_tags[tag_id] = {
                    "name": name,
                    "id": tag_id,
                    "path": path + name
                }
            
            for child in node.get('children', []):
                find_intent_node(child, path + name + " > ")
        
        if isinstance(tree, list):
            for node in tree:
                find_intent_node(node)
        
        return intent_tags
        
    except json.JSONDecodeError as e:
        print(f"JSON解析错误: {e}")
        return {}

def main():
    print("🎯 QuickCEP 意图标签筛选工具")
    print("=" * 30)
    print()
    
    # 获取所有意图标签
    print("📋 可用的意图标签:")
    intent_tags = get_all_intent_tags()
    
    if not intent_tags:
        print("未找到意图标签")
        return
    
    for tag_id, tag_info in intent_tags.items():
        print(f"  • {tag_info['name']} (ID: {tag_id})")
    print()
    
    # 设置目标意图标签
    product_inquiry = PRODUCT_INQUIRY_INTENT
    logistics_inquiry = None
    
    # 尝试找到物流相关的意图标签
    logistics_candidates = [
        tag_id for tag_id, tag_info in intent_tags.items() 
        if any(keyword in tag_info['name'].lower() for keyword in 
               ['logistics', '物流', '运输', 'delivery', 'shipping', '配送'])
    ]
    
    if logistics_candidates:
        logistics_inquiry = logistics_candidates[0]
        print(f"🔍 找到物流意图: {intent_tags[logistics_inquiry]['name']}")
    
    print()
    
    # 检测运行模式
    if len(sys.argv) > 1:
        mode = sys.argv[1].lower()
    else:
        mode = "or"  # 默认OR逻辑
    
    # 构建标签列表
    target_tags = [product_inquiry]
    if logistics_inquiry:
        target_tags.append(logistics_inquiry)
    
    if mode == "and":
        print(f"📋 模式: AND (同时包含所有意图标签)")
        sessions = filter_by_intent_tags(target_tags, logic="AND")
    elif mode == "product":
        print(f"📋 模式: PRODUCT (仅产品咨询)")
        sessions = get_sessions_with_intent_tag(product_inquiry)
    elif logistics_inquiry and mode == "logistics":
        print(f"📋 模式: LOGISTICS (仅物流咨询)")
        sessions = get_sessions_with_intent_tag(logistics_inquiry)
    else:
        print(f"📋 模式: OR (包含任一意图标签)")
        sessions = filter_by_intent_tags(target_tags, logic="OR")
    
    print(f"📊 找到 {len(sessions)} 个匹配会话")
    print()
    
    if sessions:
        print("前5个会话:")
        print(json.dumps(sessions[:5], indent=2, ensure_ascii=False))
        
        if len(sessions) > 5:
            print(f"\n... 还有 {len(sessions) - 5} 个会话")
            
        # 保存完整结果
        with open('/tmp/intent_filtered_sessions.json', 'w') as f:
            json.dump(sessions, f, indent=2, ensure_ascii=False)
        print(f"\n✅ 完整结果已保存到 /tmp/intent_filtered_sessions.json")
    else:
        print("未找到匹配的会话")

if __name__ == "__main__":
    main()