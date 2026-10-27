"""写入两篇示例论文摘要，便于快速演示 PaperLens。

用法（在 paperlens 目录下）：
    python scripts/seed_demo.py

脚本直接调用应用层（不经过 HTTP），因此服务没启动也能导入；
内容按标题覆盖，重复执行不会产生重复段落。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 允许从 scripts/ 目录直接运行
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app import db, rag  # noqa: E402

PAPERS: list[tuple[str, str]] = [
    (
        "Attention Is All You Need（中文速读版）",
        "\n\n".join(
            [
                "摘要：本文提出 Transformer，一种完全基于注意力机制的序列转换模型，"
                "摒弃了循环神经网络与卷积神经网络。在机器翻译任务上，该模型训练并行度更高，"
                "训练时间显著缩短，同时取得更好的翻译质量。",
                "方法：模型由编码器与解码器堆叠而成，每层包含多头自注意力子层与前馈网络子层，"
                "并配合残差连接与层归一化。由于没有循环结构，序列中任意两个位置之间的依赖路径长度"
                "为常数，因此更擅长建模长距离依赖。位置信息通过正弦位置编码注入。",
                "训练设置：在 WMT 2014 英德数据集上训练，使用 Adam 优化器，"
                "学习率采用 warmup 预热策略，批大小按 token 数统计，共训练约 10 万步；"
                "使用 dropout 与标签平滑缓解过拟合。",
                "实验结果：在 WMT 2014 英德翻译任务上，模型取得 28.4 BLEU，"
                "比此前最佳结果提升约 2 BLEU；在英法任务上取得 41.8 BLEU，"
                "单模型即可超越此前所有集成模型，训练成本明显下降。",
                "结论：注意力机制本身足以构建高性能序列转换模型，无需循环或卷积结构。"
                "作者同时指出，Transformer 在长序列上的计算复杂度较高，"
                "后续工作可以围绕稀疏注意力与更高效的推理展开。",
            ]
        ),
    ),
    (
        "检索增强生成在论文问答中的应用（示例）",
        "\n\n".join(
            [
                "摘要：针对论文阅读中定位困难的问题，本文构建了一个检索增强生成（RAG）系统，"
                "用户在导入论文后可用自然语言提问，系统返回带原文引用的回答。",
                "方法：系统先将论文按段落切分为不超过 600 字的文本块，写入支持中文分词的全文索引；"
                "提问时使用关键词检索召回前 5 个段落，再交给大语言模型生成回答，"
                "并要求模型仅依据召回段落作答、用方括号标注引用编号。",
                "实验设置：数据集包含 120 篇公开论文摘要与 600 条人工问题，"
                "评价指标包括引用准确率、答案忠实度与人工可读性评分。"
                "对比基线为纯关键词检索与不提供上下文的直接问答。",
                "实验结果：引入段落上下文后，答案忠实度从 0.62 提升到 0.89，"
                "引用准确率达到 0.91；纯关键词检索虽然响应更快，但无法给出成句解释。"
                "消融实验表明，中文分词全文索引相比二元切分把召回率提升了 11%。",
                "结论：把检索下推到数据库全文索引、让模型只做归纳，可以在控制成本的同时显著降低幻觉；"
                "未来工作包括引入向量召回、段落重排序以及跨论文对比问答。",
            ]
        ),
    ),
]


def main() -> int:
    try:
        db.ensure_schema()
    except db.SeekDBError as exc:
        print(f"[失败] {exc}")
        return 1

    for title, content in PAPERS:
        chunks = rag.split_into_chunks(content)
        written = db.replace_paper_chunks(title, chunks)
        print(f"[完成] 《{title}》写入 {written} 个段落，平均 {sum(map(len, chunks)) // written} 字")

    print(f"[总计] 库内现有 {db.count_chunks()} 个段落，seekdb 版本：{db.seekdb_version()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
