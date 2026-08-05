# 手测跑测文档 — Editorial 文案 / Agent 重选 / 解析链接

适用改动：editorial H2/总览文案、「重新挑选植入候选」走 Agent、「解析链接」一键填卡；客户端 TOC 含 `#povison-picks`；parse-card 拒绝 CoT 泄漏文案。

## 前置条件

1. 重启 SEO Studio Bridge（加载最新 UI/服务端）：
   ```bash
   cd hermes-agent/playground/seo-studio && ./start.sh restart
   ```
2. Gateway（povison-seo profile）在线，浏览器打开 Studio UI。
3. 准备一个 **已完成正文** 的任务；建议用 **editorial** 样式。
4. 准备 1 条真实 POVISON 商详 URL（用于「解析链接」）。

## 用例 T1 — 解析链接（editorial）

1. 打开任务 → **产品与内链** → 样式选 **编辑式评测卡 (editorial)**。
2. 确保至少有 1 张产品卡；在「产品链接」填入真实 PDP URL。
3. 点该卡 **解析链接**，等待 toast「已解析填入…」。
4. **肉眼检查**：产品名、配图、规格、买家评价（可选）、评测文案（约 90–150 词）是否填入。

**通过标准（UI）**：有 name + image；editorial 下 ideally 有 specs + blurb；review 可空。  
**blurb 质量**：必须是可发布的产品文案（约 90–150 词），**不能**出现「The user wants me to… / Let me analyze / Word count / Paragraph 1」这类模型推理过程。若出现，重新点「解析链接」（服务端会拒绝超长/CoT 文本）。

## 用例 T2 — 解析链接（inline，可选对照）

1. 切到 **行内植入 (inline)**（注意：切换会清空产品卡，需重新加 URL）。
2. 填 URL → **解析链接**。
3. **肉眼检查**：name/image/blurb（40–70 词）；不应出现规格/评价字段被强制写入（UI 不展示即可）。

## 用例 T3 — 重新挑选植入候选（Agent）

1. 保持 editorial（或 inline），正文已完成（`phaseDone.sections` 为真，且无 `stale.sections === placementStyle`）。
2. 点 **重新挑选植入候选**。
3. **肉眼检查**：`placementsLive` 出现 Agent 进度；完成后卡片刷新；**不是**瞬间塞入 demo catalog。

## 用例 T4 — editorial H2 / 总览 + 目录 + 预览

1. 在 editorial 总览区填写：
   - **H2**：描述性标题（如 `Best media console picks for OLED TV setup`）。
   - **总览**：50–70 词，末尾含「尺寸/功能/价格以商详页为准」（或英文等价 disclaimer）。
2. **接受恰好 3 张**产品卡。
3. 点 **组装预览（本地）**（修改后需重新组装才会刷新 `previewHtml`）。
4. **肉眼检查**：
   - 正文出现 `id="povison-picks"` 的 H2，标题=你填的 editorialTitle（非空时）。
   - 总览段词数大致 50–70，含 disclaimer。
   - 目录 TOC 含同一标题条目，可锚到 `#povison-picks`（位于 Conclusion 之前）。

## 用例 T5 — 降级（可选）

1. 产品卡填一个明显无效 URL → **解析链接**。
2. **期望**：toast 失败提示；页面不白屏。

## 建议覆盖

至少跑完 **T1 + T3 + T4**；T2/T5 可选。
