#!/bin/bash
# Configure Povison SCM API with JWT token

echo "🔧 Povison SCM API 配置向导"
echo "=========================="
echo ""
echo "📋 从浏览器中获取JWT token:"
echo "1. 打开 https://scm.povison-inc.com/qualityManage/qualityPicture"
echo "2. 按F12 → Network标签"
echo "3. 执行查询操作"
echo "4. 找到 /srm/quality/check/detail/img/page 请求"
echo "5. 查看 Request Headers 中的 x-access-token"
echo ""

if [ -z "$1" ]; then
    echo "请提供JWT token:"
    read -p "x-access-token: " jwt_token
else
    jwt_token="$1"
fi

if [ -z "$jwt_token" ]; then
    echo "❌ JWT token不能为空"
    exit 1
fi

echo ""
echo "🔍 验证JWT token..."
python3 << EOF
import base64
import json
import sys

jwt_token = "$jwt_token"

try:
    parts = jwt_token.split('.')
    if len(parts) == 3:
        payload = parts[1]
        # Add padding if needed
        payload += '=' * (4 - len(payload) % 4)
        decoded = base64.urlsafe_b64decode(payload)
        data = json.loads(decoded)
        
        print("✅ JWT token格式正确")
        print(f"👤 用户名: {data.get('username', 'N/A')}")
        
        if 'exp' in data:
            from datetime import datetime
            exp_time = datetime.fromtimestamp(data['exp'])
            current_time = datetime.now()
            
            if exp_time > current_time:
                days_left = (exp_time - current_time).days
                print(f"⏰ 过期时间: {exp_time}")
                print(f"✅ Token有效 (还有{days_left}天)")
            else:
                print(f"❌ Token已过期: {exp_time}")
                sys.exit(1)
        else:
            print("⚠️  无法确定过期时间")
    else:
        print("❌ 无效的JWT格式")
        sys.exit(1)
        
except Exception as e:
    print(f"❌ JWT验证失败: {e}")
    sys.exit(1)
EOF

if [ $? -ne 0 ]; then
    echo ""
    echo "❌ JWT token验证失败，请检查token是否正确"
    exit 1
fi

echo ""
echo "💾 保存配置..."

# Add to environment file
env_file="$HOME/.hermes/profiles/povison-cs/.env"
mkdir -p "$(dirname "$env_file")"

# Remove existing JWT token entry
sed -i.bak '/^POVISON_SCM_JWT_TOKEN=/d' "$env_file" 2>/dev/null || true

# Add new JWT token
echo "POVISON_SCM_JWT_TOKEN='$jwt_token'" >> "$env_file"

echo "✅ 配置已保存到: $env_file"
echo ""

# Test the configuration
echo "🧪 测试配置..."
export POVISON_SCM_JWT_TOKEN="$jwt_token"

python3 << 'EOF'
import os
import sys
sys.path.insert(0, '/Users/arnold/agent_prj/hermes-agent')

# Test the API client
try:
    from scripts.povison_scm_api import PovisonSCMApiClient
    
    api = PovisonSCMApiClient()
    
    print("📡 测试API连接...")
    response = api.query_images(psku="TEST", page_size=1)
    
    if response:
        print("✅ API连接成功")
        print(f"📊 响应类型: {type(response)}")
        if isinstance(response, dict):
            if response.get("success") == False:
                print(f"⚠️  API返回错误: {response.get('error', 'Unknown error')}")
            else:
                print("✅ API响应正常")
    else:
        print("❌ API连接失败")
        
except Exception as e:
    print(f"❌ 测试失败: {e}")
    import traceback
    traceback.print_exc()

EOF

echo ""
echo "🎉 配置完成！"
echo ""
echo "📖 使用方法:"
echo "  python3 scripts/povison_scm_api.py query --psku <SKU>"
echo "  python3 scripts/povison_scm_api.py urls --psku <SKU>"
echo ""
echo "📋 测试命令:"
echo "  python3 scripts/povison_scm_api.py query --psku 8033"
echo ""