#!/usr/bin/env python3
"""Build on-demand investment requests for Codex/Agent AI runtimes."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.agent_adapter import (  # noqa: E402
    build_chat_request,
    build_position_review_request,
    build_report_request,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build an investment-agent request JSON.")
    sub = parser.add_subparsers(dest="command", required=True)

    report = sub.add_parser("report", help="Build an on-demand report request.")
    report.add_argument(
        "--no-collect",
        action="store_true",
        help="Do not fetch live market data; emit a prompt shell only.",
    )

    chat = sub.add_parser("chat", help="Build a free-form investment chat request.")
    chat.add_argument("question")
    chat.add_argument("--context", default="")
    chat.add_argument("--yahoo-context", default="")

    position = sub.add_parser("position", help="Build a position review request.")
    position.add_argument("--position-json", required=True)
    position.add_argument("--current-price", type=float)

    args = parser.parse_args()
    if args.command == "report":
        payload = build_report_request(collect=not args.no_collect)
    elif args.command == "chat":
        payload = build_chat_request(
            args.question,
            context=args.context,
            yahoo_ctx=args.yahoo_context,
        )
    elif args.command == "position":
        payload = build_position_review_request(
            json.loads(args.position_json),
            current_price=args.current_price,
        )
    else:
        parser.error("unknown command")

    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
