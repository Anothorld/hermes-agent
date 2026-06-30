#!/bin/bash
# Configure Povison Quality Check API with provided credentials

echo "🔧 Povison Quality Check API 配置"
echo "=============================="
echo ""

# Use the provided credentials
APP_ID="scm"
APP_KEY="sdg4W8A8xX2e3dfaGpn6ylVygo9f9H"

echo "📋 使用凭证:"
echo "  appId: $APP_ID"
echo "  appKey: ${APP_KEY:0:10}..."
echo ""

# Save to environment file
ENV_FILE="/Users/arnold/.hermes/profiles/povison-cs/.env"
mkdir -p "$(dirname "$ENV_FILE")"

# Remove existing entries
sed -i.bak '/^POVISON_API_ID=/d' "$ENV_FILE" 2>/dev/null || true
sed -i.bak '/^POVISON_API_KEY=/d' "$ENV_FILE" 2>/dev/null || true

# Add new entries
echo "POVISON_API_ID='$APP_ID'" >> "$ENV_FILE"
echo "POVISON_API_KEY='$APP_KEY'" >> "$ENV_FILE"

echo "✅ 凭证已保存到: $ENV_FILE"
echo ""

# Test the configuration
echo "🧪 测试API连接..."
export POVISON_API_ID="$APP_ID"
export POVISON_API_KEY="$APP_KEY"

cd /Users/arnold/agent_prj/hermes-agent

# Test with a simple query
echo "📡 测试查询..."
python3 scripts/quality_check_api.py query --psku TEST-SKU --size 1

echo ""
echo "🎉 配置完成！"
echo ""
echo "📖 使用方法:"
echo "  python3 scripts/quality_check_api.py query --psku <SKU>"
echo "  python3 scripts/quality_check_api.py urls --psku <SKU>"
echo ""
echo "🚀 开始使用吧！"