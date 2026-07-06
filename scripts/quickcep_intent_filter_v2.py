#!/usr/bin/env python3
"""
QuickCEP 意图标签筛选工具 v2
专门针对 intentionTags 字段进行会话筛选
"""

import json
import subprocess
import sys

# 意图标签映射 (基于实际数据)
INTENT_TAGS = {
    "产品咨询": "Product inquiry产品咨询",
    "物流咨询": "物流咨询",
    "退货退款咨询": "Return/refund inquiry退货退款咨询",
    "支付咨询": "Payment inquiry",
    "服务咨询": "Service inquiry",
    "折扣咨询": "Discounts/Sale inquiry"
}

def get_all_sessions(max_pages=5):
    """获取所有邮件会话"""
    cli_path = "/Users/arnold/.hermes/profiles/povison-cs/skills/social-media/quickcep/scripts/quickcep_cli.py"
    
    all_sessions = []
    
    for page in range(1, max_pages + 1):
        result = subprocess.run([
            sys.executable, cli_path, "sessions",
            "--email-only", "--page-size", "100",
            "--page", str(page)
        ], capture_output=True, text=True)
        
        if result.returncode != 0:
            print(f"Error on page {page}: {result.stderr}")
            continue
            
        try:
            data = json.loads(result.stdout)
            sessions = data.get("sessions", [])
            
            if not sessions:
                break
                
            all_sessions.extend(sessions)
            
            # 检查是否还有更多页
            if not data.get("hasNextPage"):
                break
                
        except json.JSONDecodeError as e:
            print(f"JSON解析错误: {e}")
            continue
    
    return all_sessions

def filter_by_intention_tags(sessions, target_intentions, logic="OR"):
    """按意图标签筛选会话"""
    if logic == "OR":
        # 任一标签匹配
        filtered = [s for s in sessions if any(
            intent in (s.get("intentionTags") or [])
            for intent in target_intentions
        )]
    else:  # AND
        # 所有标签都匹配
        filtered = [s for s in sessions if all(
            intent in (s.get("intentionTags") or [])
            for intent in target_intentions
        )]
    
    return filtered

def analyze_intent_distribution(sessions):
    """分析意图标签分布"""
    intent_counts = {}
    
    for session in sessions:
        intentions = session.get("intentionTags") or []
        for intent in intentions:
            intent_counts[intent] = intent_counts.get(intent, 0) + 1
    
    return intent_counts

def main():
    print("🎯 QuickCEP 意图标签筛选工具 v2")
    print("=" * 40)
    print()
    
    # 检测运行模式
    if len(sys.argv) > 1:
        mode = sys.argv[1].lower()
    else:
        mode = "product"  # 默认产品咨询
    
    print(f"📋 模式: {mode}")
    print()
    
    # 获取所有会话
    print("🔄 获取会话数据...")
    all_sessions = get_all_sessions(max_pages=10)
    print(f"📊 总共获取 {len(all_sessions)} 个会话")
    print()
    
    # 分析当前意图分布
    intent_distribution = analyze_intent_distribution(all_sessions)
    print("📈 当前意图标签分布:")
    for intent, count in sorted(intent_distribution.items(), key=lambda x: x[1], reverse=True):
        print(f"  • {intent}: {count} 个会话")
    print()
    
    # 根据模式筛选
    if mode == "product":
        target_intentions = ["产品咨询"]
        print("🔍 筛选产品咨询会话...")
    elif mode == "logistics":
        target_intentions = ["物流咨询"]
        print("🔍 筛选物流咨询会话...")
    elif mode == "both":
        target_intentions = ["产品咨询", "物流咨询"]
        print("🔍 筛选产品咨询或物流咨询会话...")
    elif mode == "and_both":
        target_intentions = ["产品咨询", "物流咨询"]
        print("🔍 筛选同时包含产品咨询和物流咨询的会话...")
        filtered = filter_by_intention_tags(all_sessions, target_intentions, logic="AND")
        
        print(f"📊 找到 {len(filtered)} 个匹配会话")
        
        if filtered:
            print("\n📋 匹配的会话:")
            for i, session in enumerate(filtered[:5], 1):
                email = session.get("visitorInfo", {}).get("email", "N/A")
                intentions = session.get("intentionTags", [])
                print(f"  {i}. {email}")
                print(f"     意图: {', '.join(intentions)}")
            
            if len(filtered) > 5:
                print(f"\n... 还有 {len(filtered) - 5} 个会话")
            
            # 保存结果
            with open('/tmp/intent_filtered_both.json', 'w') as f:
                json.dump(filtered, f, indent=2, ensure_ascii=False)
            print(f"\n✅ 结果已保存到 /tmp/intent_filtered_both.json")
        else:
            print("未找到匹配的会话")
        
        return
    else:
        print(f"❌ 未知模式: {mode}")
        print("可用模式: product, logistics, both, and_both")
        return
    
    filtered = filter_by_intention_tags(all_sessions, target_intentions)
    
    print(f"📊 找到 {len(filtered)} 个匹配会话")
    print()
    
    if filtered:
        print("📋 前5个匹配的会话:")
        print()
        
        for i, session in enumerate(filtered[:5], 1):
            email = session.get("visitorInfo", {}).get("email", "N/A")
            intentions = session.get("intentionTags", [])
            sentiment = session.get("sentimentTags", [])
            unread = session.get("unreadNum", 0)
            last_time = session.get("lastMsgTime", "N/A")
            
            print(f"{i}. 📧 {email}")
            print(f"   意图: {', '.join(intentions) if intentions else '无'}")
            print(f"   情感: {', '.join(sentiment) if sentiment else '无'}")
            print(f"   未读: {unread} | 最后消息: {last_time}")
            print()
        
        if len(filtered) > 5:
            print(f"... 还有 {len(filtered) - 5} 个会话")
        
        # 保存完整结果
        filename = f'/tmp/intent_filtered_{mode}.json'
        with open(filename, 'w') as f:
            json.dump(filtered, f, indent=2, ensure_ascii=False)
        print(f"\n✅ 完整结果已保存到 {filename}")
        
        # 导出CSV格式
        csv_data = []
        for session in filtered:
            email = session.get("visitorInfo", {}).get("email", "N/A")
            intentions = session.get("intentionTags", [])
            sentiment = session.get("sentimentTags", [])
            csv_data.append({
                "email": email,
                "intentions": ",".join(intentions),
                "sentiment": ",".join(sentiment),
                "unread": session.get("unreadNum", 0),
                "last_msg_time": session.get("lastMsgTime", "N/A"),
                "session_id": session.get("id", "")
            })
        
        csv_filename = f'/tmp/intent_filtered_{mode}.csv'
        with open(csv_filename, 'w') as f:
            f.write("email,intentions,sentiment,unread,last_msg_time,session_id\n")
            for row in csv_data:
                f.write(f"{row['email']},{row['intentions']},{row['sentiment']},{row['unread']},{row['last_msg_time']},{row['session_id']}\n")
        
        print(f"✅ CSV格式已保存到 {csv_filename}")
        
    else:
        print("未找到匹配的会话")
        print("\n💡 提示: 可能的原因:")
        print("  1. 当前会话没有被自动标注意图标签")
        print("  2. 意图标签分配逻辑可能需要人工触发")
        print("  3. 意图标签名称可能与预期不同")

if __name__ == "__main__":
    main()