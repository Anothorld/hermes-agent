# DingTalk Docs & Wiki Plugin

Hermes Agent plugin for reading and searching DingTalk documents and Wiki spaces.

## Tools Provided

| Tool | Description |
|------|-------------|
| `dingtalk_doc_get_content` | 读取文档内容（支持 doc_id 或 URL） |
| `dingtalk_doc_search` | 搜索钉钉文档 |
| `dingtalk_wiki_list_workspaces` | 列出知识库空间 |
| `dingtalk_wiki_list_nodes` | 列出知识库下的文档节点 |
| `dingtalk_wiki_get_node` | 获取文档节点详情 |
| `dingtalk_doc_list_recents` | 列出最近访问的文档 |

## Configuration

Requires environment variables (already set for DingTalk messaging):

```bash
DINGTALK_CLIENT_ID=dingk8dzfvefvpv0kseh
DINGTALK_CLIENT_SECRET=your_secret
```

## Required API Permissions

The DingTalk app needs these permissions in the open platform:

- **文档读取** (`doc_read`) - Read document content
- **知识库读取** (`wiki_read`) - Read wiki spaces and nodes
- **文档搜索** (`doc_search`) - Search documents
- **最近文档** (`doc_recents`) - List recent documents

## Usage Examples

```
# 搜索文档
dingtalk_doc_search(keyword="产品需求")

# 列出知识库
dingtalk_wiki_list_workspaces()

# 通过 URL 读取文档
dingtalk_doc_get_content(url="https://wiki.dingtalk.com/...")

# 浏览知识库目录
dingtalk_wiki_list_nodes(workspace_id="xxx")
```
