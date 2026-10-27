"""seekdb 连接、建库建表与数据访问层。

seekdb 兼容 MySQL 协议，因此这里只用 pymysql 作为驱动，
所有检索都下推到 seekdb 的 IK 全文索引，应用层不做任何内存检索。
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

import pymysql
from dotenv import load_dotenv
from pymysql.cursors import DictCursor

# 读取项目根目录的 .env（存在时），方便本地开发
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# ---------------------------------------------------------------------------
# 连接配置：全部支持环境变量覆盖，默认值对齐本地 docker 版 seekdb
# ---------------------------------------------------------------------------
SEEKDB_HOST = os.getenv("SEEKDB_HOST", "127.0.0.1")
SEEKDB_PORT = int(os.getenv("SEEKDB_PORT", "2881"))
SEEKDB_USER = os.getenv("SEEKDB_USER", "root")
SEEKDB_PASSWORD = os.getenv("SEEKDB_PASSWORD", "")
SEEKDB_DB = os.getenv("SEEKDB_DB", "paperlens")

# 连接超时放短一些，seekdb 没起来时前端能快速看到明确提示
CONNECT_TIMEOUT = int(os.getenv("SEEKDB_CONNECT_TIMEOUT", "5"))

# 建表语句：chunks 直接带 IK 全文索引，保证 MATCH...AGAINST 走倒排
CREATE_CHUNKS_SQL = f"""
CREATE TABLE IF NOT EXISTS chunks (
    id VARCHAR(64) PRIMARY KEY,
    paper_title VARCHAR(255) NOT NULL,
    chunk_index INT NOT NULL,
    content LONGTEXT NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FULLTEXT INDEX idx_fts_content (content) WITH PARSER ik,
    KEY idx_paper_title (paper_title)
) DEFAULT CHARSET = utf8mb4
"""

CREATE_QA_SQL = """
CREATE TABLE IF NOT EXISTS qa_history (
    id VARCHAR(64) PRIMARY KEY,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    citations JSON,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_created_at (created_at)
) DEFAULT CHARSET = utf8mb4
"""


class SeekDBError(RuntimeError):
    """seekdb 连接或查询失败，路由层会把它翻译成 503。"""


# 建表只需成功一次，用标记 + 锁避免每个请求都做 DDL
_schema_lock = threading.Lock()
_schema_ready = False


def _connect(database: str | None = SEEKDB_DB) -> pymysql.connections.Connection:
    """建立一条连接；失败时抛出带排查提示的中文异常。"""
    try:
        return pymysql.connect(
            host=SEEKDB_HOST,
            port=SEEKDB_PORT,
            user=SEEKDB_USER,
            password=SEEKDB_PASSWORD,
            database=database,
            charset="utf8mb4",
            cursorclass=DictCursor,
            autocommit=True,
            connect_timeout=CONNECT_TIMEOUT,
            read_timeout=60,
            write_timeout=30,
        )
    except pymysql.MySQLError as exc:
        raise SeekDBError(
            f"无法连接 seekdb（{SEEKDB_HOST}:{SEEKDB_PORT}）：{exc}。"
            "请确认容器已启动：docker start seekdb，并检查端口映射与环境变量。"
        ) from exc


def _init_schema(conn: pymysql.connections.Connection) -> None:
    """建库 + 建表 + 兜底补 IK 全文索引（幂等）。"""
    with conn.cursor() as cur:
        cur.execute(
            f"CREATE DATABASE IF NOT EXISTS `{SEEKDB_DB}` "
            "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci"
        )
        conn.select_db(SEEKDB_DB)
        cur.execute(CREATE_CHUNKS_SQL)
        cur.execute(CREATE_QA_SQL)

        # 老库可能由别的版本创建而缺全文索引，这里检查后补齐
        cur.execute(
            "SELECT COUNT(*) AS n FROM information_schema.statistics "
            "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'chunks' AND INDEX_TYPE = 'FULLTEXT'",
            (SEEKDB_DB,),
        )
        row = cur.fetchone() or {}
        if int(row.get("n") or 0) == 0:
            cur.execute(
                "ALTER TABLE chunks ADD FULLTEXT INDEX idx_fts_content "
                "(content) WITH PARSER ik"
            )


def ensure_schema() -> None:
    """服务启动时调用；seekdb 没起来也不阻塞 FastAPI 启动，请求时会再重试。"""
    global _schema_ready
    with _schema_lock:
        if _schema_ready:
            return
        conn = _connect(database=None)
        try:
            _init_schema(conn)
            _schema_ready = True
        except pymysql.MySQLError as exc:
            raise SeekDBError(f"初始化 paperlens 库表失败：{exc}") from exc
        finally:
            conn.close()


@contextmanager
def get_conn() -> Iterator[pymysql.connections.Connection]:
    """获取连接；若此前初始化失败（例如 seekdb 后启动），这里自动补一次。"""
    global _schema_ready
    conn = _connect()
    try:
        if not _schema_ready:
            with _schema_lock:
                if not _schema_ready:
                    _init_schema(conn)
                    _schema_ready = True
        else:
            conn.select_db(SEEKDB_DB)
        yield conn
    except pymysql.MySQLError as exc:
        raise SeekDBError(f"seekdb 查询失败：{exc}") from exc
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 写入
# ---------------------------------------------------------------------------
def replace_paper_chunks(title: str, chunks: list[str]) -> int:
    """按标题覆盖写入一篇论文的所有块，返回写入块数。

    重复导入同一标题时先清掉旧块，避免检索出过期内容。
    """
    if not chunks:
        return 0
    now = datetime.now()
    rows = [
        (uuid.uuid4().hex, title, idx, content, now)
        for idx, content in enumerate(chunks)
    ]
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM chunks WHERE paper_title = %s", (title,))
        cur.executemany(
            "INSERT INTO chunks (id, paper_title, chunk_index, content, created_at) "
            "VALUES (%s, %s, %s, %s, %s)",
            rows,
        )
    return len(rows)


def insert_qa(question: str, answer: str, citations: list[dict[str, Any]]) -> str:
    """写入一条问答历史，返回记录 id。"""
    qa_id = uuid.uuid4().hex
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO qa_history (id, question, answer, citations, created_at) "
            "VALUES (%s, %s, %s, %s, %s)",
            (qa_id, question, answer, json.dumps(citations, ensure_ascii=False), datetime.now()),
        )
    return qa_id


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------
def _parse_citations(raw: Any) -> list[dict[str, Any]]:
    """citations 列可能是 JSON 字符串，也可能是驱动已解析的对象。"""
    if raw is None:
        return []
    if isinstance(raw, (list, dict)):
        return raw if isinstance(raw, list) else [raw]
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def search_chunks(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """用 seekdb IK 全文索引检索段落（自然语言模式）。

    MATCH...AGAINST 在 WHERE 与 SELECT 中各出现一次：前者做过滤，
    后者取相关度分数用于排序。
    """
    if not query.strip():
        return []
    sql = (
        "SELECT id, paper_title, chunk_index, content, "
        "MATCH(content) AGAINST(%s IN NATURAL LANGUAGE MODE) AS score "
        "FROM chunks "
        "WHERE MATCH(content) AGAINST(%s IN NATURAL LANGUAGE MODE) "
        "ORDER BY score DESC, created_at DESC "
        f"LIMIT {int(limit)}"
    )
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, (query, query))
        return list(cur.fetchall())


def count_chunks() -> int:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM chunks")
        return int((cur.fetchone() or {}).get("n") or 0)


def list_papers() -> list[dict[str, Any]]:
    """按论文聚合块数，前端徽章与导入结果复用。"""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT paper_title, COUNT(*) AS chunks FROM chunks "
            "GROUP BY paper_title ORDER BY MAX(created_at) DESC"
        )
        return list(cur.fetchall())


def list_history(limit: int = 20) -> list[dict[str, Any]]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT id, question, answer, citations, created_at "
            f"FROM qa_history ORDER BY created_at DESC LIMIT {int(limit)}"
        )
        rows = list(cur.fetchall())
    for row in rows:
        row["citations"] = _parse_citations(row.get("citations"))
        created = row.get("created_at")
        row["created_at"] = created.strftime("%Y-%m-%d %H:%M:%S") if created else ""
    return rows


def seekdb_version() -> str:
    """取 seekdb 版本串，例如 5.7.25-OceanBase seekdb-v1.4.0.0。"""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT VERSION() AS v")
        return str((cur.fetchone() or {}).get("v") or "unknown")
