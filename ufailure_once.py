#!/usr/bin/env python3
"""One-shot local skill usage reporter and safe remover for Codex/ClaudeCode."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


USING_RE = re.compile(r"\bUsing\s+([A-Za-z0-9_.:-]+)\s+skill\b", re.IGNORECASE)
CN_USING_RE = re.compile(r"使用(?:了)?\s*([A-Za-z0-9_.:-]+)\s*(?:skill|技能)", re.IGNORECASE)
LEADING_SLASH_RE = re.compile(r"\A\s*/([A-Za-z0-9_.:-]+)\b")
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
        for regex in (USING_RE, CN_USING_RE, MD_SKILL_RE, LEADING_SLASH_RE):
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


SCOPE_USER = "user"
SCOPE_PROJECT = "proj"
SCOPE_PLUGIN = "plug"

_VERSION_RE = re.compile(r"^\d+(\.\d+)+$")
_PLUGIN_PATH_NOISE = {"plugins", "cache", "marketplaces", "external_plugins"}


@dataclass
class DiscoveredSkill:
    """A skill discovered on disk, with the scope it belongs to.

    `name` is the bare directory name for `user` / `proj` scopes; for `plug`
    scope it's the namespaced form `<plugin>:<skill>` that matches what
    ClaudeCode emits in its Skill tool_use payloads.
    """

    name: str
    paths: list[Path]
    scope: str
    removable: bool


def _gather_user_dir_skills(roots: list[Path], scope: str) -> dict[str, DiscoveredSkill]:
    found: dict[str, DiscoveredSkill] = {}
    for root in roots:
        if not root.exists():
            continue
        for skill_file in root.glob("*/SKILL.md"):
            skill_dir = skill_file.parent
            if skill_dir.name.startswith("."):
                continue
            if ".system" in skill_dir.parts:
                continue
            entry = found.setdefault(
                skill_dir.name, DiscoveredSkill(skill_dir.name, [], scope, removable=True)
            )
            entry.paths.append(skill_dir)
    return found


def _gather_plugin_skills(plugins_root: Path) -> dict[str, DiscoveredSkill]:
    found: dict[str, DiscoveredSkill] = {}
    if not plugins_root.exists():
        return found
    for skill_file in plugins_root.glob("**/skills/*/SKILL.md"):
        parts = skill_file.parts
        try:
            skills_idx = max(i for i, p in enumerate(parts[:-1]) if p == "skills")
        except ValueError:
            continue
        plugin_name: str | None = None
        for i in range(skills_idx - 1, 0, -1):
            seg = parts[i]
            if seg in _PLUGIN_PATH_NOISE or _VERSION_RE.match(seg):
                continue
            plugin_name = seg
            break
        if not plugin_name:
            continue
        skill_name = parts[skills_idx + 1]
        full_name = f"{plugin_name}:{skill_name}"
        entry = found.setdefault(
            full_name, DiscoveredSkill(full_name, [], SCOPE_PLUGIN, removable=False)
        )
        entry.paths.append(skill_file.parent)
    return found


def discover_skills(
    home: Path | None = None,
    project_root: Path | None = None,
) -> list[DiscoveredSkill]:
    """Discover skills across user-global, project-local, and plugin scopes.

    `project_root` defaults to `Path.cwd()`; pass `None` to use cwd or pass a
    specific path. If project_root resolves to the same directory as `home`,
    project discovery is skipped to avoid duplicating user-global entries.
    """
    base = home or Path.home()
    project = project_root if project_root is not None else Path.cwd()

    user_skills = _gather_user_dir_skills(
        [base / ".codex" / "skills", base / ".claude" / "skills"],
        scope=SCOPE_USER,
    )

    project_skills: dict[str, DiscoveredSkill] = {}
    try:
        same_as_home = project.resolve() == base.resolve()
    except OSError:
        same_as_home = False
    if not same_as_home:
        project_skills = _gather_user_dir_skills(
            [project / ".codex" / "skills", project / ".claude" / "skills"],
            scope=SCOPE_PROJECT,
        )

    plugin_skills = _gather_plugin_skills(base / ".claude" / "plugins")

    return [*user_skills.values(), *project_skills.values(), *plugin_skills.values()]


def discover_user_skills(home: Path | None = None) -> dict[str, list[Path]]:
    """Backwards-compatible: return user-global skills only as name -> paths.

    Used by older call sites and tests. New code should call `discover_skills`
    to get scope-aware results.
    """
    skills = discover_skills(home=home)
    return {s.name: s.paths for s in skills if s.scope == SCOPE_USER}


BAR_WIDTH = 16


@dataclass(frozen=True)
class Glyphs:
    """Bundle of single-character symbols used to render the report.

    Two variants exist: a rich Unicode set for direct terminal use and an
    ASCII-only set for agent harnesses whose renderers may swallow or
    truncate non-ASCII output (e.g. Codex Desktop choking on the block-
    drawing characters and emitting just the first letter of the report).
    """

    bar_full: str
    bar_partials: str  # 8 chars indexed by remainder eighths
    bar_zero: str
    rule: str
    ellipsis: str
    warn: str
    use_partials: bool


RICH_GLYPHS = Glyphs(
    bar_full="█",
    bar_partials=" ▏▎▍▌▋▊▉",
    bar_zero="·",
    rule="─",
    ellipsis="…",
    warn="⚠",
    use_partials=True,
)

ASCII_GLYPHS = Glyphs(
    bar_full="#",
    bar_partials="        ",  # ignored when use_partials is False
    bar_zero=".",
    rule="-",
    ellipsis="..",
    warn="!",
    use_partials=False,
)


def select_glyphs(
    *,
    force_rich: bool = False,
    force_ascii: bool = False,
    isatty: bool | None = None,
) -> Glyphs:
    """Pick a glyph bundle: explicit flags win, otherwise default to ASCII
    unless stdout is a real TTY. Agents typically run via captured pipes,
    so the ASCII default keeps their renderings safe."""
    if force_ascii:
        return ASCII_GLYPHS
    if force_rich:
        return RICH_GLYPHS
    tty = sys.stdout.isatty() if isatty is None else isatty
    return RICH_GLYPHS if tty else ASCII_GLYPHS


def parse_days(value: str) -> int:
    raw = value[:-1] if value.endswith("d") else value
    days = int(raw)
    if days <= 0:
        raise ValueError(f"--since must be a positive number of days, got {value!r}")
    return days


def build_report_rows(
    usage: dict[str, Usage],
    candidate_threshold: int,
    paths_by_skill: dict[str, list[Path]] | None = None,
    skill_scopes: dict[str, str] | None = None,
    removable_skills: set[str] | None = None,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    total_uses = sum(entry.uses for entry in usage.values())
    paths_by_skill = paths_by_skill or {}
    skill_scopes = skill_scopes or {}
    removable_skills = removable_skills if removable_skills is not None else set(usage)
    for entry in usage.values():
        percent = (entry.uses / total_uses * 100) if total_uses else 0.0
        is_removable = entry.skill in removable_skills
        # Only removable skills with low usage are deletion candidates;
        # plugin skills are never candidates because the user can't act on them here.
        is_candidate = is_removable and entry.uses <= candidate_threshold
        rows.append(
            {
                "skill": entry.skill,
                "uses": entry.uses,
                "percent": round(percent, 1),
                "last_used": entry.last_used.isoformat() if entry.last_used else None,
                "candidate": is_candidate,
                "paths": len(paths_by_skill.get(entry.skill, [])),
                "scope": skill_scopes.get(entry.skill, SCOPE_USER),
                "removable": is_removable,
            }
        )
    return sorted(rows, key=lambda row: (-int(row["uses"]), str(row["skill"])))


def render_bar(uses: int, max_uses: int, glyphs: Glyphs = RICH_GLYPHS, width: int = BAR_WIDTH) -> str:
    if max_uses <= 0:
        return " " * width
    if uses <= 0:
        return glyphs.bar_zero + " " * (width - 1)
    if glyphs.use_partials:
        eighths = max(1, round((uses / max_uses) * width * 8))
        full, remainder = divmod(eighths, 8)
        full = min(full, width)
        bar = glyphs.bar_full * full
        if remainder and full < width:
            bar += glyphs.bar_partials[remainder]
    else:
        full = max(1, round((uses / max_uses) * width))
        full = min(full, width)
        bar = glyphs.bar_full * full
    return bar.ljust(width)


def truncate_name(name: str, width: int, glyphs: Glyphs = RICH_GLYPHS) -> str:
    if len(name) <= width:
        return name
    if width <= len(glyphs.ellipsis):
        return name[:width]
    head = (width - len(glyphs.ellipsis)) // 2
    tail = width - len(glyphs.ellipsis) - head
    return f"{name[:head]}{glyphs.ellipsis}{name[-tail:]}"


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


def print_text_report(
    rows: list[dict[str, object]],
    since_days: int,
    glyphs: Glyphs = RICH_GLYPHS,
    show_all: bool = False,
) -> None:
    if not rows:
        print("No skills discovered under ~/.codex/skills/, ~/.claude/skills/, or ~/.claude/plugins/.")
        return
    now = datetime.now(timezone.utc)
    max_uses = max((int(row["uses"]) for row in rows), default=0)
    plugin_count = sum(1 for row in rows if row.get("scope") == SCOPE_PLUGIN)
    plugin_unused = sum(
        1 for row in rows if row.get("scope") == SCOPE_PLUGIN and int(row["uses"]) == 0
    )
    used_count = sum(1 for row in rows if int(row["uses"]) > 0)

    # Unused plugin skills create most of the visual noise on a typical
    # ClaudeCode setup (dozens of plugins, only a handful invoked). Hide
    # them by default; users can pass `--all` to see the full inventory.
    visible = (
        rows
        if show_all
        else [
            row
            for row in rows
            if not (row.get("scope") == SCOPE_PLUGIN and int(row["uses"]) == 0)
        ]
    )
    actives = [row for row in visible if not row["candidate"]]
    candidates = [row for row in visible if row["candidate"]]
    hidden_plugins = plugin_unused if not show_all else 0

    rule = glyphs.rule
    width = 80

    print(f"  Local Skill Usage - Last {since_days} days")
    if plugin_count:
        print(f"  Scope codes: user = ~/.claude/skills/  proj = ./.claude/skills/  plug = plugin (read-only, manage via /plugin).")
    print("  " + rule * width)
    print(f"  {'Skill':28}  {'Scope':<5}  {'Uses':>4}  {'Share':>6}  {'Bar':<16}  Last used")
    print("  " + rule * width)
    for row in actives:
        bar = render_bar(int(row["uses"]), max_uses, glyphs)
        last = render_relative(row["last_used"], now)
        name = truncate_name(str(row["skill"]), 28, glyphs)
        scope = str(row.get("scope", SCOPE_USER))
        print(f"  {name:28}  {scope:<5}  {int(row['uses']):>4}  {float(row['percent']):>5.1f}%  {bar}  {last}")
    if hidden_plugins:
        print(f"  ... {hidden_plugins} unused plugin skills hidden (use --all to show)")
    if candidates:
        print("  " + rule * 6 + " Failure Skills (removable, uses <= 1) " + rule * 35)
        for index, row in enumerate(candidates, start=1):
            bar = render_bar(int(row["uses"]), max_uses, glyphs)
            last = render_relative(row["last_used"], now)
            prefix = f"[{index}]"
            name = truncate_name(str(row["skill"]), 23, glyphs)
            scope = str(row.get("scope", SCOPE_USER))
            paths_count = int(row.get("paths", 0))
            suffix = f"  {glyphs.warn} {paths_count} paths" if paths_count > 1 else ""
            print(f"  {prefix:<4} {name:23}  {scope:<5}  {int(row['uses']):>4}  {float(row['percent']):>5.1f}%  {bar}  {last}{suffix}")
    print("  " + rule * width)
    summary = f"  Total {len(rows)} - Used {used_count} - Failure Skills {len(candidates)}"
    if plugin_count:
        summary += f" - Plugin {plugin_count} (read-only)"
    print(summary)
    if candidates:
        print()
        print("  Which Failure Skills should be removed? Reply with numbers (for example 1,2), all, or skip.")


def user_skill_roots(
    home: Path | None = None, project_root: Path | None = None
) -> list[Path]:
    base = home or Path.home()
    project = project_root if project_root is not None else Path.cwd()
    roots = [
        base / ".codex" / "skills",
        base / ".claude" / "skills",
    ]
    try:
        same_as_home = project.resolve() == base.resolve()
    except OSError:
        same_as_home = False
    if not same_as_home:
        roots.extend(
            [
                project / ".codex" / "skills",
                project / ".claude" / "skills",
            ]
        )
    return roots


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def find_removable_skill_paths(
    skill: str, home: Path | None = None, project_root: Path | None = None
) -> list[Path]:
    """Plugin-namespaced names (containing ':') are never removable here."""
    if ":" in skill:
        return []
    paths: list[Path] = []
    for root in user_skill_roots(home, project_root):
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


def remove_skill(
    skill: str, confirm: bool, home: Path | None = None, project_root: Path | None = None
) -> list[Path]:
    paths = find_removable_skill_paths(skill, home, project_root)
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
    stats.add_argument(
        "--all",
        dest="show_all",
        action="store_true",
        help="Include unused plugin skills in the text report (hidden by default).",
    )
    glyph_group = stats.add_mutually_exclusive_group()
    glyph_group.add_argument(
        "--ascii",
        dest="ascii_only",
        action="store_true",
        help="Force ASCII-only output (safe for agent harnesses).",
    )
    glyph_group.add_argument(
        "--rich",
        action="store_true",
        help="Force Unicode block-character output (default when stdout is a TTY).",
    )

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
        discovered = discover_skills()
        known = {s.name for s in discovered}
        paths_by_skill = {s.name: s.paths for s in discovered}
        skill_scopes = {s.name: s.scope for s in discovered}
        removable_skills = {s.name for s in discovered if s.removable}
        usage = collect_usage(home=None, known_skills=known, since_days=since_days)
        rows = build_report_rows(
            usage,
            candidate_threshold=1,
            paths_by_skill=paths_by_skill,
            skill_scopes=skill_scopes,
            removable_skills=removable_skills,
        )
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            glyphs = select_glyphs(force_rich=args.rich, force_ascii=args.ascii_only)
            print_text_report(rows, since_days=since_days, glyphs=glyphs, show_all=args.show_all)
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
