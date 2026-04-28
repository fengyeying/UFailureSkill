#!/usr/bin/env python3
"""One-shot local skill usage reporter and safe remover for Codex/ClaudeCode."""

from __future__ import annotations

import argparse
from pathlib import Path


def discover_user_skills(home: Path | None = None) -> dict[str, list[Path]]:
    base = home or Path.home()
    roots = [
        base / ".codex" / "skills",
        base / ".claude" / "skills",
    ]
    discovered: dict[str, list[Path]] = {}
    for root in roots:
        if not root.exists():
            continue
        for skill_file in root.glob("*/SKILL.md"):
            skill_dir = skill_file.parent
            if skill_dir.name.startswith("."):
                continue
            if ".system" in skill_dir.parts:
                continue
            discovered.setdefault(skill_dir.name, []).append(skill_dir)
    return discovered


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ufailure_once.py")
    subparsers = parser.add_subparsers(dest="command", required=True)

    stats = subparsers.add_parser("stats")
    stats.add_argument("--since", default="90d")
    stats.add_argument("--json", action="store_true")

    remove = subparsers.add_parser("remove")
    remove.add_argument("skill")
    remove.add_argument("--dry-run", action="store_true")
    remove.add_argument("--confirm", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    parser.parse_args(argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
