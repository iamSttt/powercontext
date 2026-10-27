# Dify 赛题：午夜美术馆谜案（AI 侦探推理游戏）

用 Dify 搭建的互动推理游戏：玩家扮演见习侦探，通过自由提问审问嫌疑人、核对物证与时间线，最终指认真凶。
AI 扮演侦探主持人与三名角色，根据玩家的问题逐步释放线索，并在玩家指认时判定推理是否成立。

## 技术分工

| 组件 | 版本 | 承担的作用 |
|---|---|---|
| **Dify** | 1.18.0（自部署） | 应用核心流程编排：Chatflow（开始 → LLM → 回答）、开场白、追问建议、多轮记忆（对话窗口 20 条） |
| **seekdb** | 1.4.0.0 | **作为 Dify 的数据库**（`DB_TYPE=seekdb`）：应用、会话、消息记录等全部元数据实际存储在 seekdb（MySQL 协议），可在 seekdb 中直接查询验证 |
| **MiniMax** | MiniMax-M3.1-Flash | 通过 OpenAI 兼容接口作为 Dify 的模型提供商，驱动游戏主持人的推理与叙事 |

题面允许「使用 seekdb 作为自行部署 Dify 时使用的数据库」作为技术结合方式；本作品采用该方式，并附 seekdb 中的实际数据查询记录作为证据。

## 部署步骤（从干净环境开始）

### 1. 启动 seekdb

```bash
docker run -d --name seekdb -p 2881:2881 oceanbase/seekdb:latest
# 等待就绪（约 10 秒），创建 Dify 数据库
docker exec seekdb mysql -h127.0.0.1 -P2881 -uroot -e "CREATE DATABASE IF NOT EXISTS dify CHARACTER SET utf8mb4;"
```

也可以直接使用 Dify 官方 compose 自带的 seekdb 服务（`--profile seekdb`）。

### 2. 准备 Dify

```bash
git clone --depth 1 https://github.com/langgenius/dify.git
cd dify/docker
cp .env.example .env
```

修改 `.env` 中的这些项（完整示例见本目录 `env.example`）：

```ini
SECRET_KEY=<随机 64 位十六进制>
INIT_PASSWORD=Dify2026!          # 首次进入的设置密码
DB_TYPE=seekdb
DB_HOST=host.docker.internal     # Linux 上改为宿主 IP 或 seekdb 容器名
DB_PORT=2881
DB_USERNAME=root
DB_PASSWORD=
DB_DATABASE=dify
VECTOR_STORE=oceanbase
OCEANBASE_VECTOR_HOST=host.docker.internal
OCEANBASE_VECTOR_PORT=2881
OCEANBASE_VECTOR_USER=root
OCEANBASE_VECTOR_PASSWORD=
OCEANBASE_VECTOR_DATABASE=dify
EXPOSE_NGINX_PORT=3000
COMPOSE_PROFILES=                # 留空：不启动内置 postgres/weaviate 等服务
```

### 3. 启动

```bash
docker compose up -d
```

首次启动会执行数据库迁移（在 seekdb 中建表）。等待 api 容器 healthy 后访问 **http://localhost:3000**。

### 4. 配置模型提供商

登录后：右上角头像 → **设置 → 模型供应商 → OpenAI-API-compatible → 添加模型**：

| 字段 | 值 |
|---|---|
| 模型名称 | `MiniMax-M3.1-Flash` |
| API Key | 你的 MiniMax API Key（`sk-api-...`） |
| API endpoint URL | `https://api.minimaxi.com/v1` |
| 模型类型 | LLM（对话） |
| 上下文长度 | 200000 |
| 最大 token 上限 | 8192 |

### 5. 导入应用

工作区 → **创建应用 → 导入 DSL 文件** → 选择 `app/detective-chatflow.yml`。
导入后打开「侦探主持人」LLM 节点，选择刚配置的 `MiniMax-M3.1-Flash` 模型，点击发布。

### 6. 开始游戏

打开应用对话页，按开场白提示提问即可。

## 验证样例

完整对话脚本与预期结果见 [`验证样例.md`](验证样例.md)。最短验证路径：

1. 发送「苏明那晚在做什么？」→ 应转述保安证词（值班室、22:32 前门警报、野猫），不直接说出真相
2. 发送「后门的门禁记录说明了什么？」→ 应提到 22:38 由馆长卡开启、22:41 关闭
3. 发送「我怀疑是林致远，因为他 22:00 说离开但车库 22:45 才出场，而且后门是他的卡刷的」→ 应判定推理正确并复盘作案过程

## seekdb 实际作用的验证

在 seekdb 中直接查询 Dify 写入的数据：

```bash
docker exec seekdb mysql -h127.0.0.1 -P2881 -uroot dify -e "SHOW TABLES;" | head -20
docker exec seekdb mysql -h127.0.0.1 -P2881 -uroot dify -e "SELECT COUNT(*) AS apps FROM apps; SELECT COUNT(*) AS messages FROM messages;"
docker exec seekdb mysql -h127.0.0.1 -P2881 -uroot dify -e "SELECT role, LEFT(answer,60) FROM messages ORDER BY created_at DESC LIMIT 3;"
```

## 已知限制

- MiniMax 的当前 Key 只有对话模型权限，没有 embedding 模型；本应用不使用知识库向量检索（游戏设定直接写在提示词中），seekdb 承担的是 Dify 数据库职责
- Dify 的 `host.docker.internal` 适用于 Docker Desktop（Windows/macOS）；Linux 需改为宿主 IP
- 首次启动 Dify 会执行数据库迁移，seekdb 上大约需要 1—2 分钟
