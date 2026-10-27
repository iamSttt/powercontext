"""PowerContext 任务状态：保存、重启后读取并复用于后续任务。"""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

BASE_URL = os.environ.get("POWERCONTEXT_URL", "http://127.0.0.1:8000").rstrip("/")
TOKEN = os.environ.get("POWERCONTEXT_TOKEN")
SCOPE_CACHE = Path(__file__).resolve().parent.parent / ".powercontext_scope.json"


def _request(method: str, path: str, payload: dict | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = Request(f"{BASE_URL}{path}", data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=15) as response:
            return json.load(response)
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"PowerContext {path} HTTP {error.code}: {detail}") from error


def ensure_scope() -> str:
    """获取或创建稳定的 Scope，并把 scope_id 缓存在项目内。"""
    if SCOPE_CACHE.exists():
        data = json.loads(SCOPE_CACHE.read_text(encoding="utf-8"))
        if data.get("scope_id"):
            return data["scope_id"]
    created = _request(
        "POST",
        "/v1/scopes",
        {
            "title": "SDD 上下文管理（MiniMax 赛题）",
            "summary": "为 AI 开发助手提供规格、代码与测试上下文，并保存任务进展",
            "idempotency_key": "minimax-sdd-context",
        },
    )
    scope_id = created["scope_id"]
    SCOPE_CACHE.write_text(json.dumps({"scope_id": scope_id}, ensure_ascii=False), encoding="utf-8")
    return scope_id


def save_state(task_id: str, state: dict, reason: str = "") -> dict:
    """把任务状态写入 PowerContext（显式 Memory）。"""
    scope_id = ensure_scope()
    text = json.dumps({"task_id": task_id, "state": state}, ensure_ascii=False, sort_keys=True)
    return _request(
        "POST",
        "/v1/memory/remember",
        {
            "scope_id": scope_id,
            "kind": "decision",
            "text": text,
            "reason": reason or f"task state for {task_id}",
        },
    )


def load_state(query: str, max_bytes: int = 4000) -> dict:
    """重启后按任务号召回已保存的状态（PreparedContext）。"""
    scope_id = ensure_scope()
    return _request(
        "POST",
        "/v1/context/prepare",
        {"scope_id": scope_id, "query": query, "max_bytes": max_bytes},
    )
