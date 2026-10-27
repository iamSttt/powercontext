"""演示：规格更新并重新导入后，上下文模块使用更新后的内容。

步骤：
1. 从规格中移除「补充验收标准 8」段落，重新索引，按该段落检索 → 0 命中
2. 重新写入该段落，重新索引，再次检索 → 命中并返回新内容
证据输出到 records/spec-update-evidence.txt
"""
from __future__ import annotations

import sys
from pathlib import Path

import pymysql

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from context_module import store  # noqa: E402

SPEC = ROOT / "specs" / "loan-status-filter.md"
MARK = "补充验收标准 8"
LINE = (
    "- 2026-10-27：补充验收标准 8——非法 status 的异常信息必须包含该值本身"
    f"（例如 {chr(96)}invalid status: lost{chr(96)}）。"
)


def spec_without_update() -> str:
    lines = [line for line in SPEC.read_text(encoding="utf-8").splitlines() if MARK not in line]
    return "\n".join(lines) + "\n"


def spec_with_update() -> str:
    base = spec_without_update().rstrip("\n")
    return base + "\n\n## 变更记录\n\n" + LINE + "\n"


def count_hits() -> tuple[int, str]:
    conn = pymysql.connect(
        host=store.DB_HOST,
        port=store.DB_PORT,
        user=store.DB_USER,
        password=store.DB_PASSWORD,
        database=store.DB_NAME,
        charset="utf8mb4",
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*), COALESCE(MAX(RIGHT(content, 120)), '') "
                "FROM sdd_documents WHERE content LIKE %s",
                (f"%{MARK}%",),
            )
            total, tail = cur.fetchone()
            return int(total), (tail or "").replace("\n", " ")
    finally:
        conn.close()


def main() -> int:
    out: list[str] = []

    # 1) 更新前
    SPEC.write_text(spec_without_update(), encoding="utf-8")
    store.index_documents(store.collect_documents(ROOT))
    before, _ = count_hits()
    out.append(f"[更新前] 规格中无「{MARK}」，检索命中：{before} 条")

    # 2) 更新并重新导入
    SPEC.write_text(spec_with_update(), encoding="utf-8")
    store.index_documents(store.collect_documents(ROOT))
    after, tail = count_hits()
    out.append(f"[更新后] 重新索引完成，检索命中：{after} 条")
    out.append(f"[更新后] seekdb 中的规格末尾内容：…{tail[-80:]}")

    # 3) 通过上下文模块检索
    result = store.build_context("非法 status 异常信息包含值本身", top_k=3)
    paths = [s["path"] for s in result["sources"]]
    out.append(f"[上下文模块] 检索来源：{paths}")
    out.append("[上下文模块] 规格片段包含新增段落：" + str(MARK in result["context"]))

    text = "\n".join(out)
    (ROOT / "records").mkdir(exist_ok=True)
    (ROOT / "records" / "spec-update-evidence.txt").write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
