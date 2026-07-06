#!/usr/bin/env python3
"""
QuickCEP 业务意图筛选工具 v3
结合官方标签和内容分析的业务意图筛选
"""

import json
import subprocess
import sys
import re

# 细粒度业务意图关键词
BUSINESS_INTENTS = {
    "询价": ["inquiry", "quote", "price", "cost", "how much", "pricing", "discount"],
    "投诉": ["complaint", "issue", "problem", "broken", "damaged", "not working", "defect"],
    "售后": ["return", "refund", "exchange", "warranty", "replacement", "repair"],
    "技术支持": ["support", "help", "technical", "install", "setup", "assembly", "instruction"],
    "配送咨询": ["delivery", "shipping", "tracking", "logistics", "when will", "arrive"],
    "产品咨询": ["product", "item", "specification", "size", "color", "material", "details"],
    "支付问题": ["payment", "billing", "invoice", "credit card", "charge", "transaction"],
    "订单问题": ["order", "status", "cancel", "modify", "change", "update"]
}

def analyze_session_business_intent(session_id):
    """分析单个会话的业务意图"""
    cli_path = "/Users/arnold/.hermes/profiles/povison-cs/skills/social-media/quickcep/scripts/quickcep_cli.py"
    
    result = subprocess.run([
        sys.executable, cli_path, "messages", str(session_id),
        "--plain"
    ], capture_output=True, text=True, timeout=10)
    
    if result.returncode != 0:
        return []
        
    try:
        data = json.loads(result.stdout)
        messages = data.get("messages", [])
        
        # 收集所有消息内容
        all_content = ""
        for msg in messages:
            if msg.get("contentType") == "html":
                content = msg.get("content", {})
                if isinstance(content, dict):
                    text = content.get("content", "")
                    # 移除HTML标签
                    text = re.sub(r'<[^>]+>', ' ', text)
                    all_content += text + " "
            elif msg.get("contentType") == "text":
                content = msg.get("content", "")
                all_content += content + " "
        
        # 检测业务意图
        detected_intents = []
        text_lower = all_content.lower()
        
        for intent, keywords in BUSINESS_INTENTS.items():
            for keyword in keywords:
                if keyword.lower() in text_lower:
                    if intent not in detected_intents:
                        detected_intents.append(intent)
                    break
        
        return detected_intents
        
    except (json.JSONDecodeError, subprocess.TimeoutExpired) as e:
        return []

def filter_sessions_by_business_intent(sessions, target_intent, max_analyze=50):
    """按业务意图筛选会话"""
    filtered = []
    
    for i, session in enumerate(sessions[:max_analyze]):
        session_id = session.get("id")
        email = session.get("visitorInfo", {}).get("email", "N/A")
        
        print(f"分析 {i+1}/{min(len(sessions), max_analyze)}: {email}")
        
        detected_intents = analyze_session_business_intent(session_id)
        
        if target_intent in detected_intents:
            session_copy = session.copy()
            session_copy['business_intents'] = detected_intents
            filtered.append(session_copy)
    
    return filtered

def main():
    print("🎯 QuickCEP 业务意图筛选工具 v3")
    print("=" * 40)
    print()
    
    if len(sys.argv) < 2:
        print("📋 可用的业务意图:")
        for intent in BUSINESS_INTENTS.keys():
            print(f"  • {intent}")
        print()
        print("使用方法:")
        print("  python quickcep_business_intent_filter.py <业务意图>")
        print()
        print("示例:")
        print("  python quickcep_business_intent_filter.py 投诉")
        print("  python quickcep_business_intent_filter.py 技术支持")
        return
    
    target_intent = sys.argv[1]
    
    if target_intent not in BUSINESS_INTENTS:
        print(f"❌ 未知的业务意图: {target_intent}")
        print(f"可用的业务意图: {', '.join(BUSINESS_INTENTS.keys())}")
        return
    
    print(f"🔍 筛选包含'{target_intent}'的会话...")
    print()
    
    # 获取会话
    cli_path = "/Users/arnold/.hermes/profiles/povison-cs/skills/social-media/quickcep/scripts/quickcep_cli.py"
    
    result = subprocess.run([
        sys.executable, cli_path, "sessions",
        "--email-only", "--page-size", "100"
    ], capture_output=True, text=True)
    
    if result.returncode != 0:
        print(f"Error: {result.stderr}")
        return
        
    try:
        data = json.loads(result.stdout)
        sessions = data.get("sessions", [])
        
        print(f"📊 总共 {len(sessions)} 个会话需要分析")
        print("🔍 开始业务意图分析...")
        print()
        
        filtered = filter_sessions_by_business_intent(sessions, target_intent, max_analyze=50)
        
        print(f"\n📊 找到 {len(filtered)} 个匹配'{target_intent}'的会话")
        print()
        
        if filtered:
            print("📋 前5个匹配的会话:")
            print()
            
            for i, session in enumerate(filtered[:5], 1):
                email = session.get("visitorInfo", {}).get("email", "N/A")
                business_intents = session.get('business_intents', [])
                official_intents = session.get("intentionTags") or []
                sentiment = session.get("sentimentTags") or []
                unread = session.get("unreadNum", 0)
                
                print(f"{i}. 📧 {email}")
                print(f"   业务意图: {', '.join(business_intents)}")
                print(f"   官方意图: {', '.join(official_intents) if official_intents else '无'}")
                print(f"   情感标签: {', '.join(sentiment) if sentiment else '无'}")
                print(f"   未读: {unread} | 会话ID: {session.get('id', '')}")
                print()
            
            if len(filtered) > 5:
                print(f"... 还有 {len(filtered) - 5} 个会话")
            
            # 保存结果
            filename = f'/tmp/business_intent_{target_intent}.json'
            with open(filename, 'w') as f:
                json.dump(filtered, f, indent=2, ensure_ascii=False)
            print(f"✅ 结果已保存到 {filename}")
            
    except json.JSONDecodeError as e:
        print(f"JSON解析错误: {e}")

if __name__ == "__main__":
    main()