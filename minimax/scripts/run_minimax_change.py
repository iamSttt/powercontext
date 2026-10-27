"""调用 MiniMax 完成一次实际代码修改，并保存完整开发记录。

流程：
1. 从 seekdb 检索任务相关的规格/代码/测试（SDD 上下文）
2. 从 PowerContext 读取该任务已保存的进展（状态复用）
3. 把上下文交给 MiniMax-M3.1-Flash，要求输出完整的新 loans.py
4. 保存开发记录（请求、响应、token 用量）与模型输出的代码
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from context_module import state, store  # noqa: E402

TASK_ID = "T-001"
TASK = "为借阅查询 list_loans 增加 status 筛选参数（borrowed/returned），保持现有行为不变"
MODEL = os.environ.get("MINIMAX_MODEL", "MiniMax-M3.1-Flash")
API_URL = "https://api.minimaxi.com/v1/chat/completions"
API_KEY = os.environ["MINIMAX_API_KEY"]


def read_full_sources(sources: list[dict]) -> str:
    blocks = []
    for src in sources:
        path = ROOT / src["path"]
        if path.exists():
            blocks.append(f"### 文件：{src['path']}（{src['kind']}）\n```\n{path.read_text(encoding='utf-8')}\n```")
    return "\n\n".join(blocks)


def call_minimax(messages: list[dict], max_tokens: int = 4096) -> dict:
    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }
    req = Request(
        API_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req, timeout=300) as resp:
        return json.load(resp)


def extract_code(text: str) -> str | None:
    blocks = re.findall(r"```(?:python)?\s*\n(.*?)```", text, flags=re.S)
    if not blocks:
        return None
    return max(blocks, key=len).strip() + "\n"


def main() -> int:
    # 1) seekdb 检索上下文
    retrieved = store.build_context(TASK, top_k=5)
    sources = retrieved["sources"]

    # 2) PowerContext 状态复用
    prior_state = state.load_state(TASK_ID)
    prior_content = prior_state.get("content") or "（无历史状态）"

    # 3) 组装提示词
    full_sources = read_full_sources(sources)
    system_prompt = (
        "你是资深 Python 工程师，严格遵循规格驱动开发（SDD）。"
        "只能依据给定的规格、代码与测试作答，不要发明未被规格要求的行为。"
        "输出要求：给出完整修改后的 business/library/loans.py 内容，放在一个 ```python 代码块中；"
        "不要输出其他代码块，不要省略任何函数。"
    )
    user_prompt = f"""## 本次任务
{TASK}

## 已保存的任务进展（PowerContext，来自上一轮）
{prior_content}

## 检索到的工程资料（来源：seekdb 全文检索）
{full_sources}

## 硬性要求
1. 按 specs/loan-status-filter.md 的验收标准实现 status 参数（None/borrowed/returned/非法值 ValueError）。
2. 满足 specs/regression-contract.md：Loan、status 属性、is_overdue、summarize 行为不变。
3. business/tests/test_loans_filter.py 的 5 个目标测试必须通过，business/tests/test_loans_basic.py 的回归测试必须继续通过。
4. 只修改 business/library/loans.py，不要修改测试或其他文件。
"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    # 4) 调用 MiniMax
    started = datetime.now()
    response = call_minimax(messages)
    elapsed = (datetime.now() - started).total_seconds()
    choice = response["choices"][0]
    content = choice["message"].get("content") or ""
    reasoning = choice["message"].get("reasoning_content") or ""
    usage = response.get("usage", {})

    # 5) 保存开发记录
    records = ROOT / "records"
    records.mkdir(exist_ok=True)
    code = extract_code(content)
    (records / "minimax-output-loans.py").write_text(code or "", encoding="utf-8")

    record = f"""# MiniMax 开发记录

- 时间：{started.strftime('%Y-%m-%d %H:%M:%S')}
- 模型：{MODEL}
- 任务：{TASK}
- 耗时：{elapsed:.1f}s
- Token 用量：{json.dumps(usage, ensure_ascii=False)}
- 检索来源（seekdb）：{json.dumps(sources, ensure_ascii=False)}

## 提示词（system）

```
{system_prompt}
```

## 提示词（user）

```
{user_prompt}
```

## MiniMax 响应（正文）

```
{content}
```

## MiniMax 推理片段（reasoning）

```
{reasoning[:4000]}
```

## 提取出的代码

```python
{code or '（未提取到代码块）'}
```
"""
    (records / "minimax-development-record.md").write_text(record, encoding="utf-8")

    # 6) 更新任务状态（PowerContext）
    state.save_state(
        TASK_ID,
        {
            "step": "minimax_patch_generated",
            "model": MODEL,
            "usage": usage,
            "output_file": "records/minimax-output-loans.py",
        },
        reason="MiniMax 已生成代码补丁",
    )

    print(json.dumps({"ok": bool(code), "elapsed": round(elapsed, 1), "usage": usage}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
