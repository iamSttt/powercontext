"""FastAPI 入口：论文导入、检索问答、历史记录与健康检查。

启动：uvicorn app.main:app --port 8000
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from app import db, rag

logger = logging.getLogger("paperlens")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

# 单次导入的正文上限，避免误粘贴超大文件把内存打满
MAX_CONTENT_CHARS = int(os.getenv("PAPERLENS_MAX_CONTENT_CHARS", "2000000"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时尝试建库建表；seekdb 没起来也照常启动，接口里再给出提示。"""
    try:
        db.ensure_schema()
        logger.info("seekdb 已连接，paperlens 库表就绪")
    except db.SeekDBError as exc:
        logger.warning("seekdb 初始化失败，服务仍会启动：%s", exc)
    yield


app = FastAPI(title="PaperLens 论文速读助手", version="1.0.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------
class PaperIn(BaseModel):
    title: str = Field(..., max_length=255, description="论文标题")
    content: str = Field(..., description="论文正文或摘要")

    @field_validator("title")
    @classmethod
    def _strip_title(cls, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise ValueError("论文标题不能为空")
        return value

    @field_validator("content")
    @classmethod
    def _check_content(cls, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise ValueError("论文正文不能为空")
        if len(value) > MAX_CONTENT_CHARS:
            raise ValueError(f"正文过长（超过 {MAX_CONTENT_CHARS} 字符），请分段导入")
        return value


class AskIn(BaseModel):
    question: str = Field(..., max_length=2000, description="自然语言问题")

    @field_validator("question")
    @classmethod
    def _strip_question(cls, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise ValueError("问题不能为空")
        return value


# ---------------------------------------------------------------------------
# 异常翻译：把底层错误变成前端可直接展示的中文提示
# ---------------------------------------------------------------------------
@app.exception_handler(db.SeekDBError)
async def _seekdb_error_handler(_request, exc: db.SeekDBError):
    return JSONResponse(status_code=503, content={"detail": str(exc), "kind": "seekdb"})


@app.exception_handler(rag.MiniMaxError)
async def _minimax_error_handler(_request, exc: rag.MiniMaxError):
    return JSONResponse(status_code=502, content={"detail": str(exc), "kind": "minimax"})


# ---------------------------------------------------------------------------
# 页面
# ---------------------------------------------------------------------------
@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html; charset=utf-8")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
@app.get("/api/health")
async def health():
    """返回 seekdb 版本、地址与段落数，前端用它渲染顶部徽章。"""
    try:
        version = db.seekdb_version()
        chunks = db.count_chunks()
        papers = db.list_papers()
    except db.SeekDBError as exc:
        return JSONResponse(
            status_code=503,
            content={"ok": False, "detail": str(exc), "kind": "seekdb"},
        )
    return {
        "ok": True,
        "version": version,
        "chunks": chunks,
        "papers": len(papers),
        "database": db.SEEKDB_DB,
        "host": f"{db.SEEKDB_HOST}:{db.SEEKDB_PORT}",
    }


def _import_paper(title: str, content: str) -> dict:
    """共用导入逻辑：分块 → 覆盖写入 seekdb → 返回块数。"""
    chunks = rag.split_into_chunks(content)
    if not chunks:
        raise HTTPException(status_code=400, detail="正文为空或无法分块，请检查内容后重试")
    try:
        written = db.replace_paper_chunks(title, chunks)
        total = db.count_chunks()
    except db.SeekDBError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    logger.info("导入《%s》：%d 块，库内共 %d 块", title, written, total)
    return {
        "paper_title": title,
        "chunks": written,
        "total_chunks": total,
        "avg_chars": sum(len(c) for c in chunks) // max(written, 1),
    }


@app.post("/api/papers")
async def create_paper(payload: PaperIn):
    """文本框粘贴导入。"""
    return _import_paper(payload.title, payload.content)


def _decode_upload(raw: bytes) -> str:
    """上传文件按 UTF-8 → GBK → 忽略错误 依次尝试解码。"""
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


@app.post("/api/papers/upload")
async def upload_paper(file: UploadFile = File(...), title: str = Form("")):
    """上传 .txt / .md 导入；标题留空时用文件名（去扩展名）。"""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".txt", ".md", ".markdown"}:
        raise HTTPException(status_code=400, detail="仅支持 .txt / .md 文件")

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="文件内容为空")
    if len(raw) > MAX_CONTENT_CHARS * 4:
        raise HTTPException(status_code=413, detail="文件过大，请拆分后再导入")

    content = _decode_upload(raw)
    paper_title = (title or "").strip() or (Path(file.filename or "未命名论文").stem)
    return _import_paper(paper_title[:255], content.strip())


@app.post("/api/ask")
async def ask(payload: AskIn):
    """检索 + 生成 + 落库。"""
    try:
        result = rag.answer_question(payload.question)
        qa_id = db.insert_qa(payload.question, result["answer"], result["citations"])
    except db.SeekDBError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    result["id"] = qa_id
    result["question"] = payload.question
    return result


@app.get("/api/history")
async def history(limit: int = 20):
    """最近问答，默认 20 条。"""
    limit = max(1, min(limit, 100))
    try:
        items = db.list_history(limit)
    except db.SeekDBError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"items": items, "total": len(items)}
