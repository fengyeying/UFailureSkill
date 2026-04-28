#!/usr/bin/env python3
"""One-shot local skill usage reporter and safe remover for Codex/ClaudeCode."""

from __future__ import annotations

import argparse
import json
import re
import shutil
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
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


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


BAR_PARTIALS = " ▏▎▍▌▋▊▉"
BAR_FULL = "█"
BAR_WIDTH = 16
RULE = "─"


def parse_days(value: str) -> int:
    if value.endswith("d"):
        return int(value[:-1])
    return int(value)


def build_report_rows(usage: dict[str, Usage], candidate_threshold: int) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    total_uses = sum(entry.uses for entry in usage.values())
    for entry in usage.values():
        percent = (entry.uses / total_uses * 100) if total_uses else 0.0
        rows.append(
            {
                "skill": entry.skill,
                "uses": entry.uses,
                "percent": round(percent, 1),
                "last_used": entry.last_used.isoformat() if entry.last_used else None,
                "candidate": entry.uses <= candidate_threshold,
            }
        )
    return sorted(rows, key=lambda row: (-int(row["uses"]), str(row["skill"])))


def render_bar(uses: int, max_uses: int, width: int = BAR_WIDTH) -> str:
    if max_uses <= 0:
        return " " * width
    if uses <= 0:
        return "·" + " " * (width - 1)
    eighths = max(1, round((uses / max_uses) * width * 8))
    full, remainder = divmod(eighths, 8)
    full = min(full, width)
    bar = BAR_FULL * full
    if remainder and full < width:
        bar += BAR_PARTIALS[remainder]
    return bar.ljust(width)


def render_relative(last_used: object, now: datetime) -> str:
    if not last_used:
        return "Never used"
    try:
        last = datetime.fromisoformat(str(last_used))
    except ValueError:
        return str(last_used)
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    delta = (now - last).days
    if delta <= 0:
        return "Today"
    if delta == 1:
        return "Yesterday"
    return f"{delta} days ago"


def print_text_report(rows: list[dict[str, object]], since_days: int) -> None:
    if not rows:
        print("No removable user skills found (scanned only ~/.codex/skills/ and ~/.claude/skills/).")
        return
    now = datetime.now(timezone.utc)
    max_uses = max((int(row["uses"]) for row in rows), default=0)
    actives = [row for row in rows if not row["candidate"]]
    candidates = [row for row in rows if row["candidate"]]

    print(f"  Local Skill Usage · Last {since_days} days")
    print("  " + RULE * 76)
    print(f"  {'Skill':28}  {'Uses':>4}  {'Share':>6}  {'Bar':<16}  Last used")
    print("  " + RULE * 76)
    for row in actives:
        bar = render_bar(int(row["uses"]), max_uses)
        last = render_relative(row["last_used"], now)
        name = str(row["skill"])[:28]
        print(f"  {name:28}  {int(row['uses']):>4}  {float(row['percent']):>5.1f}%  {bar}  {last}")
    if candidates:
        print("  " + RULE * 6 + " Failure Skills (uses <= 1) " + RULE * 38)
        for index, row in enumerate(candidates, start=1):
            bar = render_bar(int(row["uses"]), max_uses)
            last = render_relative(row["last_used"], now)
            prefix = f"[{index}]"
            name = str(row["skill"])[:23]
            print(f"  {prefix:<4} {name:23}  {int(row['uses']):>4}  {float(row['percent']):>5.1f}%  {bar}  {last}")
    print("  " + RULE * 76)
    print(f"  Total {len(rows)} · Active {len(actives)} · Failure Skills {len(candidates)}")
    if candidates:
        print()
        print("  Which Failure Skills should be removed? Reply with numbers (for example 1,2), all, or skip.")


def user_skill_roots(home: Path | None = None) -> list[Path]:
    base = home or Path.home()
    return [
        base / ".codex" / "skills",
        base / ".claude" / "skills",
    ]


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def find_removable_skill_paths(skill: str, home: Path | None = None) -> list[Path]:
    paths: list[Path] = []
    for root in user_skill_roots(home):
        candidate = root / skill
        if not candidate.exists():
            continue
        if skill.startswith("."):
            continue
        if not is_relative_to(candidate, root):
            continue
        if not (candidate / "SKILL.md").exists():
            continue
        paths.append(candidate)
    return paths


def remove_skill(skill: str, confirm: bool, home: Path | None = None) -> list[Path]:
    paths = find_removable_skill_paths(skill, home)
    if confirm:
        for path in paths:
            shutil.rmtree(path)
    return paths


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
    args = parser.parse_args(argv)

    if args.command == "stats":
        since_days = parse_days(args.since)
        skills = discover_user_skills()
        usage = collect_usage(home=None, known_skills=set(skills), since_days=since_days)
        rows = build_report_rows(usage, candidate_threshold=1)
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            print_text_report(rows, since_days=since_days)
        return 0

    if args.command == "remove":
        if args.dry_run == args.confirm:
            parser.error("choose exactly one of --dry-run or --confirm")
        paths = remove_skill(args.skill, confirm=args.confirm, home=None)
        if not paths:
            print(f"  ✗ {args.skill}: no removable user skill found")
            return 1
        glyph = "✓" if args.confirm else "·"
        action = "Removed" if args.confirm else "Would remove"
        for path in paths:
            print(f"  {glyph} {action}: {path}")
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
