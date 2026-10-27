# 一页技术说明：端侧 AI 推理性能优化与 seekdb 混合检索

## 1. 模型与量化

| 项目 | 内容 |
|---|---|
| 模型 | bge-small-zh-v1.5（BAAI 开源，MIT 协议，HF 仓库 [Xenova/bge-small-zh-v1.5](https://huggingface.co/Xenova/bge-small-zh-v1.5) ONNX 转换版） |
| 结构 | BERT，4 层，512 维输出，中文词表 21128 |
| 量化方案 | **INT8（ONNX 动态量化 q8）**，由 Transformers.js 加载 `model_quantized.onnx`，运行时 dtype=q8 |
| 模型体积 | fp32 94.9 MB → q8 24.0 MB，**压缩比 3.95×** |
| 检索质量变化 | 自建 40 文档/8 主题中文评测集：Recall@5 均为 **67.5%（fp32）→ 67.5%（q8）**，量化前后检索质量无损失；逐对嵌入平均角度偏差 7.4° |
| 推理位置 | 文档与查询向量化均在用户浏览器本地执行，无任何云端推理 API 调用 |

## 2. 端侧运行

| 项目 | 内容 |
|---|---|
| 运行时 | Transformers.js（@huggingface/transformers 3.7，ONNX Runtime Web 内核） |
| 后端选择 | 优先 WebGPU：检测 `navigator.gpu` 并请求 WebGPU 设备加载模型；失败或不支持时**自动降级 WASM**（q8 → fp32 兜底链），界面徽章明确显示当前后端 |
| WebGPU 实测 | AMD Radeon 780M（Ryzen 7 8845H 核显）Chrome 下 WebGPU 路径验证通过 |
| WASM 降级验证 | Node CPU 环境加载同一 q8 模型成功，推理正常（WASM 与 CPU 执行图一致） |
| 模型加载 | 模型文件由本地后端静态托管，浏览器从本地源加载，不依赖外网 |

## 3. seekdb 使用

| 项目 | 内容 |
|---|---|
| 部署 | Docker 官方镜像 seekdb 1.4.0.0（MySQL 协议，端口 2881），数据目录挂载本地 |
| 接入 | Node 后端通过 seekdb-js（npm seekdb 1.4.0）接入；浏览器经 HTTP 转发向量与查询 |
| 集合结构 | 每知识库一个 Collection：文档列 + embedding VECTOR(512) + metadata（JSON）；**HNSW 向量索引**（VSAG，cosine）+ **IK 中文全文索引** |
| 混合检索 | `collection.hybridSearch()`：全文（query.whereDocument）+ 向量 KNN + **RRF 融合，seekdb 内单查询完成**，客户端不拼合两路结果 |
| 增量写入与刷新 | 每次导入后调用 `refresh_index()` 使异步 HNSW 索引立即可见；实测写入 117ms + 刷新 505ms（4 条） |
| 状态管理 | 知识库/集合可创建、切换、删除，状态持久化于 seekdb，浏览器重启后仍在 |
| 检索延迟 | 混合检索 48~62ms（40 文档级规模） |

## 4. 状态与优化

| 优化点 | 做法与实测 |
|---|---|
| embedding 批处理 | 批量 16 条/次，批间复用已加载模型，避免逐条推理开销 |
| 模型加载 | 单次加载驻留内存；实测加载 ~100-200ms，预热后查询向量化 ~5-10ms/批 |
| 查询延迟构成 | 查询向量化（本地）+ seekdb 检索：端到端 <100ms（含 HTTP 往返） |
| 索引刷新策略 | 写入后立即 refresh_index 保证可见性；刷新耗时单次 ~0.5s，批量写入均摊更低 |
| 降级路径 | WebGPU 失败 → WASM 自动切换并界面提示，核心流程不受影响 |

## 5. 性能数据汇总（实测，AMD Ryzen 7 8845H / Radeon 780M）

| 配置 | 模型体积 | 向量化吞吐 | seekdb 检索延迟 | 端到端查询延迟 | 检索质量 |
|---|---|---|---|---|---|
| 量化前基线（fp32） | 94.9 MB | ~5-10ms/批(16) | 48~62ms | <100ms | Recall@5 67.5% |
| INT8(q8) + WebGPU | **24.0 MB（3.95×）** | 同量级（浏览器实测） | 48~62ms | <100ms | **Recall@5 67.5%（无损失）** |
| INT8(q8) + WASM | 24.0 MB | 同量级（CPU/WASM 实测） | 48~62ms | <100ms | Recall@5 67.5% |

## 6. 评测方法

- 语料：8 主题 × 5 文档共 40 条中文短文档；查询：每主题 1 条改写查询（与原文无同词）；真值：同主题文档标记为相关
- 指标：Recall@5（精确余弦排序，L2 归一化向量）；每组重复测量取中位数
- 量化对比：同一模型 fp32/q8 两种精度独立嵌入，按同一真值计算 Recall@5 与逐对角度偏差
