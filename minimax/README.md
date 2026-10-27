# MiniMax 赛题：规有所依——面向软件持续演进的 SDD 上下文管理系统

为 AI 开发助手提供「规格 → 代码 → 测试」的工程上下文模块：从 seekdb 检索任务相关的规格、代码与测试，
用 PowerContext 保存并复用任务进展，再由 MiniMax 依据上下文完成一次真实的代码修改。

## 技术分工

| 组件 | 在本作品中的作用 |
|---|---|
| **seekdb**（1.4.0.0，MySQL 协议 2881） | 工程资料库：规格、代码、测试全文索引进 `minimax_sdd.sdd_documents`（IK 中文分词全文索引），按任务检索并组装上下文 |
| **PowerContext**（1.2.0，本地 Server） | 任务状态存储：`/v1/memory/remember` 保存进展，`/v1/context/prepare` 在新进程中召回；模块重启后继续使用 |
| **MiniMax**（MiniMax-M3.1-Flash） | 依据上下文生成代码修改（`records/minimax-development-record.md` 保存完整请求/响应/token 用量） |

## 目录结构

```
minimax/
├── business/                 # 业务工程（图书借阅系统，可运行）
│   ├── library/loans.py      # 借阅逻辑（本次修改的文件）
│   └── tests/                # test_loans_basic.py 回归；test_loans_filter.py 目标
├── specs/                    # 规格
│   ├── loan-status-filter.md # 本次需求规格（SPEC-LOAN-002）
│   └── regression-contract.md# 回归契约（SPEC-REG-001）
├── context_module/           # SDD 上下文模块
│   ├── store.py              # seekdb 索引与检索（IK 全文）
│   ├── state.py              # PowerContext 状态保存/复用
│   └── cli.py                # 命令行入口
├── scripts/
│   ├── run_minimax_change.py # 调用 MiniMax 完成代码修改
│   └── spec_update_demo.py   # 规格更新→重新索引→检索生效 演示
├── records/                  # 技术使用记录与证据
└── requirements.txt
```

## 运行说明

### 1. 前置服务

```bash
# seekdb（MySQL 协议 2881）
docker run -d --name seekdb -p 2881:2881 oceanbase/seekdb:latest

# PowerContext（本地 Server，SQLite 持久化，不需要模型）
uv tool install "powercontext[cli,server]==1.2.0"
powercontext server run      # 监听 127.0.0.1:8000
```

### 2. 安装依赖

```bash
cd minimax
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
```

### 3. 配置环境变量

```bash
export SDD_SEEKDB_HOST=127.0.0.1      # WSL 访问 Windows 上的 Docker 时用宿主机网关 IP
export SDD_SEEKDB_PORT=2881
export SDD_SEEKDB_USER=root
export SDD_SEEKDB_PASSWORD=
export POWERCONTEXT_URL=http://127.0.0.1:8000
export MINIMAX_API_KEY=sk-api-你的key
```

### 4. 初始化与使用

```bash
# 索引工程资料到 seekdb
.venv/bin/python -m context_module.cli index --root .

# 按任务检索上下文（输出规格/代码/测试来源）
.venv/bin/python -m context_module.cli context --task "为借阅查询增加按状态筛选" --top 5

# 保存任务状态（PowerContext）
.venv/bin/python -m context_module.cli state save --task-id T-001 --step context_built

# 新进程读取并复用状态
.venv/bin/python -m context_module.cli state load --task-id T-001
```

### 5. 完成一次真实代码修改（MiniMax）

```bash
export MINIMAX_API_KEY=sk-api-...
.venv/bin/python scripts/run_minimax_change.py
# 生成 records/minimax-development-record.md 与 records/minimax-output-loans.py
cp records/minimax-output-loans.py business/library/loans.py

cd business && ../.venv/bin/python -m pytest tests -q     # 10 passed
```

### 6. 规格更新验证

```bash
.venv/bin/python scripts/spec_update_demo.py
# 输出 records/spec-update-evidence.txt：更新前 0 命中 → 重新索引后 1 命中
```

## 验证样例

见 [`验证样例.md`](验证样例.md)；测试结果、代码差异、MiniMax 记录等证据都在 `records/`。

## 已知限制

- 检索使用 IK 全文索引（seekdb 原生），不依赖 embedding 模型；如需语义检索可扩展 VECTOR 列
- PowerContext 使用默认 SQLite 存储；如需与 seekdb 共库可配置 `seekdb` 后端（Linux/macOS）
- MiniMax 为推理模型，生成长代码时 `max_tokens` 需给足（本仓库默认 4096）
