#!/usr/bin/env python3
"""
QuickCEP 业务意图标签分析工具
分析现有标签字段并寻找可能的业务意图标签
"""

import json
import subprocess
import sys

def analyze_all_tag_fields():
    """全面分析所有标签相关字段"""
    cli_path = "/Users/arnold/.hermes/profiles/povison-cs/skills/social-media/quickcep/scripts/quickcep_cli.py"
    
    # 获取多个会话样本
    result = subprocess.run([
        sys.executable, cli_path, "sessions",
        "--email-only", "--page-size", "50"
    ], capture_output=True, text=True)
    
    if result.returncode != 0:
        print(f"Error: {result.stderr}")
        return {}
        
    try:
        data = json.loads(result.stdout)
        sessions = data.get("sessions", [])
        
        # 统计所有标签字段
        all_tag_fields = {
            'intentionTags': set(),
            'sentimentTags': set(), 
            'intentions': set(),
            'slaTags': set(),
            'subSessionTags': set()
        }
        
        for session in sessions:
            for field in all_tag_fields.keys():
                value = session.get(field)
                if isinstance(value, list):
                    all_tag_fields[field].update(value)
        
        return {
            'sessions_count': len(sessions),
            'tag_fields': all_tag_fields
        }
        
    except json.JSONDecodeError as e:
        print(f"JSON解析错误: {e}")
        return {}

def search_business_related_fields():
    """搜索可能包含业务意图的字段"""
    cli_path = "/Users/arnold/.hermes/profiles/povison-cs/skills/social-media/quickcep/scripts/quickcep_cli.py"
    
    result = subprocess.run([
        sys.executable, cli_path, "sessions",
        "--email-only", "--page-size", "10"
    ], capture_output=True, text=True)
    
    if result.returncode != 0:
        return {}
        
    try:
        data = json.loads(result.stdout)
        sessions = data.get("sessions", [])
        
        # 查找可能包含业务信息的字段
        business_fields = {}
        
        for session in sessions:
            for key, value in session.items():
                if any(keyword in key.lower() for keyword in 
                       ['business', 'purpose', 'category', 'type', 'nature', 'domain', 'scope']):
                    if key not in business_fields:
                        business_fields[key] = []
                    
                    if isinstance(value, (list, str)) and value:
                        business_fields[key].append(value)
                    elif value not in [None, 0, ""]:
                        business_fields[key].append(value)
        
        return business_fields
        
    except json.JSONDecodeError as e:
        print(f"JSON解析错误: {e}")
        return {}

def main():
    print("🔍 QuickCEP 业务意图标签分析工具")
    print("=" * 40)
    print()
    
    print("📊 当前已知标签字段分析:")
    print("-" * 40)
    
    tag_analysis = analyze_all_tag_fields()
    
    if tag_analysis:
        print(f"样本会话数: {tag_analysis['sessions_count']}")
        print()
        
        for field, values in tag_analysis['tag_fields'].items():
            print(f"📌 {field}:")
            if values:
                unique_values = sorted(values)
                print(f"   发现 {len(unique_values)} 个唯一值:")
                for i, val in enumerate(unique_values[:10], 1):  # 只显示前10个
                    print(f"   {i}. {val}")
                if len(unique_values) > 10:
                    print(f"   ... 还有 {len(unique_values) - 10} 个值")
            else:
                print(f"   (无数据)")
            print()
    
    print("🔎 搜索可能包含业务意图的字段:")
    print("-" * 40)
    
    business_fields = search_business_related_fields()
    
    if business_fields:
        for field, values in business_fields.items():
            print(f"📌 {field}:")
            print(f"   示例值: {values[:3] if len(values) > 3 else values}")
            print()
    else:
        print("未找到明显的业务意图字段")
        print()
    
    print("❓ 如果您指的是特定的业务意图标签，请提供:")
    print("1. 字段名称（如果知道）")
    print("2. 示例值或预期值")
    print("3. 这些标签在QuickCEP界面中的显示位置")
    print()
    
    print("💡 可能的业务意图标签来源:")
    print("• visitorInfo 中的业务相关字段")
    print("• 消息内容中的关键词分析")
    print("• 系统自动分类的结果")
    print("• 人工设置的额外标签")

if __name__ == "__main__":
    main()