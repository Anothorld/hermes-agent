#!/usr/bin/env python3
"""
QuickCEP 标签筛选工具
支持按产品咨询和物流咨询标签筛选会话
"""

import json
import subprocess
import sys
import os

# 标签ID
PRODUCT_INQUIRY_TAG = "1715241229806047233"  # Product inquiry产品咨询
LOGISTICS_INQUIRY_TAG = "1715248774713020417"  # Delivery Complaint

def get_sessions_with_tag(tag_id, max_pages=5):
    """获取包含指定标签的会话"""
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
                
            # 筛选包含指定标签的会话
            filtered = [s for s in sessions if tag_id in s.get("subSessionTags", [])]
            all_sessions.extend(filtered)
            
            print(f"  Page {page}: 找到 {len(filtered)} 个匹配会话")
            
            # 检查是否还有更多页
            if not data.get("hasNextPage"):
                break
                
        except json.JSONDecodeError as e:
            print(f"JSON解析错误: {e}")
            continue
    
    return all_sessions

def filter_by_tag_logic(tag_ids, logic="OR"):
    """按标签逻辑筛选会话"""
    cli_path = "/Users/arnold/.hermes/profiles/povison-cs/skills/social-media/quickcep/scripts/quickcep_cli.py"
    
    result = subprocess.run([
        sys.executable, cli_path, "sessions",
        "--email-only", "--compact",
        "--page-size", "200"
    ], capture_output=True, text=True)
    
    if result.returncode != 0:
        print(f"Error: {result.stderr}")
        return []
        
    try:
        data = json.loads(result.stdout)
        sessions = data.get("sessions", [])
        
        if logic == "OR":
            # 任一标签匹配
            filtered = [s for s in sessions if any(tag in s.get("subSessionTags", []) for tag in tag_ids)]
        else:  # AND
            # 所有标签都匹配
            filtered = [s for s in sessions if all(tag in s.get("subSessionTags", []) for tag in tag_ids)]
        
        return filtered
        
    except json.JSONDecodeError as e:
        print(f"JSON解析错误: {e}")
        return []

def main():
    print("🎯 QuickCEP 标签筛选工具")
    print("=" * 30)
    print()
    
    tag_ids = [PRODUCT_INQUIRY_TAG, LOGISTICS_INQUIRY_TAG]
    
    # 检测运行模式
    if len(sys.argv) > 1:
        mode = sys.argv[1].lower()
    else:
        mode = "or"  # 默认OR逻辑
    
    if mode == "and":
        print(f"📋 模式: AND (同时包含'产品咨询'和'物流咨询'标签)")
        sessions = filter_by_tag_logic(tag_ids, logic="AND")
    else:
        print(f"📋 模式: OR (包含任一标签：产品咨询、物流咨询)")
        sessions = filter_by_tag_logic(tag_ids, logic="OR")
    
    print(f"📊 找到 {len(sessions)} 个匹配会话")
    print()
    
    if sessions:
        print("前5个会话:")
        print(json.dumps(sessions[:5], indent=2, ensure_ascii=False))
        
        if len(sessions) > 5:
            print(f"\n... 还有 {len(sessions) - 5} 个会话")
    else:
        print("未找到匹配的会话")

if __name__ == "__main__":
    main()