"""seekdb 存储与检索：把规格、代码、测试索引进 seekdb，并按任务查询。"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pymysql

DB_HOST = os.environ.get("SDD_SEEKDB_HOST", "127.0.0.1")
DB_PORT = int(os.environ.get("SDD_SEEKDB_PORT", "2881"))
DB_USER = os.environ.get("SDD_SEEKDB_USER", "root")
DB_PASSWORD = os.environ.get("SDD_SEEKDB_PASSWORD", "")
DB_NAME = os.environ.get("SDD_SEEKDB_DATABASE", "minimax_sdd")


@dataclass
class Doc:
    doc_id: str
    kind: str  # spec | code | test
    path: str
    title: str
    content: str


def _connect(database: str | None = DB_NAME):
    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=database,
        charset="utf8mb4",
        autocommit=True,
    )


def ensure_schema() -> None:
    """创建 minimax_sdd 库与文档表（含 IK 中文全文索引）。"""
    conn = _connect(None)
    try:
        with conn.cursor() as cur:
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` CHARACTER SET utf8mb4")
            cur.execute(f"USE `{DB_NAME}`")
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS sdd_documents (
                    doc_id     VARCHAR(64)  PRIMARY KEY,
                    kind       VARCHAR(16)  NOT NULL,
                    path       VARCHAR(255) NOT NULL,
                    title      VARCHAR(255) NOT NULL,
                    content    LONGTEXT     NOT NULL,
                    updated_at DATETIME     NOT NULL,
                    FULLTEXT INDEX idx_fts_content (content) WITH PARSER ik
                )
                """
            )
    finally:
        conn.close()


def _kind_for(rel_path: str) -> str:
    if rel_path.startswith("specs/"):
        return "spec"
    if "/tests/" in rel_path or rel_path.startswith("tests/"):
        return "test"
    return "code"


def _title_for(text: str, rel_path: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("# ").strip()[:120]
    return Path(rel_path).name


def collect_documents(root: Path) -> list[Doc]:
    """收集工程资料：specs/、library 代码、tests 测试。"""
    targets: list[Path] = []
    for pattern in ("specs/*.md", "business/library/*.py", "business/tests/*.py"):
        targets.extend(sorted(root.glob(pattern)))
    docs: list[Doc] = []
    for path in targets:
        if not path.is_file() or path.name == "__init__.py":
            continue
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        doc_id = hashlib.sha1(rel.encode("utf-8")).hexdigest()[:16]
        docs.append(Doc(doc_id, _kind_for(rel), rel, _title_for(text, rel), text))
    return docs


def index_documents(docs: list[Doc]) -> int:
    ensure_schema()
    conn = _connect()
    now = datetime.now()
    try:
        with conn.cursor() as cur:
            for doc in docs:
                cur.execute(
                    """
                    REPLACE INTO sdd_documents
                        (doc_id, kind, path, title, content, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (doc.doc_id, doc.kind, doc.path, doc.title, doc.content, now),
                )
    finally:
        conn.close()
    return len(docs)


def _keywords(query: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}|[一-鿿]{2,6}", query)
    return list(dict.fromkeys(tokens))[:6]


def search(query: str, top_k: int = 6) -> list[dict]:
    """seekdb 全文检索（IK 分词）；无命中时回退到关键词 LIKE。"""
    ensure_schema()
    conn = _connect()
    rows: list[dict] = []
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            try:
                cur.execute(
                    """
                    SELECT kind, path, title, LEFT(content, 1600) AS snippet
                    FROM sdd_documents
                    WHERE MATCH(content) AGAINST (%s)
                    LIMIT %s
                    """,
                    (query, top_k),
                )
                rows = list(cur.fetchall())
            except Exception:
                rows = []
            if not rows:
                words = _keywords(query)
                if words:
                    where = " OR ".join("content LIKE %s" for _ in words)
                    params = [f"%{w}%" for w in words] + [top_k]
                    cur.execute(
                        f"""
                        SELECT kind, path, title, LEFT(content, 1600) AS snippet
                        FROM sdd_documents
                        WHERE {where}
                        LIMIT %s
                        """,
                        params,
                    )
                    rows = list(cur.fetchall())
    finally:
        conn.close()
    return rows


def build_context(task: str, top_k: int = 6) -> dict:
    """按任务检索资料，组装为供模型使用的上下文。"""
    rows = search(task, top_k)
    order = {"spec": 0, "code": 1, "test": 2}
    rows.sort(key=lambda r: order.get(r["kind"], 9))
    blocks = []
    for row in rows:
        blocks.append(f"### [{row['kind']}] {row['path']} — {row['title']}\n{row['snippet']}")
    return {
        "task": task,
        "sources": [{"kind": r["kind"], "path": r["path"], "title": r["title"]} for r in rows],
        "context": "\n\n".join(blocks),
    }
