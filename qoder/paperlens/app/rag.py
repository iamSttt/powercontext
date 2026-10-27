"""RAG 主流程：文本分块 → seekdb IK 检索 → MiniMax 生成带引用的回答。"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from app import db

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# ---------------------------------------------------------------------------
# 可调参数（均可用环境变量覆盖）
# ---------------------------------------------------------------------------
MAX_CHUNK_CHARS = int(os.getenv("PAPERLENS_CHUNK_CHARS", "600"))
TOP_K = int(os.getenv("PAPERLENS_TOP_K", "5"))
MINIMAX_API_KEY = os.getenv("MINIMAX_API_KEY", "").strip()
MINIMAX_BASE_URL = os.getenv("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1").rstrip("/")
MINIMAX_MODEL = os.getenv("MINIMAX_MODEL", "MiniMax-M3.1-Flash")
# 推理模型会先消耗 reasoning tokens，max_tokens 给小了正文会空
MINIMAX_MAX_TOKENS = int(os.getenv("MINIMAX_MAX_TOKENS", "2048"))
MINIMAX_TIMEOUT = float(os.getenv("MINIMAX_TIMEOUT", "90"))
# 没有 API Key / 模型调用失败时，是否回退为"检索原文摘录"以保证演示可用
OFFLINE_ANSWER = os.getenv("PAPERLENS_OFFLINE_ANSWER", "1") == "1"

NO_HIT_ANSWER = "未在该论文库中找到相关内容。可以换一种问法，或先导入相关论文。"

SYSTEM_PROMPT = """你是 PaperLens 论文速读助手，帮助读者快速理解已导入的论文段落。

必须遵守的规则：
1. 只依据用户给出的「参考段落」回答，不得使用外部知识，不得编造数据、方法或结论。
2. 每个结论后用 [编号] 标注依据，编号必须来自参考段落；可以同时标注多个，如 [1][3]。
3. 如果参考段落不足以回答，直接说明「根据已导入的段落无法确定……」，并指出缺什么信息，不要猜测。
4. 使用简体中文，先给结论，再分点给出依据；回答紧凑，避免空话。
5. 输出纯文本：可用「-」分点和 **加粗**，不要使用 # 标题、表格或代码块。"""


class MiniMaxError(RuntimeError):
    """MiniMax 调用失败（缺 Key、超时、非 200、空响应）。"""


# ---------------------------------------------------------------------------
# 1. 分块：按段落切分，单块不超过 600 字，超长段落按 600 字硬切
# ---------------------------------------------------------------------------
def split_into_chunks(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []

    # 以空行作为段落边界；整篇没有空行时（PDF/网页复制常见）把连续行并成段落
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", normalized) if p.strip()]
    if len(paragraphs) <= 1:
        merged: list[str] = []
        buffer = ""
        for line in (line.strip() for line in normalized.split("\n")):
            if not line:
                continue
            if not buffer:
                buffer = line
            elif len(buffer) + len(line) + 1 <= max_chars:
                buffer = f"{buffer}\n{line}"
            else:
                merged.append(buffer)
                buffer = line
        if buffer:
            merged.append(buffer)
        paragraphs = merged

    # 段落不跨段合并，保证引用能精确回到原文段落；超长段落按 max_chars 硬切
    chunks: list[str] = []
    for para in paragraphs:
        if len(para) <= max_chars:
            chunks.append(para)
        else:
            chunks.extend(para[i : i + max_chars] for i in range(0, len(para), max_chars))
    return chunks


# ---------------------------------------------------------------------------
# 2. 检索：全部下推到 seekdb 的 MATCH...AGAINST
# ---------------------------------------------------------------------------
# 只剔除疑问词与标点，保留"方法""实验""准确率"这类实词
_QUESTION_WORDS = (
    "请问", "帮我", "我想知道", "论文里", "论文中", "这篇文章", "本文", "这篇",
    "论文", "作者", "是什么", "有什么", "有哪些", "怎么样", "怎么", "如何",
    "为什么", "哪些", "哪个", "是否", "能否", "可以", "那么", "以及", "一下", "吗", "呢",
    "什么",
)
_PUNCT_RE = re.compile(r"[^\w\u4e00-\u9fff]+")


def extract_keywords(question: str) -> str:
    """从问句中粗提关键词，仅作为原问句检索的召回补充。"""
    text = _PUNCT_RE.sub(" ", question or "")
    for word in _QUESTION_WORDS:
        text = text.replace(word, " ")
    words = [w for w in text.split() if w]
    # 去重且保持出现顺序
    return " ".join(dict.fromkeys(words))


# 中文按 2-gram 切分，用于剔除只命中“体现/怎么样”等泛词的假召回
_TOKEN_RE = re.compile(r"[0-9A-Za-z\u4e00-\u9fff]+")


def _bigrams(text: str) -> set[str]:
    grams: set[str] = set()
    for token in _TOKEN_RE.findall(text or ""):
        if len(token) == 1:
            grams.add(token)
        for i in range(len(token) - 1):
            grams.add(token[i : i + 2])
    return grams


def is_relevant(question: str, content: str) -> bool:
    """低相关度过滤：检索与打分全部由 seekdb 完成，这里只做重叠校验。"""
    query_grams = _bigrams(extract_keywords(question)) or _bigrams(question)
    if not query_grams:
        return True
    return bool(query_grams & _bigrams(content))


def retrieve(question: str, top_k: int = TOP_K) -> list[dict[str, Any]]:
    """先按原问句检索；不足 top_k 时用关键词补一轮，统一去重。"""
    hits = [row for row in db.search_chunks(question, top_k) if is_relevant(question, row["content"])]
    if len(hits) >= top_k:
        return hits[:top_k]

    keywords = extract_keywords(question)
    if keywords and keywords != (question or "").strip():
        seen = {row["id"] for row in hits}
        for row in db.search_chunks(keywords, top_k):
            if row["id"] not in seen and is_relevant(question, row["content"]):
                hits.append(row)
                seen.add(row["id"])
    return hits[:top_k]


def build_citations(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    citations = []
    for idx, row in enumerate(hits, start=1):
        content = row["content"] or ""
        citations.append(
            {
                "index": idx,
                "chunk_id": row["id"],
                "paper_title": row["paper_title"],
                "chunk_index": int(row["chunk_index"]),
                "snippet": content[:180].replace("\n", " "),
                "content": content,
                "score": round(float(row.get("score") or 0), 4),
            }
        )
    return citations


def build_context(hits: list[dict[str, Any]]) -> str:
    blocks = []
    for idx, row in enumerate(hits, start=1):
        content = (row["content"] or "").strip()
        if len(content) > 1200:  # 控制上下文长度，避免无谓的 token 消耗
            content = content[:1200] + "……"
        blocks.append(
            f"[{idx}] 论文《{row['paper_title']}》第 {int(row['chunk_index']) + 1} 段\n{content}"
        )
    return "\n\n".join(blocks)


# ---------------------------------------------------------------------------
# 3. 生成：MiniMax OpenAI 兼容接口
# ---------------------------------------------------------------------------
def call_minimax(question: str, context: str) -> str:
    if not MINIMAX_API_KEY:
        raise MiniMaxError(
            "未配置 MINIMAX_API_KEY：请在环境变量或 .env 中填入 MiniMax API Key 后重启服务。"
        )

    payload = {
        "model": MINIMAX_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"问题：{question}\n\n参考段落：\n{context}"},
        ],
        "max_tokens": MINIMAX_MAX_TOKENS,
        "temperature": 0.3,
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {MINIMAX_API_KEY}",
        "Content-Type": "application/json",
    }
    try:
        with httpx.Client(timeout=httpx.Timeout(MINIMAX_TIMEOUT, connect=10.0)) as client:
            resp = client.post(f"{MINIMAX_BASE_URL}/chat/completions", json=payload, headers=headers)
    except httpx.TimeoutException as exc:
        raise MiniMaxError(
            f"调用 MiniMax 超时（>{MINIMAX_TIMEOUT:.0f}s），请稍后重试或调大 MINIMAX_TIMEOUT。"
        ) from exc
    except httpx.HTTPError as exc:
        raise MiniMaxError(f"调用 MiniMax 网络异常：{exc}") from exc

    if resp.status_code != 200:
        raise MiniMaxError(f"MiniMax 返回 HTTP {resp.status_code}：{resp.text[:300]}")

    try:
        data = resp.json()
    except json.JSONDecodeError as exc:
        raise MiniMaxError(f"MiniMax 返回非 JSON 内容：{resp.text[:300]}") from exc

    choices = data.get("choices") or []
    message = (choices[0].get("message") if choices else {}) or {}
    answer = (message.get("content") or "").strip()
    if not answer:
        # 推理模型偶尔把内容只放在 reasoning_content 里
        answer = (message.get("reasoning_content") or "").strip()
    if not answer:
        raise MiniMaxError(f"MiniMax 返回内容为空：{json.dumps(data, ensure_ascii=False)[:300]}")
    return answer


def offline_answer(citations: list[dict[str, Any]]) -> str:
    """降级模式：不调用模型，直接把命中的原文摘录按引用编号列出。

    失败原因通过接口的 note 字段返回，前端统一展示，避免正文重复。
    """
    lines = ["以下为 seekdb 检索到的原文摘录，未经模型归纳：", ""]
    for item in citations:
        lines.append(
            f"[{item['index']}] 《{item['paper_title']}》第 {item['chunk_index'] + 1} 段：{item['snippet']}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 4. 编排：一次问答的完整流程
# ---------------------------------------------------------------------------
def answer_question(question: str) -> dict[str, Any]:
    hits = retrieve(question)
    if not hits:
        return {
            "answer": NO_HIT_ANSWER,
            "citations": [],
            "degraded": False,
            "note": "no_hits",
            "hits": 0,
        }

    citations = build_citations(hits)
    context = build_context(hits)
    try:
        answer = call_minimax(question, context)
        return {
            "answer": answer,
            "citations": citations,
            "degraded": False,
            "note": None,
            "hits": len(hits),
        }
    except MiniMaxError as exc:
        # 有 Key 但调用失败时也降级，保证用户仍能看到依据；同时明确告知失败原因
        if not OFFLINE_ANSWER:
            raise
        return {
            "answer": offline_answer(citations),
            "citations": citations,
            "degraded": True,
            "note": str(exc),
            "hits": len(hits),
        }
