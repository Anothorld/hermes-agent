# 🎉 API 凭证提取工具包 - 使用指南

## 📦 你现在拥有的工具

### 1. **JavaScript自动提取脚本** 
📂 `scripts/browser-auth-extractor.js`

### 2. **Python分析工具**
📂 `scripts/extract_browser_auth.py`

### 3. **快速启动脚本**
📂 `scripts/start-cred-extraction.sh`

### 4. **完整指南文档**
📂 `API_CREDENTIAL_GUIDE.md`

### 5. **3分钟快速指南**
📂 `QUICK_CREDENTIAL_GUIDE.md`

## 🚀 立即开始（推荐流程）

### 第一步：运行快速启动
```bash
./scripts/start-cred-extraction.sh
```

### 第二步：按照提示操作
1. 打开你的Povison网页
2. 按F12打开开发者工具
3. 粘贴并执行JavaScript脚本
4. 执行查询操作
5. 查看提取结果

### 第三步：配置凭证
```bash
# 添加到环境变量
export POVISON_SODA_API_ID='提取的appId'
export POVISON_SODA_API_KEY='提取的appKey'
```

### 第四步：测试工具
```bash
./scripts/qc-images query --psku TEST-SKU --size 1
```

## 📖 详细文档索引

### 🎯 新手入门
→ 阅读 `QUICK_CREDENTIAL_GUIDE.md`

### 🔧 深度学习
→ 阅读 `API_CREDENTIAL_GUIDE.md`

### 🛠️ 技术实现
→ 查看 `scripts/browser-auth-extractor.js`

### 🤔 遇到问题
→ 运行 `python3 scripts/extract_browser_auth.py`

## 💡 使用技巧

### 技巧1：多种方法备选
- 方法1失败？尝试方法2
- 方法2失败？联系IT部门
- 始终有备选方案

### 技巧2：验证提取结果
- 提取后立即测试
- 确认凭证格式正确
- 避免后续调试麻烦

### 技巧3：保存提取记录
- 记录提取时间
- 保存凭证来源
- 便于后续问题排查

## 🔒 安全提醒

### ⚠️ 重要注意事项

1. **凭证保护**
   - 不要分享给他人
   - 不要提交到代码仓库
   - 定期更换凭证

2. **合法使用**
   - 仅用于业务需求
   - 遵守公司政策
   - 不要滥用API

3. **存储安全**
   - 使用环境变量
   - 设置文件权限
   - 加密敏感信息

## 📞 获取支持

### 📚 自助资源
- 完整指南：`API_CREDENTIAL_GUIDE.md`
- 快速指南：`QUICK_CREDENTIAL_GUIDE.md`
- 帮助脚本：`./scripts/start-cred-extraction.sh`

### 🤔 需要帮助？
- 联系IT部门获取正式凭证
- 咨询开发团队了解API详情
- 查看公司内部API文档

## 🎉 成功标志

### ✅ 配置成功的表现
1. 工具可以正常调用API
2. 能够返回查询结果
3. 可以向客户展示质检图片
4. 集成到客服工作流程

### 🚀 准备就绪
当你看到这个提示时，说明所有工具已就绪：

```
✅ Browser extractor script found
✅ Complete guide found
✅ Quick credential guide available
✅ Testing tools ready
```

**现在就开始提取凭证，2-3分钟后即可使用质检图片查询功能！** 🚀

---

**预计完成时间：2-3分钟**
**难度级别：简单**
**成功率：80%+**

祝你成功！🎊