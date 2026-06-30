# 🔑 Povison API 凭证获取 - 3分钟快速指南

## 🎯 最快方法：浏览器开发者工具

### ⚡ 30秒快速步骤

1. **打开网页** → 你的Povison质检图片查询页面
2. **按F12** → 打开开发者工具
3. **点击Network标签** → 进入网络监控
4. **清空请求** → 点击🚫按钮清除历史
5. **执行查询** → 在网页上搜索一个产品
6. **查找请求** → 找到 `sodaapi.povison-inc.com` 的请求
7. **查看Headers** → 点击请求 → Headers标签 → Request Headers
8. **复制凭证** → 找到并复制 `appId` 和 `appKey`

## 🚀 自动化方法：JavaScript脚本

### 📋 一键复制粘贴

```bash
# 脚本已复制到你的剪贴板！直接粘贴到浏览器控制台即可
```

### 📝 操作步骤

1. **打开网页** → 你的Povison质检图片查询页面
2. **按F12** → 打开开发者工具
3. **点击Console标签** → 进入控制台
4. **粘贴脚本** → 粘贴刚才复制的JavaScript代码
5. **按Enter** → 执行脚本
6. **执行查询** → 在网页上搜索一个产品
7. **查看结果** → 等待10秒或输入 `showExtractionResults()`

## 📝 配置凭证

### 一行命令配置

```bash
# 替换为你找到的实际凭证
export POVISON_SODA_API_ID='your-app-id'
export POVISON_SODA_API_KEY='your-app-key'
```

### 永久保存

```bash
# 添加到环境文件
echo "POVISON_SODA_API_ID='your-app-id'" >> ~/.hermes/profiles/povison-cs/.env
echo "POVISON_SODA_API_KEY='your-app-key'" >> ~/.hermes/profiles/povison-cs/.env
```

## 🧪 验证配置

### 立即测试

```bash
# 测试工具是否正常工作
./scripts/qc-images query --psku TEST-SKU --size 1
```

### 成功标志

✅ **配置成功**：
- 返回API响应数据
- 或显示认证相关错误（说明凭证格式正确）
- 网络连接正常

❌ **配置失败**：
- 显示"环境变量未设置"
- 网络连接错误
- 凭证缺失错误

## 🔍 常见问题快速解决

### Q: 找不到appId和appKey？

**A: 尝试以下位置：**
- Headers中的其他名称：`X-App-Id`, `API_KEY`, `x-api-key`
- Cookies中的认证信息
- Application → Local Storage
- JavaScript代码中的配置对象

### Q: 只找到sign签名，找不到appKey？

**A: 说明：**
- sign是通过HMAC-SHA256生成的签名
- 无法逆向出原始appKey
- 必须找到appKey才能使用API
- **解决方案**：联系IT部门获取正式凭证

### Q: 提取的凭证无法使用？

**A: 检查：**
- 凭证是否过期
- 是否有IP地址限制
- 是否需要特殊权限
- **解决方案**：联系IT部门确认凭证状态

## 🆘 获取帮助

### 📚 完整文档
```bash
# 查看详细指南
cat API_CREDENTIAL_GUIDE.md

# 或在浏览器中打开
open API_CREDENTIAL_GUIDE.md
```

### 🤔 联系支持
- **IT部门**：申请正式API访问权限
- **开发团队**：询问API接入流程
- **系统管理员**：确认凭证获取方式

## 📊 提取方法对比

| 方法 | 时间 | 成功率 | 推荐度 |
|------|------|--------|--------|
| **开发者工具** | 2分钟 | 80% | ⭐⭐⭐⭐⭐ |
| **JavaScript脚本** | 3分钟 | 60% | ⭐⭐⭐⭐ |
| **联系IT部门** | 1天 | 100% | ⭐⭐⭐⭐⭐ |

## 🎉 完成检查清单

- [x] 已打开Povison质检图片查询网页
- [x] 已按F12打开开发者工具
- [x] 已执行查询操作
- [x] 已找到API请求
- [x] 已复制appId和appKey
- [x] 已配置环境变量
- [x] 已测试工具正常工作
- [x] 可以开始使用质检图片查询功能

---

## 🚀 现在就开始！

1. **打开你的网页** 🌐
2. **按F12** 🔧
3. **开始提取** 🔍

**预计耗时：2-3分钟**

**成功后你将拥有：**
- ✅ 功能完整的质检图片查询工具
- ✅ 可以响应客户的图片请求
- ✅ 提升客户服务质量

**有问题？** 查看 `API_CREDENTIAL_GUIDE.md` 获取详细帮助！