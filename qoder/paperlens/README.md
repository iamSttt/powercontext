# PaperLens · 论文速读助手

把论文摘要或全文粘贴 / 上传后自动分块写入 **seekdb**，用自然语言提问即可检索相关段落，
并得到带 **原文引用编号** 的回答；问答历史自动保存在 seekdb 中。

- 后端：Python 3.11+ / FastAPI + uvicorn
- 数据层：seekdb（MySQL 协议），唯一存储与检索层，中文检索走 **IK 全文索引**
- 模型：MiniMax `MiniMax-M3.1-Flash`（OpenAI 兼容接口）
- 前端：单页原生 HTML/JS，无框架、无 CDN，离线可用

## 目录结构

```
paperlens/
├── app/
│   ├── __init__.py
│   ├── main.py          # FastAPI 路由、异常翻译、静态页挂载
│   ├── db.py            # seekdb 连接、建库建表（含 IK 索引）、数据访问
│   └── rag.py           # 分块、IK 检索编排、MiniMax 调用、降级策略
├── static/
│   └── index.html       # 三栏单页（导入 / 提问 / 历史）
├── scripts/
│   └── seed_demo.py     # 写入两篇示例论文摘要
├── requirements.txt
├── .env.example
└── README.md
```

## 快速开始

### 1. 启动 seekdb

```powershell
docker start seekdb
```

首次部署（容器尚不存在时）：

```powershell
docker run -d --name seekdb -p 2881:2881 oceanbase/seekdb:latest
```

确认端口映射正常：

```powershell
docker ps --filter name=seekdb --format "{{.Names}} {{.Status}} {{.Ports}}"
# seekdb Up ... 0.0.0.0:2881->2881/tcp
```

### 2. 安装依赖

```powershell
cd paperlens
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

> 直接用系统 Python 也可以：`python -m pip install -r requirements.txt`。

### 3. 配置环境变量

```powershell
Copy-Item .env.example .env
```

编辑 `.env`，至少填入 MiniMax API Key：

```dotenv
MINIMAX_API_KEY=你的Key
```

也可以只在当前终端临时生效：

```powershell
$env:MINIMAX_API_KEY="你的Key"
```

> API Key 只从环境变量 / `.env` 读取，不写进代码；`.env` 已在 `.gitignore` 中忽略。

### 4. 启动服务

```powershell
python -m uvicorn app.main:app --port 8000
```

打开 <http://127.0.0.1:8000>。

### 5. 写入示例数据（可选）

```powershell
python scripts/seed_demo.py
```

写入两篇示例论文（Transformer 速读版、RAG 论文问答），重复执行按标题覆盖。

## 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `SEEKDB_HOST` | `127.0.0.1` | seekdb 地址 |
| `SEEKDB_PORT` | `2881` | seekdb 端口 |
| `SEEKDB_USER` | `root` | 用户名 |
| `SEEKDB_PASSWORD` | 空 | 密码 |
| `SEEKDB_DB` | `paperlens` | 数据库名（不存在会自动创建） |
| `MINIMAX_API_KEY` | 空 | **必填**，Missing 时走降级摘录 |
| `MINIMAX_BASE_URL` | `https://api.minimaxi.com/v1` | OpenAI 兼容地址 |
| `MINIMAX_MODEL` | `MiniMax-M3.1-Flash` | 模型名 |
| `MINIMAX_MAX_TOKENS` | `2048` | 至少 1024，推理模型会消耗 reasoning tokens |
| `MINIMAX_TIMEOUT` | `90` | 单次调用超时（秒） |
| `PAPERLENS_CHUNK_CHARS` | `600` | 单块最大字数 |
| `PAPERLENS_TOP_K` | `5` | 检索返回段落数 |
| `PAPERLENS_OFFLINE_ANSWER` | `1` | 无 Key / 调用失败时降级为检索摘录 |
| `PAPERLENS_MAX_CONTENT_CHARS` | `2000000` | 单次导入正文字符上限 |

## API

### `POST /api/papers` — 文本导入

```powershell
curl.exe -X POST http://127.0.0.1:8000/api/papers `
  -H "Content-Type: application/json" `
  -d '{"title":"我的论文","content":"第一段……\n\n第二段……"}'
```

```json
{
  "paper_title": "我的论文",
  "chunks": 2,
  "total_chunks": 12,
  "avg_chars": 86
}
```

### `POST /api/papers/upload` — 文件导入

支持 `.txt` / `.md`，按 UTF-8 → GB18030 依次解码，标题留空则用文件名。

```powershell
curl.exe -X POST http://127.0.0.1:8000/api/papers/upload `
  -F "file=@paper.md" -F "title=我的论文"
```

### `POST /api/ask` — 检索 + 生成

```powershell
curl.exe -X POST http://127.0.0.1:8000/api/ask `
  -H "Content-Type: application/json" `
  -d '{"question":"论文用了什么方法？"}'
```

```json
{
  "answer": "论文采用块稀疏注意力……[1][2]",
  "citations": [
    {
      "index": 1,
      "chunk_id": "f0186a10…",
      "paper_title": "测试论文：稀疏注意力加速",
      "chunk_index": 1,
      "snippet": "方法上提出一种块稀疏注意力……",
      "content": "完整段落原文",
      "score": 0.8259
    }
  ],
  "degraded": false,
  "note": null,
  "hits": 3,
  "id": "qa_history 主键"
}
```

- 没有检索结果时：`answer` 固定为「未在该论文库中找到相关内容……」，`citations` 为空，不调用模型。
- `degraded: true` 表示未走模型（缺 Key / 超时 / 接口报错），此时 `answer` 是检索原文摘录，`note` 给出具体原因。

### `GET /api/history` — 最近问答

```powershell
curl.exe "http://127.0.0.1:8000/api/history?limit=20"
```

### `GET /api/health` — 健康检查

```json
{
  "ok": true,
  "version": "5.7.25-OceanBase seekdb-v1.4.0.0",
  "chunks": 10,
  "papers": 2,
  "database": "paperlens",
  "host": "127.0.0.1:2881"
}
```

seekdb 不可用时返回 HTTP 503 与中文排查提示。

## 实现说明

### 分块

- 以**空行**为段落边界；整篇没有空行时（网页 / PDF 复制常见）把连续行合并成段落。
- 段落不跨段合并，保证引用能精确回到原文段落。
- 单段超过 600 字按 600 字硬切。
- 重复导入同一标题会先删除旧块再写入，避免检索出过期内容。

### 检索

- 建表即建 IK 全文索引：
  `FULLTEXT INDEX idx_fts_content (content) WITH PARSER ik`。
- 检索全部下推到 seekdb：
  ```sql
  SELECT id, paper_title, chunk_index, content,
         MATCH(content) AGAINST(? IN NATURAL LANGUAGE MODE) AS score
  FROM chunks
  WHERE MATCH(content) AGAINST(? IN NATURAL LANGUAGE MODE)
  ORDER BY score DESC
  LIMIT 5
  ```
  应用内存中不做任何检索或向量计算。
- 原问句召回不足 5 条时，用去掉疑问词后的关键词再检索一轮补足（同样由 seekdb 执行）。
- 额外做一层 **2-gram 相关性过滤**：剔除只命中「体现 / 怎么样」这类泛词的假召回，
  例如「量子纠缠在生物钟里如何体现？」会如实返回「未找到相关内容」，而不是硬凑 5 条。

### 生成

- `POST {MINIMAX_BASE_URL}/chat/completions`，模型 `MiniMax-M3.1-Flash`，`max_tokens=2048`。
- 系统提示要求：只依据给定段落回答、每个结论标注 `[n]`、无法回答时明确说明，不得编造。
- 返回空 `content` 时回退读取 `reasoning_content`（推理模型场景）。

### 错误处理

| 场景 | 行为 |
| --- | --- |
| seekdb 未启动 / 连接失败 | `503`，提示 `docker start seekdb` 与端口排查 |
| seekdb 启动晚于服务 | 首次请求自动补建库表 |
| 缺少 `MINIMAX_API_KEY` | 降级为检索摘录（`degraded=true`），前端顶部黄条提示 |
| MiniMax 超时 / 非 200 / 空响应 | 同上降级，并在 `note` 中给出具体原因 |
| `PAPERLENS_OFFLINE_ANSWER=0` | 关闭降级，改为 `502` 明确报错 |
| 标题 / 正文 / 问题为空 | `422`，中文校验信息 |

## 自测清单

| 验收项 | 状态 |
| --- | --- |
| `docker start seekdb` + `uvicorn app.main:app --port 8000` 启动并打开首页 | 通过 |
| 导入两段文本 → 返回块数 → 顶部徽章段落数增加 | 通过 |
| 提问「论文用了什么方法？」→ 带 `[1][2]` 引用的回答 + 可直接展开的引用卡片 | 通过（未配置 Key 时走检索摘录，配置 Key 后走 MiniMax） |
| 刷新页面后历史记录仍在（`qa_history`） | 通过 |
| `GET /api/health` 返回 seekdb 版本与段落数 | 通过 |

本地实测环境：seekdb `5.7.25-OceanBase seekdb-v1.4.0.0`，Python 3.14（≥3.11）。

## 常见问题

**连不上 seekdb**
`docker ps` 确认容器状态与 `2881` 端口映射；`/api/health` 的报错会带上具体地址。

**提示 `PARSER ik` 相关错误**
当前 seekdb 未内置 IK 分词器。本仓库自测实例已内置；若换环境报错，可先去掉
`WITH PARSER ik` 建普通全文索引，中文召回会退化为按空格切分。

**回答一直显示「检索摘录」**
检查 `MINIMAX_API_KEY` 是否在当前终端 / `.env` 中生效，改完需重启 uvicorn；
接口返回的 `note` 会说明是缺 Key、超时还是 MiniMax 报错。

**中文显示乱码（上传文件）**
上传接口按 UTF-8 → GB18030 解码；前端「选择文件」按 UTF-8 读取，GBK 文件建议改用
`/api/papers/upload` 导入。

**想清空数据**

```sql
USE paperlens;
DELETE FROM chunks;
DELETE FROM qa_history;
```
