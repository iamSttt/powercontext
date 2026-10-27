# Qoder 赛题提交材料：PaperLens 论文速读助手

## 项目信息

| 项目 | 内容 |
|---|---|
| 项目名称 | PaperLens · 论文速读助手 |
| 一句话介绍 | 导入论文后自动分块写入 seekdb，用自然语言提问即得到带原文引用的回答 |
| 项目网站（Qoder Sites） | **https://paperlens-dn11pqe5vk6.qoder.zone**（已发布，未登录可访问） |
| 代码位置 | 本仓库 `qoder/paperlens/` |
| Qoder 账号 UID | `01a17d7d-ff7c-7bd9-9c16-4e8f6f9223da` |
| 技术栈 | FastAPI + 原生前端 + **seekdb（IK 全文索引）** + **MiniMax-M3.1-Flash** |

## 技术分工

| 组成 | 作用 |
|---|---|
| **Qoder** | 项目主要开发工作（需求拆解、代码生成、调试与页面迭代），开发记录见提交材料中的截图 |
| **seekdb** | 唯一数据层：论文分块与问答历史的存储；检索全部下推为 `MATCH ... AGAINST`（IK 中文分词），应用内存不参与检索 |
| **MiniMax** | `MiniMax-M3.1-Flash` 经 OpenAI 兼容接口生成带 `[n]` 引用的回答 |

## 运行方式

见 [`paperlens/README.md`](paperlens/README.md)，核心三步：

```bash
docker start seekdb
pip install -r requirements.txt
python -m uvicorn app.main:app --port 8000
```

`.env` 中填入 `MINIMAX_API_KEY`（仓库不含任何密钥）。

## 验证样例（实测，2026-10-27）

| 步骤 | 实际结果 |
|---|---|
| `GET /api/health` | `{"ok":true,"version":"5.7.25-OceanBase seekdb-v1.4.0.0","chunks":10,"papers":2}` |
| 导入示例论文 | 2 篇 / 10 个段落写入 seekdb |
| 提问「论文用了什么方法？」 | 251 字回答、2 条引用（`degraded=false`，走 MiniMax） |
| `GET /api/history` | 返回 5 条历史，刷新页面后仍在（seekdb `qa_history`） |

原始证据：`paperlens/records/paperlens-ask.json`、`paperlens/records/paperlens-history.json`；
页面截图：`paperlens/records/_final-*.png`、`_shot-*.png`。

## Qoder 开发记录

- 账号 UID：`01a17d7d-ff7c-7bd9-9c16-4e8f6f9223da`
- 开发对话截图：由参赛者随提测材料上传（需求拆解 / 代码生成 / 调试修复）

## 提交时填写的字段

| 平台字段 | 值 |
|---|---|
| 项目网站网址 | `https://paperlens-dn11pqe5vk6.qoder.zone` |
| 代码仓库地址 | `https://github.com/iamSttt/powercontext`（目录 `qoder/paperlens/`） |
| Qoder 账号 UID | `01a17d7d-ff7c-7bd9-9c16-4e8f6f9223da` |
| 其余材料 | 运行说明 + 验证样例 + 开发记录截图 |
