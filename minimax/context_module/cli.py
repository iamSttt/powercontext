"""SDD 上下文模块命令行入口。

用法（在 minimax/ 目录下）：
    python -m context_module.cli index --root .
    python -m context_module.cli context --task "为借阅查询增加状态筛选"
    python -m context_module.cli state save --task-id T-001 --step minimax_patch_generated
    python -m context_module.cli state load --task-id T-001
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import state, store


def cmd_index(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    docs = store.collect_documents(root)
    count = store.index_documents(docs)
    print(json.dumps({"indexed": count, "paths": [d.path for d in docs]}, ensure_ascii=False, indent=2))
    return 0


def cmd_context(args: argparse.Namespace) -> int:
    result = store.build_context(args.task, args.top)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    print(f"任务：{result['task']}\n")
    print("命中的来源：")
    for src in result["sources"]:
        print(f"  - [{src['kind']}] {src['path']} — {src['title']}")
    print("\n----- 上下文 -----\n")
    print(result["context"])
    return 0


def cmd_state_save(args: argparse.Namespace) -> int:
    payload = {"step": args.step, "notes": args.notes or "", "task_id": args.task_id}
    response = state.save_state(args.task_id, payload, reason=args.reason)
    print(json.dumps({"saved": True, "task_id": args.task_id, "response": response}, ensure_ascii=False, indent=2))
    return 0


def cmd_state_load(args: argparse.Namespace) -> int:
    prepared = state.load_state(args.task_id)
    print(json.dumps(prepared, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sdd-context", description="SDD 上下文模块")
    sub = parser.add_subparsers(dest="command", required=True)

    p_index = sub.add_parser("index", help="索引 specs/代码/测试到 seekdb")
    p_index.add_argument("--root", default=".")
    p_index.set_defaults(func=cmd_index)

    p_ctx = sub.add_parser("context", help="按任务检索并组装上下文")
    p_ctx.add_argument("--task", required=True)
    p_ctx.add_argument("--top", type=int, default=6)
    p_ctx.add_argument("--json", action="store_true")
    p_ctx.set_defaults(func=cmd_context)

    p_state = sub.add_parser("state", help="任务状态保存/读取（PowerContext）")
    state_sub = p_state.add_subparsers(dest="state_command", required=True)

    p_save = state_sub.add_parser("save")
    p_save.add_argument("--task-id", required=True)
    p_save.add_argument("--step", required=True)
    p_save.add_argument("--notes", default="")
    p_save.add_argument("--reason", default="")
    p_save.set_defaults(func=cmd_state_save)

    p_load = state_sub.add_parser("load")
    p_load.add_argument("--task-id", required=True)
    p_load.set_defaults(func=cmd_state_load)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
