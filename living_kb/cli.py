from __future__ import annotations

import argparse
import json
from pathlib import Path

from living_kb.engine import LivingKnowledgeBaseEngine


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Living AI development knowledge base CLI"
    )
    parser.add_argument(
        "--db-path",
        default="kb/knowledge_base.json",
        help="Path to JSON knowledge base state",
    )
    parser.add_argument(
        "--md-path",
        default="kb/knowledge_base.md",
        help="Path to generated markdown summary",
    )
    parser.add_argument(
        "--merge-threshold",
        type=float,
        default=0.30,
        help="Similarity score required to merge into an existing principle",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Create or refresh KB files from current state")

    add_cmd = sub.add_parser("add", help="Add a thought and auto-rewrite the KB")
    add_cmd.add_argument("--text", required=True, help="Thought text to ingest")

    sub.add_parser("show", help="Print current KB JSON to stdout")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    engine = LivingKnowledgeBaseEngine(
        db_path=args.db_path,
        output_markdown_path=args.md_path,
        merge_threshold=args.merge_threshold,
    )

    if args.command == "init":
        kb = engine.load()
        engine.save(kb)
        print(f"Initialized KB at {Path(args.db_path)} and {Path(args.md_path)}")
        return

    if args.command == "add":
        kb = engine.add_thought(args.text)
        print(
            f"Added thought. Version={kb.version}, principles={len(kb.principles)}, thoughts={len(kb.thoughts)}"
        )
        return

    if args.command == "show":
        kb = engine.load()
        print(json.dumps(kb.to_dict(), indent=2))
        return

    parser.error(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
