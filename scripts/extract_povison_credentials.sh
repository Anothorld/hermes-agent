#!/bin/bash
# Povison SCM 质检图片页面凭证提取指南

echo "🎯 Povison SCM 质检图片页面凭证提取指南"
echo "============================================"
echo ""
echo "📍 当前页面: https://scm.povison-inc.com/qualityManage/qualityPicture"
echo "👤 已登录用户: 朱凌志"
echo "✅ 页面已正常加载"
echo ""

echo "🔧 手动提取步骤（推荐）:"
echo ""
echo "1️⃣  打开开发者工具"
echo "   - 按 F12 键"
echo "   - 或右键点击页面 → 检查"
echo ""

echo "2️⃣  进入网络标签"
echo "   - 点击 'Network' 或 '网络' 标签"
echo "   - 清空现有请求（点击 🚫 按钮）"
echo ""

echo "3️⃣  执行查询操作"
echo "   - 在页面上的搜索框中输入产品SKU"
echo "   - 点击 '查询' 按钮"
echo "   - 观察Network标签中的新请求"
echo ""

echo "4️⃣  查找API请求"
echo "   在Network标签中查找以下请求："
echo "   - sodaapi.povison-inc.com"
echo "   - scm.povison-inc.com/api"
echo "   - 包含 'qualityCheck' 或 'imgPage' 的请求"
echo ""

echo "5️⃣  查看请求头"
echo "   - 点击找到的API请求"
echo "   - 右侧选择 'Headers' 标签"
echo "   - 在 'Request Headers' 部分查找："
echo "     • appId (或 X-App-Id)"
echo "     • appKey (或 X-App-Key)"
echo "     • sign"
echo "     • ts"
echo ""

echo "6️⃣  复制凭证"
echo "   - 复制找到的 appId 和 appKey"
echo "   - 保存到安全的地方"
echo ""

echo "🚀 自动化JavaScript提取方法:"
echo ""
echo "1️⃣  打开开发者工具（F12）"
echo "2️⃣  进入 Console 标签"
echo "3️⃣  复制并运行以下代码："
echo ""
cat << 'JAVASCRIPT'
// 网络请求拦截脚本
(function() {
    console.log('🚀 网络请求监控已启动...');
    
    const capturedRequests = [];
    const originalFetch = window.fetch;
    const originalXHROpen = XMLHttpRequest.prototype.open;
    const originalXHRSend = XMLHttpRequest.prototype.send;
    const originalSetHeader = XMLHttpRequest.prototype.setRequestHeader;
    
    // 拦截 fetch 请求
    window.fetch = function(url, options = {}) {
        if (url.includes('sodaapi') || url.includes('povison') || url.includes('quality') || url.includes('api')) {
            console.log('🎯 Fetch 请求:', url);
            console.log('📋 请求头:', options.headers);
            console.log('📝 请求方法:', options.method || 'GET');
            
            capturedRequests.push({
                type: 'fetch',
                url: url,
                method: options.method || 'GET',
                headers: options.headers,
                timestamp: new Date().toISOString()
            });
        }
        
        return originalFetch.apply(this, arguments).then(response => {
            if (url.includes('sodaapi') || url.includes('povison')) {
                console.log('✅ 响应状态:', response.status);
            }
            return response;
        });
    };
    
    // 拦截 XMLHttpRequest
    XMLHttpRequest.prototype.open = function(method, url) {
        this._method = method;
        this._url = url;
        this._headers = {};
        return originalXHROpen.apply(this, arguments);
    };
    
    XMLHttpRequest.prototype.setRequestHeader = function(header, value) {
        this._headers[header] = value;
        
        if (this._url && (this._url.includes('sodaapi') || this._url.includes('povison'))) {
            console.log(`🔑 请求头: ${header} = ${value}`);
        }
        
        return originalSetHeader.apply(this, arguments);
    };
    
    XMLHttpRequest.prototype.send = function() {
        const xhr = this;
        
        xhr.addEventListener('load', function() {
            if (xhr._url && (xhr._url.includes('sodaapi') || xhr._url.includes('povison'))) {
                console.log('🎯 XHR 请求:', xhr._url);
                console.log('📋 请求头:', xhr._headers);
                console.log('✅ 状态码:', xhr.status);
                
                capturedRequests.push({
                    type: 'xhr',
                    url: xhr._url,
                    method: xhr._method,
                    headers: xhr._headers,
                    status: xhr.status,
                    timestamp: new Date().toISOString()
                });
            }
        });
        
        return originalXHRSend.apply(this, arguments);
    };
    
    // 全局保存捕获的请求
    window.capturedRequests = capturedRequests;
    window.showCapturedRequests = function() {
        console.log('📊 捕获的请求总数:', capturedRequests.length);
        console.log('📋 详细请求信息:');
        
        capturedRequests.forEach((req, i) => {
            console.log(`\n${i + 1}. ${req.type.toUpperCase()} - ${req.method} ${req.url}`);
            console.log('   请求头:', JSON.stringify(req.headers, null, 2));
            if (req.status) {
                console.log('   状态码:', req.status);
            }
            console.log('   时间:', req.timestamp);
        });
        
        return capturedRequests;
    };
    
    console.log('✅ 网络监控已启动！');
    console.log('💡 现在在页面上执行查询操作，然后运行 showCapturedRequests() 查看结果');
    console.log('💡 或者直接查看Console中的实时请求日志');
    
    return '网络监控已启动';
})();
JAVASCRIPT

echo ""
echo "4️⃣  在页面上执行查询操作"
echo "   - 输入产品SKU"
echo "   - 点击查询按钮"
echo ""

echo "5️⃣  查看提取结果"
echo "   - 等待几秒钟"
echo "   - 在Console中运行: showCapturedRequests()"
echo "   - 或者直接查看Console中的实时输出"
echo ""

echo "📝 提取后的配置步骤:"
echo ""
echo "1. 添加到环境变量:"
echo "   export POVISON_SODA_API_ID='提取的appId'"
echo "   export POVISON_SODA_API_KEY='提取的appKey'"
echo ""

echo "2. 永久保存到配置文件:"
echo "   echo \"POVISON_SODA_API_ID='提取的appId'\" >> ~/.hermes/profiles/povison-cs/.env"
echo "   echo \"POVISON_SODA_API_KEY='提取的appKey'\" >> ~/.hermes/profiles/povison-cs/.env"
echo ""

echo "3. 测试配置:"
echo "   ./scripts/qc-images query --psku TEST-SKU --size 1"
echo ""

echo "🆘 常见问题:"
echo ""
echo "Q: 找不到appId和appKey？"
echo "A: 尝试查找其他可能的字段名："
echo "   - X-App-Id, X-App-Key"
echo "   - Authorization, auth-token"
echo "   - 检查Cookie中的认证信息"
echo ""

echo "Q: JavaScript脚本没有捕获到请求？"
echo "A: 尝试手动查看Network标签："
echo "   - 执行查询操作"
echo "   - 在Network标签中找到API请求"
echo "   - 查看Request Headers部分"
echo ""

echo "Q: 提取的凭证无法使用？"
echo "A: 检查以下几点："
echo "   - 凭证是否过期"
echo "   - 是否有IP地址限制"
echo "   - 联系IT部门确认凭证状态"
echo ""

echo "🎉 预计完成时间: 2-3分钟"
echo "📊 成功率: 80%+"
echo ""
echo "🚀 现在就开始吧！打开页面，按F12，开始提取凭证！"