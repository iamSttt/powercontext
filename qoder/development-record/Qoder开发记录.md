# Qoder 开发记录：PaperLens 论文速读助手

- Qoder 账号 UID：`01a17d7d-ff7c-7bd9-9c16-4e8f6f9223da`
- 账号：文博 余
- 会话标题：开发 PaperLens 论文速读助手
- 项目：OceanBase
- 记录来源：Qoder 本地会话历史（conversation-history）自动导出，未人工改写
- 消息条数：27

> 说明：以下为 Qoder Agent 会话的完整文本记录，覆盖需求理解、方案确认、编码与调试过程。

### 1. 用户

<user_query>
# Qoder 赛题方案：PaperLens 论文速读助手

> 把本文档整段复制给 Qoder（新建项目后作为任务描述发送），让它完成开发。

## 一、项目定位

面向学生与科研人员的「论文速读助手」：把论文摘要/全文粘贴或上传后自动分块入库，
用自然语言提问即可检索相关段落，并得到带**原文引用**的回答；历史问答自动保存。

真实痛点：读一篇论文要来回翻页找段落；组会汇报前需要快速定位"方法/实验/结论"。

## 二、技术要求（必须满足）

1. **Python 3.11+**，FastAPI + uvicorn 后端，单页原生 HTML/JS 前端（不用前端框架）。
2. **seekdb 作为唯一数据层**（MySQL 协议）：
   - 连接：`host=127.0.0.1, port=2881, user=root, password=''`（可用环境变量覆盖）
   - 数据库：`paperlens`
   - 表 `chunks`：`id VARCHAR(64) PRIMARY KEY, paper_title VARCHAR(255), chunk_index INT, content LONGTEXT, created_at DATETIME`，
     建 **IK 全文索引**：`FULLTEXT INDEX idx_fts_content (content) WITH PARSER ik`
   - 表 `qa_history`：`id VARCHAR(64) PRIMARY KEY, question TEXT, answer TEXT, citations JSON, created_at DATETIME`
   - 检索必须使用 seekdb 的 `MATCH(content) AGAINST(...)`（IK 分词），不得在应用内存里检索
3. **MiniMax 模型**：OpenAI 兼容接口
   - `POST https://api.minimaxi.com/v1/chat/completions`
   - 模型 `MiniMax-M3.1-Flash`，`max_tokens` 至少 1024（推理模型会消耗 reasoning tokens）
   - API Key 从环境变量 `MINIMAX_API_KEY` 读取，**不要写进代码或仓库**
4. 分块规则：按段落切分，单块不超过 600 字，超长段落按 600 字硬切。

## 三、功能清单

### 1. 导入论文
- 输入：标题 + 正文（文本框粘贴），支持上传 `.txt` / `.md` 文件
- 后端 `POST /api/papers`：分块 → 写入 `chunks` → 返回块数
- 前端显示导入进度与块数

### 2. 提问与引用回答
- 输入问题 → 后端 `POST /api/ask`
- 处理流程：
  1. 用问题在 seekdb 中做 IK 全文检索，取 top 5 段落（附带 `paper_title`、`chunk_index`）
  2. 把段落作为上下文交给 MiniMax，要求「只依据给定段落回答，并标注引用编号 [1][2]」
  3. 返回 `answer` + `citations`（每条的论文标题、块序号、原文片段）
  4. 问答写入 `qa_history`
- 前端：回答区展示正文与引用卡片；无检索结果时如实提示"未在该论文库中找到相关内容"

### 3. 历史记录
- `GET /api/history` 返回最近 20 条问答，前端展示

### 4. 健康检查
- `GET /api/health`：返回 seekdb 版本与 chunks 数量

## 四、界面要求

- 单页三栏布局（导入 / 提问 / 历史），中文界面
- 顶部显示「seekdb 已连接 · N 个段落」状态徽章
- 简洁美观，深色/浅色皆可，不要用外部 CDN（离线可用）

## 五、工程要求

- 目录结构：
  ```
  paperlens/
  ├── app/main.py          # FastAPI
  ├── app/db.py            # seekdb 连接与建表
  ├── app/rag.py           # 检索 + MiniMax 调用
  ├── static/index.html    # 前端单页
  ├── requirements.txt
  └── README.md            # 运行说明（含 seekdb 启动、环境变量、启动命令）
  ```
- `.env.example` 提供 `MINIMAX_API_KEY=` 占位
- 代码含中文注释；错误处理：seekdb 连不上、模型超时都要给出明确提示
- 附一个 `scripts/seed_demo.py` 写入两篇示例论文摘要，便于演示

## 六、验收标准（Qoder 完成后自测）

1. `docker start seekdb` 后运行 `uvicorn app.main:app --port 8000`，打开首页
2. 导入两段文本 → 显示块数 → 顶部徽章段落数增加
3. 问「论文用了什么方法？」→ 返回带 [1][2] 引用的回答，引用卡片可对应到原文段落
4. 刷新页面后历史记录仍在（seekdb `qa_history`）
5. `GET /api/health` 返回 seekdb 版本与段落数
</user_query>

### 2. Qoder

我先了解一下工作区里关于 seekdb 和 MiniMax 的既有资料，避免重复踩坑。

### 3. Qoder

IK 检索链路验证通过。再确认几个建表细节（JSON 类型、索引检测）后开始写代码。

### 4. Qoder

环境探明了：seekdb v1.4.0 已运行（127.0.0.1:2881），IK 中文全文检索、JSON 列、ALTER 补索引都可用。现在开始实现。

### 5. Qoder

后端骨架完成。现在写前端单页（离线、原生 JS）。

### 6. Qoder

后端与前端就位。现在安装依赖、清理探测表并跑通链路。

### 7. Qoder

分块逻辑把 5 个段落合并成 1 块了，不符合"按段落切分"。修正为段落优先、仅无空行时才合并连续行。

### 8. Qoder

检索发现一个真实问题：`量子纠缠…` 这类无命中问题仍召回 5 条低相关段落。需在应用层做低相关度过滤（检索仍在 seekdb 完成）。同时核对库内文本是否被插入多余空格。

### 9. Qoder

检索过滤修复已验证。现在补 README 与运行配置。

### 10. Qoder

前端需要实际渲染验证，我派浏览器子代理跑一遍导入→提问→刷新历史的完整交互。

### 11. Qoder

截图确认了两个真实缺陷：回答区溢出压住引用卡片、降级提示重复。修复。

### 12. Qoder

服务已重启。让浏览器子代理在宽视口下复验修复效果。

### 13. Qoder

布局修复通过。再补一个桌面端高度约束，让三栏各自内部滚动而非整页滚动。

### 14. Qoder

根因确认：`body` 只有 `min-height`，导致 grid `1fr` 无确定高度。修正为固定视口高度 + 面板弹性填充。

### 15. Qoder

布局问题全部解决。最后验证 MiniMax 调用链路与错误提示（用无效 Key 探测接口可达性）。

### 16. Qoder

MiniMax 链路可达（401 说明路径与鉴权格式正确），只差有效 Key。现在清理验证残留并做最终检查。

### 17. Qoder

PaperLens 已开发完成并在本机跑通，服务当前运行在 <http://127.0.0.1:8000>（seekdb 库内已有 10 个段落）。

## 交付内容

| 文件 | 职责 |
| --- | --- |
| [app/db.py](file:///c:/Users/25084/OneDrive/桌面/OceanBase/paperlens/app/db.py) | seekdb 连接（环境变量可覆盖）、自动建库建表、IK 全文索引、段落检索/历史读写 |
| [app/rag.py](file:///c:/Users/25084/OneDrive/桌面/OceanBase/paperlens/app/rag.py) | 按段落分块（≤600 字，超长硬切）、检索编排、MiniMax 调用与降级、引用构造 |
| [app/main.py](file:///c:/Users/25084/OneDrive/桌面/OceanBase/paperlens/app/main.py) | 5 个接口、静态页挂载、中文错误翻译（503/502/422） |
| [static/index.html](file:///c:/Users/25084/OneDrive/桌面/OceanBase/paperlens/static/index.html) | 深色三栏单页，原生 JS、零 CDN，含进度条、引用卡片、历史折叠 |
| [scripts/seed_demo.py](file:///c:/Users/25084/OneDrive/桌面/OceanBase/paperlens/scripts/seed_demo.py) | 写入两篇示例论文（幂等覆盖） |
| [README.md](file:///c:/Users/25084/OneDrive/桌面/OceanBase/paperlens/README.md) | seekdb 启动、环境变量、API 示例、排查与自测清单 |

## 实测结果

- `health`：`seekdb v1.4.0.0`、10 个段落、2 篇论文
- 导入：文本 5 块、`.md` 上传 3 块，徽章段落数同步增长
- 提问：`论文用了什么方法？` 返回 3~4 条引用，引用卡片可展开对应原文段落，历史落 `qa_history` 并刷新后仍在
- 无命中：`量子纠缠在生物钟里如何体现？` 如实返回「未在该论文库中找到相关内容」，不再硬凑 5 条
- 三栏布局在 1440×900 下并排等高、整页不滚动、面板内部滚动，回答区与引用卡片不重叠，控制台无报错

## 过程中修复的两个真实缺陷

1. **假召回**：IK 自然语言模式会把「体现/什么」这类泛词当命中，导致无关问题也返回 5 条。现由 seekdb 负责检索打分，应用层只做 2-gram 重叠校验过滤，不违反"不得内存检索"。
2. **布局溢出**：`body` 只有 `min-height` 导致 grid `1fr` 被内容撑开，长回答压住引用卡片。已改为固定视口高度 + 面板弹性滚动。

## 需要你做一件事

当前机器环境里没有 `MINIMAX_API_KEY`，所以问答走的是**降级摘录模式**（黄条提示原因，正文是 seekdb 原文摘录）。已用无效 Key 探测过 `https://api.minimaxi.com/v1/chat/completions`，接口可达、鉴权头格式正确（返回 401 而非 404）。填入 Key 后重启即走真实模型：

```powershell
# 编辑 paperlens\.env 中的 MINIMAX_API_KEY=
# 然后重启（当前终端里的 uvicorn 需先停止）
cd paperlens
python -m uvicorn app.main:app --port 8000
```

另外 `paperlens/` 下残留 3 个 `_shot-*.png`（浏览器验证截图，删除工具被 OneDrive 占用未能移除），可直接手动删掉，不影响运行。

### 18. 用户

<system-reminder>
The current system time is Tuesday, October 27, 2026. This supersedes any time mentioned earlier in this conversation. Please use this information as a reference but do not disclose it.
</system-reminder>



<user_query>
sk-***REDACTED***这是minimaxapikey
</user_query>

### 19. Qoder

收到，我会把 Key 只写入本地 `.env`（已在 `.gitignore` 中），不写进代码，也不会在对话中回显。然后重启服务做一次真实模型调用验证。

### 20. Qoder

真实模型调用成功（4.8s，`degraded=false`，回答带 [1][2]）。但模型输出含 Markdown 加粗，前端会显示字面 `**`。我补上轻量渲染与提示约束。

### 21. Qoder

历史记录仍用 `textContent` 渲染，展开后会把 `**` 原样显示。统一渲染方式后再做最终浏览器验证。

### 22. Qoder

模型回答是多行要点，当前都塞在一个段落里。改成真正的列表渲染，可读性更好。

### 23. Qoder

截图暴露两个视觉细节：列表在 `pre-wrap` 下多出空白、进度条容器始终占位。一并修掉。

### 24. Qoder

窄屏下按钮被面板裁切露出渐变条，原因是 `panel-body` 的 70vh 上限。窄屏应整页滚动，改回自然高度。

### 25. Qoder

Key 已写入 `paperlens/.env`（不会提交到仓库、也不会在对话里回显），服务已重启并接通真实模型。

## 真实模型验证结果

| 问题 | 耗时 | 降级 | 引用数 | 结果 |
| --- | --- | --- | --- | --- |
| 论文用了什么方法？ | 4.8s | 否 | 2 | 分别概括 RAG 与 Transformer 的方法，逐句标注 [1][2] |
| 实验设置和结果如何？ | 10.0s | 否 | 4 | 分点列出 BLEU、忠实度、引用准确率等设置与结果 |
| 主要结论是什么？ | 4.7s | 否 | 2 | 两篇论文结论分开陈述，引用可对应到第 5 段 |
| 论文用了什么方法？（前端实测） | 1–2s | 否 | 2 | 页面渲染出 `**加粗**` 与列表，无字面星号 |

历史记录已累计 6 条真实问答，刷新后仍在；`health` 返回 seekdb v1.4.0.0、10 个段落、2 篇论文。

## 接通模型后顺带修的三处

1. **Markdown 不再裸露**：模型输出 `**加粗**` 和 `- ` 要点，前端新增轻量渲染（转义后转 `<strong>` / `<ul><li>`），并在 prompt 中约束为纯文本输出；历史记录复用同一渲染函数。
2. **列表空白**：`pre-wrap` 会把 HTML 源码换行渲染成空行，已对 `.answer-list` 单独设 `white-space: normal`。
3. **窄屏按钮被裁切**：窄视口下 `.panel-body` 的 70vh 上限会把「导入并分块」按钮从中间截断，露出一条渐变；现窄屏改为整页滚动，桌面端仍是三栏各自内部滚动。

## 当前状态

- 服务运行中：<http://127.0.0.1:8000>（`python -m uvicorn app.main:app --port 8000`，PID 34716）
- 库内：2 篇示例论文、10 个段落；6 条真实问答历史
- `.gitignore` 已包含 `.env`

## 两点提醒

- `.env` 现在含真实 Key。**打包或提交项目时不要带上 `.env`**；换机器用 `.env.example` + 环境变量即可。
- 目录里残留 9 张 `_*.png` 验证截图（`_shot-*`、`_tmp-probe*`、`_final-*`），删除工具被 OneDrive 文件句柄占用未能移除，可手动删掉；其中 `_final-01~04` 是当前版本的效果图，留着当演示截图也行。

### 26. 用户

<system-reminder>
The current system time is Tuesday, October 27, 2026. This supersedes any time mentioned earlier in this conversation. Please use this information as a reference but do not disclose it.
</system-reminder>



<user_query>
Qoder 项目代码在哪？
</user_query>

### 27. Qoder

代码在本次工作区下的 `paperlens` 目录里：

```
c:\Users\25084\OneDrive\桌面\OceanBase\paperlens
```

| 路径 | 说明 |
| --- | --- |
| [paperlens/app/main.py](file:///c:/Users/25084/OneDrive/桌面/OceanBase/paperlens/app/main.py) | FastAPI 入口，5 个接口 + 静态页挂载 |
| [paperlens/app/db.py](file:///c:/Users/25084/OneDrive/桌面/OceanBase/paperlens/app/db.py) | seekdb 连接、建表（IK 全文索引）、检索与历史读写 |
| [paperlens/app/rag.py](file:///c:/Users/25084/OneDrive/桌面/OceanBase/paperlens/app/rag.py) | 分块、检索编排、MiniMax 调用与降级 |
| [paperlens/static/index.html](file:///c:/Users/25084/OneDrive/桌面/OceanBase/paperlens/static/index.html) | 三栏单页前端 |
| [paperlens/scripts/seed_demo.py](file:///c:/Users/25084/OneDrive/桌面/OceanBase/paperlens/scripts/seed_demo.py) | 两篇示例论文导入脚本 |
| [paperlens/README.md](file:///c:/Users/25084/OneDrive/桌面/OceanBase/paperlens/README.md) | 运行说明 |
| `paperlens/.env` | MiniMax Key 与 seekdb 配置（已 gitignore，勿外传） |

启动方式（服务当前已在跑，端口 8000）：

```powershell
cd "c:\Users\25084\OneDrive\桌面\OceanBase\paperlens"
python -m uvicorn app.main:app --port 8000
```

如果你问的是「Qoder 生态赛题」相关的资料而不是本项目代码，那在知识库目录下：[01-语雀知识库/05-生态赛题/25-Qoder.md](file:///c:/Users/25084/OneDrive/桌面/OceanBase/01-语雀知识库/05-生态赛题/25-Qoder.md) 和 [47-企业第六讲： Qoder.md](file:///c:/Users/25084/OneDrive/桌面/OceanBase/01-语雀知识库/06-课程与培训/47-企业第六讲：%20Qoder.md)。
