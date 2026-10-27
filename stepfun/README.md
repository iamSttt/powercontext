# 阶跃星辰赛题：端侧 AI 推理性能优化与 seekdb 混合检索

浏览器本地向量化 + seekdb 向量/全文混合检索的迷你知识库应用。

## 架构

```
浏览器（用户设备）
 ├─ Transformers.js 本地 embedding（bge-small-zh-v1.5，INT8/q8 量化 ONNX）
 │    ├─ 优先 WebGPU，自动降级 WASM（界面明确提示当前后端）
 │    └─ 文档向量化与查询向量化均在此完成，不调用任何云端推理 API
 └─ HTTP → 本地 Node 后端（seekdb-js）
              └─ seekdb（Docker，MySQL 协议 2881）
                   ├─ HNSW 向量索引（VSAG，512 维，cosine）
                   ├─ IK 全文索引（中文分词）
                   └─ 混合检索：seekdb 内单查询完成（RRF 融合），不在客户端拼合
```

## 依赖版本

| 组件 | 版本 | 说明 |
|---|---|---|
| seekdb | 1.4.0.0（Docker 镜像 `oceanbase/seekdb:latest`） | 数据存储与混合检索 |
| seekdb-js | npm `seekdb` 1.4.0 | Node 后端接入 seekdb |
| Node.js | ≥20（开发机 24.16） | 后端 + 前端构建 |
| @huggingface/transformers | ^3.7 | 浏览器端 embedding 推理 |
| Vite | ^6 | 前端开发服务器 |
| 浏览器 | Chrome/Edge 最新版 | WebGPU 需要 Chrome 113+/Edge 113+ |
| 模型 | Xenova/bge-small-zh-v1.5（HF） | 512 维中文 embedding，q8 量化 |

## 启动步骤

1. 确保 seekdb 运行（Docker Desktop 已启动时）：

   ```powershell
   docker start seekdb
   ```

2. 启动后端（端口 3001，服务模型文件 + 对接 seekdb）：

   ```powershell
   cd backend
   npm install --legacy-peer-deps   # 首次
   node server.js
   ```

3. 启动前端（端口 5173，浏览器打开 http://localhost:5173）：

   ```powershell
   cd frontend
   npm install --registry=https://registry.npmmirror.com   # 首次
   npx vite
   ```

   或直接运行仓库根目录的 `start-all.bat`。

## 验证样例

### 样例 1：导入与混合检索

1. 打开页面，点击「新建」，创建知识库 `demo`
2. 在文本区粘贴（每段一条）：

   ```
   今天天气很好，适合去公园散步
   数据库向量检索技术发展迅速
   HNSW 索引支持近似最近邻查询
   全文检索使用 IK 分词器处理中文
   ```

3. 点击「导入并向量化」：应显示本地向量化进度，完成后提示写入与索引刷新耗时；页面徽章显示推理后端（WebGPU 或 WASM）
4. 输入「想出去走走」→ 混合检索：第 1 条应为「今天天气很好，适合去公园散步」
5. 输入「近似最近邻算法」→ 第 1 条应为「HNSW 索引支持近似最近邻查询」

### 样例 2：增量导入与索引刷新

1. 再导入一条：「北京今天有小雨，记得带伞」
2. 立即检索「天气」：刷新完成后（秒级）应能检索到新条目

### 样例 3：后端降级

1. 在 Chrome 开发者工具 Console 中观察加载日志
2. 用不支持 WebGPU 的环境（或 `chrome://flags` 关闭 WebGPU）打开：徽章显示 WASM，核心流程仍可用

## 已知限制

- seekdb-js npm 包 1.4.0 的 peer 依赖 `@seekdb/default-embed` 存在 `workspace:*` 打包问题，安装需加 `--legacy-peer-deps`（我们不使用该嵌入函数，浏览器端自行推理）
- 前端开发模式需 Node 后端与 Vite 同时运行（`start-all.bat` 已封装）
- WebGPU 在部分设备/驱动组合下可能不可用，应用会自动降级 WASM 并在界面提示

## 性能与量化数据（实测）

| 项目 | 数值 |
|---|---|
| 模型体积（fp32 → q8） | 94.9 MB → 24.0 MB，压缩比 3.95× |
| 量化质量（Recall@5，40 文档评测集） | fp32 67.5% → q8 67.5%，无损失 |
| 模型加载（WASM/CPU） | ~100-200ms（Node 实测） |
| 单次 embedding 推理 | ~5-10ms/批（Node 实测） |
| seekdb 混合检索延迟 | 48~62ms（4 条文档） |
| 写入 + 索引刷新 | ~120ms + ~500ms（4 条） |

量化评测脚本：`frontend/scripts/eval-quantization.mjs`；完整技术说明：[docs/技术说明.md](docs/技术说明.md)
