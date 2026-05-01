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


TEXT_MENTION_TERMINATOR_RE = r"(?=\s|[.,;:!?)\]\}，。！？；：、）】》」』]|$)"
USING_RE = re.compile(r"\bUsing\s+([A-Za-z0-9_.:-]+)\s+skill" + TEXT_MENTION_TERMINATOR_RE, re.IGNORECASE)
CN_USING_RE = re.compile(r"使用(?:了)?\s*([A-Za-z0-9_.:-]+)\s*(?:skill|技能)" + TEXT_MENTION_TERMINATOR_RE, re.IGNORECASE)
LEADING_SLASH_RE = re.compile(r"\A\s*/([A-Za-z0-9_.:-]+)\b")
MD_SKILL_RE = re.compile(r"\[\$?([A-Za-z0-9_.:-]+)\]\([^)]*/SKILL\.md\)")
# Codex Desktop activates skills by `cat`ing the SKILL.md path through
# exec_command rather than via a structured Skill tool. Match any path of the
# form `/skills/<name>/SKILL.md` so both Codex exec output and Claude Code
# markdown links land on the same counter.
SKILL_PATH_RE = re.compile(r"/skills/([A-Za-z0-9_.:-]+)/SKILL\.md")

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
    if not isinstance(value, str):
        return None
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


def _resolve_path_skill(node: str, bare: str, match_start: int, known_skills: set[str]) -> str | None:
    """Map a SKILL.md path's directory name to the appropriate known-skill key.

    For paths under ~/.claude/plugins/, the canonical name is namespaced
    (`<plugin>:<skill>`); for paths under user / project skill roots, the
    bare directory name is canonical. Decide based on whether `/plugins/`
    appears in the path prefix so the same `<name>` doesn't double-count
    against both a plugin entry and a same-named user entry.
    """
    prefix = node[:match_start]
    if "/.claude/plugins/" in prefix:
        # Marketplace paths: walk backwards from skills/ to find plugin name
        # (skipping noise dirs and version segments).
        if "/.claude/plugins/marketplaces/" in prefix:
            after = prefix.split("/.claude/plugins/marketplaces/", 1)[1]
            installed_names = {known.split(":", 1)[0] for known in known_skills if ":" in known}
            canonical_name = _find_canonical_plugin_name(after.split("/"), installed_names)
            if canonical_name is None:
                return None
            result = f"{canonical_name}:{bare}"
            return result if result in known_skills else None
        for known in known_skills:
            if ":" not in known:
                continue
            plugin_name, plugin_skill = known.split(":", 1)
            if plugin_skill != bare:
                continue
            if f"/{plugin_name}/" in prefix or prefix.endswith(f"/{plugin_name}"):
                return known
        return None
    if bare in known_skills:
        return bare
    return None


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
        # Codex `cat /.../skills/<name>/SKILL.md` and similar path mentions.
        for match in SKILL_PATH_RE.finditer(node):
            resolved = _resolve_path_skill(node, match.group(1), match.start(), known_skills)
            if resolved is not None:
                found.add(resolved)
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
        try:
            text = log_file.read_text(errors="ignore")
        except OSError as e:
            print(f"  Skipping {log_file}: {e}", file=sys.stderr)
            continue
        for line in text.splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
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
_PLUGIN_PATH_NOISE = {"plugins", "cache", "marketplaces", "external_plugins", ".cursor", ".windsurf"}


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


def _read_installed_plugin_names(plugins_root: Path) -> set[str]:
    """Return installed plugin names from installed_plugins.json."""
    config_path = plugins_root / "installed_plugins.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    if not isinstance(config, dict):
        return set()
    plugins = config.get("plugins")
    if not isinstance(plugins, dict):
        return set()
    return {key.split("@", 1)[0] for key in plugins if isinstance(key, str)}


def _canonical_installed_plugin_name(plugin_name: str, installed_names: Iterable[str]) -> str | None:
    installed = {name for name in installed_names if isinstance(name, str) and name}
    if plugin_name in installed:
        return plugin_name
    for installed_name in sorted(installed, key=len, reverse=True):
        if plugin_name.startswith(installed_name + "-"):
            return installed_name
    return None


def _find_canonical_plugin_name(segments: Iterable[str], installed_names: Iterable[str]) -> str | None:
    for seg in reversed([s for s in segments if s]):
        if seg in _PLUGIN_PATH_NOISE or _VERSION_RE.match(seg):
            continue
        canonical_name = _canonical_installed_plugin_name(seg, installed_names)
        if canonical_name is not None:
            return canonical_name
    return None


def _gather_plugin_skills(plugins_root: Path) -> dict[str, DiscoveredSkill]:
    """Discover installed plugin skills from the marketplace directory.

    Skips the cache (``cache/``) entirely — the marketplace is the source of
    truth and scanning cache caused duplicate entries under SHA-based names.
    Only returns plugins listed in ``installed_plugins.json`` so that
    uninstalled marketplace entries are not included in the report.

    A marketplace sub-plugin matches if its directory name equals or starts
    with (``<name>-``) an installed plugin name.  For example, an installed
    name ``nowledge-mem`` matches marketplace directories like
    ``nowledge-mem-claude-code-plugin``, ``nowledge-mem-codex-plugin``, etc.
    All matched sub-plugins are reported under the canonical installed name.
    """
    found: dict[str, DiscoveredSkill] = {}
    if not plugins_root.exists():
        return found
    installed_names = _read_installed_plugin_names(plugins_root)
    marketplaces = plugins_root / "marketplaces"
    if not marketplaces.exists():
        return found
    for skill_file in marketplaces.glob("**/skills/*/SKILL.md"):
        parts = skill_file.parts
        try:
            skills_idx = max(i for i, p in enumerate(parts[:-1]) if p == "skills")
        except ValueError:
            continue
        skill_name = parts[skills_idx + 1]
        canonical_name = _find_canonical_plugin_name(parts[:skills_idx], installed_names)
        if canonical_name is None:
            continue
        full_name = f"{canonical_name}:{skill_name}"
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
    try:
        days = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid --since value: {value!r}")
    if days <= 0:
        raise argparse.ArgumentTypeError(f"--since must be a positive number of days, got {value!r}")
    return days


def build_report_rows(
    usage: dict[str, Usage],
    candidate_threshold: int,
    paths_by_skill: dict[str, list[Path]] | None = None,
    skill_scopes: dict[str, str] | None = None,
    removable_skills: set[str] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    total_uses = sum(entry.uses for entry in usage.values())
    has_path_inventory = paths_by_skill is not None
    paths_by_skill = paths_by_skill or {}
    skill_scopes = skill_scopes or {}
    removable_skills = removable_skills if removable_skills is not None else set(usage)
    for entry in usage.values():
        percent = (entry.uses / total_uses * 100) if total_uses else 0.0
        is_removable = entry.skill in removable_skills
        paths_count = len(paths_by_skill.get(entry.skill, []))
        # Only single-path removable skills with low usage are deletion candidates;
        # multi-path matches require manual cleanup so one selector cannot delete
        # multiple same-named directories.
        is_candidate = (
            is_removable
            and entry.uses <= candidate_threshold
            and (not has_path_inventory or paths_count == 1)
        )
        rows.append(
            {
                "skill": entry.skill,
                "uses": entry.uses,
                "percent": round(percent, 1),
                "last_used": entry.last_used.isoformat() if entry.last_used else None,
                "candidate": is_candidate,
                "paths": paths_count,
                "scope": skill_scopes.get(entry.skill, SCOPE_USER),
                "removable": is_removable,
            }
        )
    return sorted(rows, key=lambda row: (-int(row["uses"]), str(row["skill"])))


def render_bar(uses: int, max_uses: int, glyphs: Glyphs = RICH_GLYPHS, width: int = BAR_WIDTH) -> str:
    if width <= 0:
        return ""
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


SECTION_TITLES: tuple[tuple[str, str, str], ...] = (
    (SCOPE_USER, "Global skills", "~/.codex/skills/, ~/.claude/skills/ - removable"),
    (SCOPE_PROJECT, "Project skills", "./.codex/skills/, ./.claude/skills/ - removable"),
    (SCOPE_PLUGIN, "Plugin skills", "~/.claude/plugins/ - read-only, manage via /plugin"),
)


def _format_skill_row(
    row: dict[str, Any],
    max_uses: int,
    now: datetime,
    glyphs: Glyphs,
    name_width: int,
) -> str:
    bar = render_bar(int(row["uses"]), max_uses, glyphs)
    last = render_relative(row["last_used"], now)
    name = truncate_name(str(row["skill"]), name_width, glyphs)
    paths_count = int(row.get("paths", 0))
    suffix = f"  {glyphs.warn} {paths_count} paths" if paths_count > 1 else ""
    return (
        f"{name:<{name_width}}  {int(row['uses']):>4}  "
        f"{float(row['percent']):>5.1f}%  {bar}  {last}{suffix}"
    )


def print_text_report(
    rows: list[dict[str, Any]],
    since_days: int,
    glyphs: Glyphs = RICH_GLYPHS,
    show_all: bool = True,  # kept for backwards compatibility; no longer gates anything
) -> None:
    if not rows:
        print("No skills discovered under ~/.codex/skills/, ~/.claude/skills/, or ~/.claude/plugins/.")
        return
    del show_all  # unused
    now = datetime.now(timezone.utc)
    max_uses = max((int(row["uses"]) for row in rows), default=0)
    used_count = sum(1 for row in rows if int(row["uses"]) > 0)

    rule = glyphs.rule
    width = 80
    name_width = 32
    cand_name_width = name_width - 5  # leave 5 chars for "[N]  "

    by_scope: dict[str, list[dict[str, Any]]] = {
        SCOPE_USER: [],
        SCOPE_PROJECT: [],
        SCOPE_PLUGIN: [],
    }
    for row in rows:
        scope = str(row.get("scope", SCOPE_USER))
        by_scope.setdefault(scope, []).append(row)

    sort_key = lambda row: (-int(row["uses"]), str(row["skill"]))
    for scope_rows in by_scope.values():
        scope_rows.sort(key=sort_key)

    candidates = sorted([row for row in rows if row["candidate"]], key=sort_key)

    header_row = (
        f"  {'Skill':<{name_width}}  {'Uses':>4}  {'Share':>6}  "
        f"{'Bar':<16}  Last used"
    )

    print(f"  Local Skill Usage - Last {since_days} days")
    print("  " + rule * width)

    # One section per scope, always shown so the user can see what each scope
    # contains (or that it's empty). Section title + sub-rule + header + rows.
    for scope_code, title, location in SECTION_TITLES:
        scope_rows = by_scope.get(scope_code, [])
        print()
        print(f"  {title} ({location})")
        print("  " + rule * width)
        if not scope_rows:
            print("  (none)")
            continue
        print(header_row)
        for row in scope_rows:
            print(f"  {_format_skill_row(row, max_uses, now, glyphs, name_width)}")

    # Failure Skills section: removable + low-use across all scopes,
    # with [N] selectors that the agent feeds back to `remove`.
    if candidates:
        print()
        print("  Failure Skills (removable, uses <= 1) - pick numbers to remove")
        print("  " + rule * width)
        print(
            f"  {'Sel':<4} {'Skill':<{cand_name_width}}  {'Uses':>4}  "
            f"{'Share':>6}  {'Bar':<16}  Last used"
        )
        for index, row in enumerate(candidates, start=1):
            prefix = f"[{index}]"
            scope = str(row.get("scope", SCOPE_USER))
            scope_tag = f"  ({scope})"
            line = _format_skill_row(row, max_uses, now, glyphs, cand_name_width)
            print(f"  {prefix:<4} {line}{scope_tag}")

    print()
    print("  " + rule * width)
    summary_parts = [
        f"Total {len(rows)}",
        f"Used {used_count}",
        f"Global {len(by_scope.get(SCOPE_USER, []))}",
        f"Project {len(by_scope.get(SCOPE_PROJECT, []))}",
        f"Plugin {len(by_scope.get(SCOPE_PLUGIN, []))}",
        f"Failure {len(candidates)}",
    ]
    print("  " + " | ".join(summary_parts))
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
    if "/" in skill:
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
    skill: str, confirm: bool, home: Path | None = None, project_root: Path | None = None,
    expected_paths: list[Path] | None = None,
) -> list[Path]:
    paths = find_removable_skill_paths(skill, home, project_root)
    if expected_paths is not None and sorted(paths) != sorted(expected_paths):
        raise ValueError(
            f"path mismatch: expected {expected_paths}, found {paths}"
        )
    if len(paths) > 1:
        raise ValueError(
            f"multiple removable paths match {skill!r}; remove one path manually: {paths}"
        )
    if confirm:
        for path in paths:
            shutil.rmtree(path)
    return paths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ufailure_once.py")
    subparsers = parser.add_subparsers(dest="command", required=True)

    stats = subparsers.add_parser("stats")
    stats.add_argument("--since", default="90d", type=parse_days)
    stats.add_argument("--json", action="store_true")
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
        since_days = args.since
        discovered = discover_skills()
        known = {s.name for s in discovered}
        paths_by_skill: dict[str, list[Path]] = {}
        for s in discovered:
            paths_by_skill.setdefault(s.name, []).extend(s.paths)
        skill_scopes: dict[str, str] = {}
        for s in discovered:
            skill_scopes.setdefault(s.name, s.scope)
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
            print_text_report(rows, since_days=since_days, glyphs=glyphs)
        return 0

    if args.command == "remove":
        if args.dry_run == args.confirm:
            parser.error("choose exactly one of --dry-run or --confirm")
        if args.confirm:
            # Dry-run first to capture expected paths for atomicity check
            try:
                expected_paths = remove_skill(args.skill, confirm=False, home=None)
            except ValueError as exc:
                print(f"  ✗ {args.skill}: {exc}", file=sys.stderr)
                return 1
            if not expected_paths:
                print(f"  ✗ {args.skill}: no removable user skill found")
                return 1
            try:
                paths = remove_skill(args.skill, confirm=True, home=None,
                                     expected_paths=expected_paths)
            except ValueError as exc:
                print(f"  ✗ {args.skill}: {exc}", file=sys.stderr)
                return 1
            for path in paths:
                print(f"  ✓ Removed: {path}")
        else:
            try:
                paths = remove_skill(args.skill, confirm=False, home=None)
            except ValueError as exc:
                print(f"  ✗ {args.skill}: {exc}", file=sys.stderr)
                return 1
            if not paths:
                print(f"  ✗ {args.skill}: no removable user skill found")
                return 1
            for path in paths:
                print(f"  · Would remove: {path}")
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
