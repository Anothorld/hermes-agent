#!/usr/bin/env python3
"""
QuickCEP 业务意图内容分析工具
基于邮件消息内容分析业务意图
"""

import json
import subprocess
import sys
import re

# 业务意图关键词映射
BUSINESS_INTENTS = {
    "询价": ["inquiry", "quote", "price", "cost", "how much", "pricing"],
    "投诉": ["complaint", "issue", "problem", "broken", "damaged", "not working"],
    "售后": ["return", "refund", "exchange", "warranty", "replacement"],
    "技术支持": ["support", "help", "technical", "install", "setup", "assembly"],
    "配送咨询": ["delivery", "shipping", "tracking", "logistics", "when will"],
    "产品咨询": ["product", "item", "specification", "size", "color", "material"],
    "支付问题": ["payment", "billing", "invoice", "credit card", "charge"],
    "订单问题": ["order", "status", "cancel", "modify", "change"]
}

def analyze_session_messages(session_id):
    """分析单个会话的消息内容"""
    cli_path = "/Users/arnold/.hermes/profiles/povison-cs/skills/social-media/quickcep/scripts/quickcep_cli.py"
    
    result = subprocess.run([
        sys.executable, cli_path, "messages", str(session_id),
        "--plain"
    ], capture_output=True, text=True)
    
    if result.returncode != 0:
        return None
        
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
        
        return all_content.lower()
        
    except json.JSONDecodeError as e:
        print(f"JSON解析错误: {e}")
        return None

def detect_business_intent(text):
    """检测业务意图"""
    detected_intents = []
    
    for intent, keywords in BUSINESS_INTENTS.items():
        for keyword in keywords:
            if keyword.lower() in text:
                if intent not in detected_intents:
                    detected_intents.append(intent)
                break
    
    return detected_intents

def analyze_sessions_business_intents(sessions):
    """批量分析会话的业务意图"""
    results = []
    
    for i, session in enumerate(sessions):
        session_id = session.get("id")
        email = session.get("visitorInfo", {}).get("email", "N/A")
        
        print(f"分析会话 {i+1}/{len(sessions)}: {email}")
        
        content = analyze_session_messages(session_id)
        if content:
            intents = detect_business_intent(content)
            results.append({
                "session_id": session_id,
                "email": email,
                "detected_intents": intents,
                "existing_intentionTags": session.get("intentionTags") or [],
                "existing_sentimentTags": session.get("sentimentTags") or []
            })
    
    return results

def main():
    print("🔍 QuickCEP 业务意图内容分析工具")
    print("=" * 40)
    print()
    
    # 获取一些会话样本
    cli_path = "/Users/arnold/.hermes/profiles/povison-cs/skills/social-media/quickcep/scripts/quickcep_cli.py"
    
    print("📊 获取会话样本...")
    result = subprocess.run([
        sys.executable, cli_path, "sessions",
        "--email-only", "--page-size", "10"
    ], capture_output=True, text=True)
    
    if result.returncode != 0:
        print(f"Error: {result.stderr}")
        return
        
    try:
        data = json.loads(result.stdout)
        sessions = data.get("sessions", [])
        
        print(f"✅ 获取了 {len(sessions)} 个会话")
        print("🔍 分析消息内容中的业务意图...")
        print()
        
        results = analyze_sessions_business_intents(sessions)
        
        # 统计分析
        print("📈 业务意图统计:")
        print("-" * 40)
        
        intent_counts = {}
        for result in results:
            for intent in result["detected_intents"]:
                intent_counts[intent] = intent_counts.get(intent, 0) + 1
        
        for intent, count in sorted(intent_counts.items(), key=lambda x: x[1], reverse=True):
            print(f"• {intent}: {count} 个会话")
        
        print()
        print("📋 详细分析结果:")
        print("-" * 40)
        
        for i, result in enumerate(results[:5], 1):  # 只显示前5个
            print(f"{i}. 📧 {result['email']}")
            print(f"   检测到的业务意图: {', '.join(result['detected_intents']) if result['detected_intents'] else '无'}")
            print(f"   现有意图标签: {', '.join(result['existing_intentionTags']) if result['existing_intentionTags'] else '无'}")
            print(f"   现有情感标签: {', '.join(result['existing_sentimentTags']) if result['existing_sentimentTags'] else '无'}")
            print()
        
        # 保存结果
        with open('/tmp/business_intent_analysis.json', 'w') as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        
        print(f"✅ 完整结果已保存到 /tmp/business_intent_analysis.json")
        
    except json.JSONDecodeError as e:
        print(f"JSON解析错误: {e}")

if __name__ == "__main__":
    main()