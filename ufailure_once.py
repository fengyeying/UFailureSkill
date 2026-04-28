#!/usr/bin/env python3
"""One-shot local skill usage reporter and safe remover for Codex/ClaudeCode."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


USING_RE = re.compile(r"\bUsing\s+([A-Za-z0-9_.:-]+)\s+skill\b", re.IGNORECASE)
CN_USING_RE = re.compile(r"使用(?:了)?\s*([A-Za-z0-9_.:-]+)\s*(?:skill|技能)", re.IGNORECASE)
SLASH_RE = re.compile(r"(?<!\w)/([A-Za-z0-9_.:-]+)\b")
MD_SKILL_RE = re.compile(r"\[\$?([A-Za-z0-9_.:-]+)\]\([^)]*/SKILL\.md\)")

SKILL_LIST_MARKERS = (
    "### Available skills",
    "Available skills",
    "The following skills are available",
    "user-invocable skills",
)
TOOL_USE_SKILL_TOOL_NAMES = {"Skill", "skill", "use_skill"}


@dataclass
class Usage:
    skill: str
    uses: int = 0
    last_used: datetime | None = None


def iter_log_files(home: Path | None = None) -> Iterable[Path]:
    base = home or Path.home()
    roots = [
        base / ".codex" / "sessions",
        base / ".codex" / "archived_sessions",
        base / ".claude" / "projects",
    ]
    for root in roots:
        if root.exists():
            yield from root.glob("**/*.jsonl")


def iter_json_nodes(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from iter_json_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_json_nodes(child)


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def extract_tool_use_skills(row: Any, known_skills: set[str]) -> set[str]:
    """Pull skill names from structured tool_use nodes (ClaudeCode's main path)."""
    found: set[str] = set()
    for node in iter_json_nodes(row):
        if not isinstance(node, dict):
            continue
        if node.get("type") != "tool_use":
            continue
        if node.get("name") not in TOOL_USE_SKILL_TOOL_NAMES:
            continue
        skill_input = node.get("input")
        if not isinstance(skill_input, dict):
            continue
        skill = skill_input.get("skill")
        if isinstance(skill, str) and skill in known_skills:
            found.add(skill)
    return found


def extract_text_skills(row: Any, known_skills: set[str]) -> set[str]:
    """Pull skill names from textual mentions, skipping skill-listing leaves only."""
    found: set[str] = set()
    for node in iter_json_nodes(row):
        if not isinstance(node, str):
            continue
        if any(marker in node for marker in SKILL_LIST_MARKERS):
            continue
        for regex in (USING_RE, CN_USING_RE, MD_SKILL_RE, SLASH_RE):
            for match in regex.findall(node):
                if match in known_skills:
                    found.add(match)
    return found


def collect_usage(home: Path | None, known_skills: set[str], since_days: int) -> dict[str, Usage]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
    usage = {name: Usage(skill=name) for name in known_skills}

    for log_file in iter_log_files(home):
        try:
            file_mtime = datetime.fromtimestamp(log_file.stat().st_mtime, tz=timezone.utc)
        except OSError:
            file_mtime = None
        if file_mtime is not None and file_mtime < cutoff:
            continue
        for line in log_file.read_text(errors="ignore").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = parse_timestamp(row.get("timestamp") or row.get("updated_at")) or file_mtime
            if ts is not None and ts < cutoff:
                continue
            mentions = extract_tool_use_skills(row, known_skills)
            mentions.update(extract_text_skills(row, known_skills))
            for skill in mentions:
                entry = usage.setdefault(skill, Usage(skill=skill))
                entry.uses += 1
                if ts is not None and (entry.last_used is None or ts > entry.last_used):
                    entry.last_used = ts
    return usage


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
