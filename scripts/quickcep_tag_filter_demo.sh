#!/bin/bash
# QuickCEP CLI with Tag Filtering - Usage Demo

echo "🎯 QuickCEP 标签筛选功能使用指南"
echo "===================================="
echo ""

# 标签ID
PRODUCT_INQUIRY_ID="1715241229806047233"
LOGISTICS_INQUIRY_ID="1715248774713020417"

echo "📋 已识别的标签ID:"
echo "  产品咨询: $PRODUCT_INQUIRY_ID"
echo "  物流咨询: $LOGISTICS_INQUIRY_ID"
echo ""

# CLI路径
CLI_PATH="/Users/arnold/.hermes/profiles/povison-cs/skills/social-media/quickcep/scripts/quickcep_cli.py"

echo "🚀 使用方法:"
echo ""
echo "1️⃣  筛选产品咨询会话:"
echo "   python $CLI_PATH sessions --email-only --compact | python3 -c \"
echo '     import json, sys; data = json.load(sys.stdin); 
echo '     sessions = [s for s in data.get(\"sessions\", []) if \"1715241229806047233\" in s.get(\"subSessionTags\", [])];
echo '     print(json.dumps({\"total\": len(sessions), \"sessions\": sessions}, indent=2))'
echo "   \""
echo ""

echo "2️⃣  筛选物流咨询会话:"
echo "   python $CLI_PATH sessions --email-only --compact | python3 -c \"
echo '     import json, sys; data = json.load(sys.stdin); 
echo '     sessions = [s for s in data.get(\"sessions\", []) if \"1715248774713020417\" in s.get(\"subSessionTags\", [])];
echo '     print(json.dumps({\"total\": len(sessions), \"sessions\": sessions}, indent=2))'
echo "   \""
echo ""

echo "3️⃣  筛选任一标签的会话 (OR逻辑):"
echo "   python $CLI_PATH sessions --email-only --compact | python3 -c \"
echo '     import json, sys; data = json.load(sys.stdin);
echo '     sessions = [s for s in data.get(\"sessions\", []) if any(tag in s.get(\"subSessionTags\", []) for tag in [\"1715241229806047233\", \"1715248774713020417\"])];
echo '     print(json.dumps({\"total\": len(sessions), \"sessions\": sessions}, indent=2))'
echo "   \""
echo ""

echo "4️⃣  筛选同时包含两个标签的会话 (AND逻辑):"
echo "   python $CLI_PATH sessions --email-only --compact | python3 -c \"
echo '     import json, sys; data = json.load(sys.stdin);
echo '     sessions = [s for s in data.get(\"sessions\", []) if all(tag in s.get(\"subSessionTags\", []) for tag in [\"1715241229806047233\", \"1715248774713020417\"])];
echo '     print(json.dumps({\"total\": len(sessions), \"sessions\": sessions}, indent=2))'
echo "   \""
echo ""

echo "💡 提示: 将上述命令保存为脚本，可批量处理多个会话"
echo ""

# 示例：测试筛选
echo "🧪 测试筛选产品咨询会话..."
echo "   python $CLI_PATH sessions --email-only --compact --page-size 10 | python3 -c \"
echo '     import json, sys; data = json.load(sys.stdin); 
echo '     sessions = [s for s in data.get(\"sessions\", []) if \"1715241229806047233\" in s.get(\"subSessionTags\", [])];
echo '     print(f\"找到 {len(sessions)} 个产品咨询会话\");
echo '     if sessions: print(\"第一个会话:\", json.dumps(sessions[0], indent=2))'
echo "   \""
echo ""

echo "📊 使用场景:"
echo "  - 优先处理特定类型客户咨询"
echo "  - 分析不同标签的会话分布"  
echo "  - 批量处理相似问题"
echo "  - 生成特定类别的会话报告"
echo ""

echo "🔧 高级用法:"
echo "  - 结合其他筛选条件: --unread-only, --sort lastMsgTime"
echo "  - 导出为CSV/Excel格式"
echo "  - 定期自动扫描并报警"
echo "  - 与现有工作流集成"
echo ""