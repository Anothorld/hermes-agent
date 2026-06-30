# Povison API 凭证获取指南

## 🎯 目标
从现有的网页查询系统中提取 appId 和 appKey，用于配置质检图片API工具。

## 📋 方法一：浏览器开发者工具（推荐）

### 步骤详解

#### 1. 打开网页
```
访问你的Povison质检图片查询网页
```

#### 2. 打开开发者工具
```
按 F12 键 或 右键 → 检查
```

#### 3. 进入网络标签
```
点击 "Network" 或 "网络" 标签
```

#### 4. 清空现有请求
```
点击清除按钮（🚫图标）清空之前的请求记录
```

#### 5. 执行查询操作
```
在网页上执行一次产品质检图片查询
```

#### 6. 查找API请求
```
在Network标签中查找以下请求：
- sodaapi.povison-inc.com
- /api/scm/qualityCheck/imgPage
```

#### 7. 查看请求头
```
点击找到的API请求 → 右侧查看 "Headers" 部分
```

#### 8. 提取凭证
```
在Request Headers中查找：
- appId (或 X-App-Id)
- appKey (或 X-App-Key) 
- sign (这是签名，用于验证算法是否正确)
- ts (时间戳)
```

### 📸 示例截图位置

```
Network Tab
├── All
├── Doc
├── XHR
├── JS
└── 📋 sodaapi.povison-inc.com/api/scm/qualityCheck/imgPage ← 点击这个
    └── Headers Tab
        ├── General
        ├── Request Headers        ← 在这里找 appId 和 appKey
        ├── Query String Parameters
        └── Response Headers
```

## 🚀 方法二：使用JavaScript自动提取

### 快速步骤

#### 1. 打开网页
```
访问你的Povison质检图片查询网页
```

#### 2. 打开控制台
```
按 F12 → Console 标签
```

#### 3. 复制并运行脚本
```
复制 scripts/browser-auth-extractor.js 的内容
粘贴到Console中，按Enter执行
```

#### 4. 执行查询
```
在网页上执行一次查询操作
```

#### 5. 查看结果
```
等待10秒或手动运行: showExtractionResults()
```

### 📝 脚本位置
```
/Users/arnold/agent_prj/hermes-agent/scripts/browser-auth-extractor.js
```

## 🔍 方法三：浏览器存储分析

### 步骤

#### 1. 打开开发者工具
```
F12 → Application 标签
```

#### 2. 检查本地存储
```
Application → Local Storage → 你的网站域名
```

#### 3. 查找凭证
```
搜索包含以下关键词的键：
- appId
- appKey
- API_KEY
- TOKEN
```

## 🧪 方法四：网络请求拦截（高级）

### 使用浏览器扩展

#### 1. 安装扩展
```
推荐扩展：
- Request Inspector
- HTTP Headers
- Tamper Data (Firefox)
```

#### 2. 配置拦截
```
设置拦截规则：
- URL模式: *.sodaapi.povison-inc.com/*
- 拦截请求和响应头
```

#### 3. 执行查询并查看
```
执行查询 → 扩展会显示完整的请求头信息
```

## 🛠️ 验证提取的凭证

### 测试步骤

#### 1. 设置环境变量
```bash
export POVISON_SODA_API_ID='提取的appId'
export POVISON_SODA_API_KEY='提取的appKey'
```

#### 2. 测试API调用
```bash
./scripts/qc-images query --psku TEST-SKU --size 1
```

#### 3. 检查结果
```
✅ 成功：返回API数据或认证错误（说明凭证格式正确）
❌ 失败：网络错误或凭证缺失
```

## 📋 常见问题和解决方案

### 问题1: 找不到appId或appKey

**可能原因：**
- 凭证在其他地方（如服务器端生成）
- 使用了不同的认证方式
- 凭证在Cookie中

**解决方案：**
```bash
# 检查Cookie
F12 → Application → Cookies → 查找认证相关的Cookie

# 检查其他可能的头部名称
- Authorization
- X-API-KEY
- api-key
- x-auth-token
```

### 问题2: 找到sign但找不到原始appKey

**说明：**
sign是通过HMAC-SHA256算法生成的签名，格式为：
```
Base64(HMAC-SHA256(appKey, "appId:data64:timestamp"))
```

**解决方法：**
- sign无法逆向得出appKey
- 必须找到原始的appKey
- 联系IT部门获取正式API凭证

### 问题3: 找到的凭证无法使用

**可能原因：**
- 凭证过期
- IP地址限制
- 凭证权限不足

**解决方案：**
```bash
# 检查凭证时效性
# 联系IT部门确认凭证状态
# 申请专门的API访问权限
```

## 🔐 安全注意事项

### ⚠️ 重要提醒

1. **凭证敏感性**
   - appId 和 appKey 相当于登录凭证
   - 不要分享给未授权人员
   - 定期轮换这些凭证

2. **合法使用**
   - 仅用于业务相关的API调用
   - 遵守公司数据使用政策
   - 不要用于个人项目

3. **存储安全**
   - 不要提交到公开的代码仓库
   - 使用环境变量存储
   - 限制文件访问权限

### 安全存储建议

```bash
# 设置正确的文件权限
chmod 600 ~/.hermes/profiles/povison-cs/.env

# 确认只有你有权限读取
ls -la ~/.hermes/profiles/povison-cs/.env
```

## 📞 获取帮助

### 如果仍然无法获取凭证

#### 1. 联系IT部门
```
申请正式的API访问权限
说明使用目的：质检图片查询客服工具
```

#### 2. 查看内部文档
```
搜索公司内部：
- API文档
- 开发者指南
- 系统架构文档
```

#### 3. 询问开发团队
```
直接联系负责质检系统的开发人员
询问API接入流程和凭证获取方式
```

## 🚀 配置完成后的验证

### 完整验证流程

#### 1. 添加凭证到环境变量
```bash
# 编辑环境文件
vim ~/.hermes/profiles/povison-cs/.env

# 添加以下行
POVISON_SODA_API_ID='your-app-id'
POVISON_SODA_API_KEY='your-app-key'
```

#### 2. 重新加载环境
```bash
source ~/.hermes/profiles/povison-cs/.env
```

#### 3. 测试工具
```bash
cd /Users/arnold/agent_prj/hermes-agent
./scripts/qc-images query --psku TEST-SKU --size 1
```

#### 4. 检查结果
```
如果看到以下任一结果，说明配置成功：
- API响应数据（即使没有结果）
- 认证相关的错误信息
- 网络连接成功的信息

如果看到凭证缺失错误，说明配置失败
```

## 📊 提取成功率对比

| 方法 | 成功率 | 难度 | 推荐度 |
|------|--------|------|--------|
| 开发者工具手动查看 | 80% | 中等 | ⭐⭐⭐⭐⭐ |
| JavaScript自动提取 | 60% | 简单 | ⭐⭐⭐⭐ |
| 浏览器存储分析 | 40% | 简单 | ⭐⭐⭐ |
| 网络请求拦截 | 90% | 较难 | ⭐⭐⭐⭐ |

---

**建议：** 先尝试方法一（开发者工具），如果失败再尝试方法二（JavaScript脚本），最后联系IT部门获取正式凭证。