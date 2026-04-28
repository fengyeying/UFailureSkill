# UFailureSkill One-Shot Guide Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a lightweight GitHub-hosted GuidePrompt plus one-shot Python script that lets Codex or ClaudeCode rank local skills by historical usage, ask the user once which low-use skills to remove, and safely remove only explicitly selected user skills.

**Architecture:** The repository ships a single dependency-free script, `ufailure_once.py`, a short user-facing GuidePrompt, and an agent-facing README procedure. The copied prompt only asks Codex/ClaudeCode to open the GitHub project and follow the one-shot procedure; the README procedure handles downloading the script to `/tmp`, running stats, asking one deletion question, and cleaning up the temporary file. The script is read-only for stats and only mutates local skill directories when run with `remove --confirm`; it does not install packages, write config, create persistent databases, or install itself as a skill.

**Tech Stack:** Python 3 standard library, JSONL transcript parsing, pathlib/shutil for safe filesystem operations, pytest for local tests, Markdown README for the copy-paste GuidePrompt.

---

## File Structure

- Create: `ufailure_once.py`
  - Single-file CLI intended to be fetched to `/tmp` and deleted after use.
  - Commands: `stats`, `remove`.
  - Responsibilities: discover local skills, parse Codex/ClaudeCode JSONL logs, compute usage, produce JSON/text output, dry-run or confirm removal.
- Create: `tests/test_ufailure_once.py`
  - Unit tests for skill discovery, log parsing, candidate selection, safe removal guards, and CLI-level behavior using temporary directories.
- Create: `README.md`
  - Project homepage content.
  - Contains one copy-paste GuidePrompt for Codex/ClaudeCode users.
  - Explains privacy, minimal traces, safety boundaries, and the single user decision point.
- Create: `.gitignore`
  - Ignore Python caches and test artifacts.

## Behavioral Contract

The shipped homepage must contain:

1. A short user-facing GuidePrompt that only tells Codex/ClaudeCode to open the GitHub project and run UFailureSkill.
2. A separate agent-facing "One-Shot Procedure" section in the README that Codex/ClaudeCode can follow after reading the GitHub project page.

The agent-facing procedure must tell Codex/ClaudeCode to:

1. Resolve the repository raw URL for `ufailure_once.py` from the GitHub project page.
2. Download `ufailure_once.py` into `/tmp/ufailure_once.py`.
3. Run `python3 /tmp/ufailure_once.py stats --since 90d --json`.
4. Display skills sorted by use count descending.
5. Display low-use candidates with stable numeric indexes.
6. Ask exactly one deletion question: `要移除哪些？回复编号、all 或 skip。`
7. For selected skills, run `python3 /tmp/ufailure_once.py remove <skill-name> --dry-run`.
8. Only if dry-run reports safe user-skill paths, run `python3 /tmp/ufailure_once.py remove <skill-name> --confirm`.
9. Delete `/tmp/ufailure_once.py`.
10. Report removed and skipped skills.

The script must:

- Read Codex logs from `~/.codex/sessions/**/*.jsonl` and `~/.codex/archived_sessions/*.jsonl`.
- Read ClaudeCode logs from `~/.claude/projects/**/*.jsonl`.
- Count usage from two signals: structured `tool_use` nodes invoking the Skill tool (ClaudeCode's primary skill activation path) and textual mentions matching `Using <skill> skill`, `/<skill>`, or Markdown links to a skill's `SKILL.md`.
- Skip `### Available skills` / `Available skills` / `The following skills are available` blocks at the JSON-leaf level, not by dropping the entire JSONL row.
- Use the log file's mtime as a fallback timestamp when a row has none, so the `--since` window stays correct for old archived sessions.
- Discover removable user skills only from `~/.codex/skills/<name>` and `~/.claude/skills/<name>`.
- Never remove `.system`, plugin cache, project-local skills, or paths outside the two allowed user skill roots.
- Ignore plugin-installed skills under `~/.claude/plugins/` entirely (neither discovered nor removable); the README must tell users to manage those with `/plugin`.
- Treat usage as visible transcript evidence, not guaranteed internal activation.
- Avoid writing persistent state.

---

### Task 1: Add Project Skeleton

**Files:**
- Create: `.gitignore`
- Create: `README.md`
- Create: `ufailure_once.py`
- Create: `tests/test_ufailure_once.py`

- [ ] **Step 1: Create `.gitignore`**

```gitignore
__pycache__/
.pytest_cache/
*.pyc
.coverage
htmlcov/
dist/
build/
*.egg-info/
```

- [ ] **Step 2: Create placeholder module with executable entrypoint**

Create `ufailure_once.py`:

```python
#!/usr/bin/env python3
"""One-shot local skill usage reporter and safe remover for Codex/ClaudeCode."""

from __future__ import annotations

import argparse


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
```

- [ ] **Step 3: Create initial smoke test**

Create `tests/test_ufailure_once.py`:

```python
from ufailure_once import main


def test_stats_command_exists():
    assert main(["stats", "--since", "90d"]) == 0
```

- [ ] **Step 4: Run smoke test**

Run: `python3 -m pytest tests/test_ufailure_once.py -q`

Expected: `1 passed`

- [ ] **Step 5: Commit**

```bash
git add .gitignore README.md ufailure_once.py tests/test_ufailure_once.py
git commit -m "chore: add one-shot script skeleton"
```

---

### Task 2: Implement Skill Discovery

**Files:**
- Modify: `ufailure_once.py`
- Modify: `tests/test_ufailure_once.py`

- [ ] **Step 1: Add failing tests for user skill discovery and system-skill exclusion**

Append to `tests/test_ufailure_once.py`:

```python
from pathlib import Path

from ufailure_once import discover_user_skills


def test_discovers_only_user_skills(tmp_path):
    codex_root = tmp_path / ".codex" / "skills"
    claude_root = tmp_path / ".claude" / "skills"
    (codex_root / "writer").mkdir(parents=True)
    (codex_root / "writer" / "SKILL.md").write_text("# Writer\n", encoding="utf-8")
    (codex_root / ".system" / "builtin").mkdir(parents=True)
    (codex_root / ".system" / "builtin" / "SKILL.md").write_text("# Builtin\n", encoding="utf-8")
    (claude_root / "reviewer").mkdir(parents=True)
    (claude_root / "reviewer" / "SKILL.md").write_text("# Reviewer\n", encoding="utf-8")

    skills = discover_user_skills(home=tmp_path)

    assert sorted(skills) == ["reviewer", "writer"]
    assert skills["writer"] == [codex_root / "writer"]
    assert skills["reviewer"] == [claude_root / "reviewer"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_ufailure_once.py::test_discovers_only_user_skills -q`

Expected: FAIL with `ImportError` or missing `discover_user_skills`.

- [ ] **Step 3: Implement discovery**

Add to `ufailure_once.py`:

```python
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
```

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/test_ufailure_once.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add ufailure_once.py tests/test_ufailure_once.py
git commit -m "feat: discover removable user skills"
```

---

### Task 3: Implement Log Parsing and Usage Counting

**Files:**
- Modify: `ufailure_once.py`
- Modify: `tests/test_ufailure_once.py`

- [ ] **Step 1: Add failing usage parsing tests**

Append to `tests/test_ufailure_once.py`:

```python
import json

from ufailure_once import collect_usage


def write_jsonl(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")


def test_collect_usage_counts_visible_skill_invocations(tmp_path):
    write_jsonl(
        tmp_path / ".codex" / "sessions" / "2026" / "04" / "28" / "rollout.jsonl",
        [
            {
                "timestamp": "2026-04-28T00:00:00Z",
                "type": "response_item",
                "payload": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Using brainstorming skill to shape this."}],
                },
            },
            {
                "timestamp": "2026-04-28T00:01:00Z",
                "type": "response_item",
                "payload": {
                    "role": "user",
                    "content": [{"type": "input_text", "text": "/writer draft this"}],
                },
            },
            {
                "timestamp": "2026-04-28T00:02:00Z",
                "type": "response_item",
                "payload": {
                    "role": "developer",
                    "content": [{"type": "text", "text": "### Available skills\n- writer\n- unused"}],
                },
            },
        ],
    )

    result = collect_usage(home=tmp_path, known_skills={"brainstorming", "writer", "unused"}, since_days=90)

    assert result["brainstorming"].uses == 1
    assert result["writer"].uses == 1
    assert result["unused"].uses == 0


def test_collect_usage_counts_claude_code_tool_use(tmp_path):
    write_jsonl(
        tmp_path / ".claude" / "projects" / "proj" / "session.jsonl",
        [
            {
                "timestamp": "2026-04-28T00:00:00Z",
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": "Skill", "input": {"skill": "brainstorming"}},
                    ],
                },
            },
        ],
    )

    result = collect_usage(home=tmp_path, known_skills={"brainstorming"}, since_days=90)

    assert result["brainstorming"].uses == 1


def test_collect_usage_skips_skill_listing_nodes_only(tmp_path):
    write_jsonl(
        tmp_path / ".codex" / "sessions" / "2026" / "04" / "28" / "rollout.jsonl",
        [
            {
                "timestamp": "2026-04-28T00:00:00Z",
                "type": "response_item",
                "payload": {
                    "content": [
                        {"type": "text", "text": "### Available skills\n- writer\n- unused"},
                        {"type": "input_text", "text": "/writer draft this"},
                    ],
                },
            },
        ],
    )

    result = collect_usage(home=tmp_path, known_skills={"writer", "unused"}, since_days=90)

    assert result["writer"].uses == 1
    assert result["unused"].uses == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_ufailure_once.py -q -k collect_usage`

Expected: FAIL with missing `collect_usage`.

- [ ] **Step 3: Implement parsing types and helpers**

Add to `ufailure_once.py`:

```python
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any, Iterable


USING_RE = re.compile(r"\bUsing\s+([A-Za-z0-9_.:-]+)\s+skill\b", re.IGNORECASE)
CN_USING_RE = re.compile(r"使用(?:了)?\s*([A-Za-z0-9_.:-]+)\s*(?:skill|技能)", re.IGNORECASE)
SLASH_RE = re.compile(r"(?<!\w)/([A-Za-z0-9_.:-]+)\b")
MD_SKILL_RE = re.compile(r"\[\$?([A-Za-z0-9_.:-]+)\]\([^)]*/SKILL\.md\)")


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
```

- [ ] **Step 4: Implement usage collection**

Add to `ufailure_once.py`:

```python
SKILL_LIST_MARKERS = (
    "### Available skills",
    "Available skills",
    "The following skills are available",
    "user-invocable skills",
)
TOOL_USE_SKILL_TOOL_NAMES = {"Skill", "skill", "use_skill"}


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
```

- [ ] **Step 5: Run tests**

Run: `python3 -m pytest tests/test_ufailure_once.py -q`

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add ufailure_once.py tests/test_ufailure_once.py
git commit -m "feat: count skill usage from local transcripts"
```

---

### Task 4: Implement `stats` Output and Low-Use Candidates

**Files:**
- Modify: `ufailure_once.py`
- Modify: `tests/test_ufailure_once.py`

- [ ] **Step 1: Add tests for candidate selection and JSON rows**

Append to `tests/test_ufailure_once.py`:

```python
from datetime import datetime, timezone

from ufailure_once import Usage, build_report_rows


def test_build_report_rows_sorts_and_marks_low_use_candidates():
    usage = {
        "active": Usage("active", uses=5, last_used=datetime(2026, 4, 28, tzinfo=timezone.utc)),
        "rare": Usage("rare", uses=1, last_used=datetime(2026, 4, 1, tzinfo=timezone.utc)),
        "unused": Usage("unused", uses=0, last_used=None),
    }

    rows = build_report_rows(usage, candidate_threshold=1)

    assert [row["skill"] for row in rows] == ["active", "rare", "unused"]
    assert rows[0]["candidate"] is False
    assert rows[1]["candidate"] is True
    assert rows[2]["candidate"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_ufailure_once.py::test_build_report_rows_sorts_and_marks_low_use_candidates -q`

Expected: FAIL with missing `build_report_rows`.

- [ ] **Step 3: Implement report rows and duration parser**

Add to `ufailure_once.py`:

```python
def parse_days(value: str) -> int:
    if value.endswith("d"):
        return int(value[:-1])
    return int(value)


def build_report_rows(usage: dict[str, Usage], candidate_threshold: int) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for entry in usage.values():
        rows.append(
            {
                "skill": entry.skill,
                "uses": entry.uses,
                "last_used": entry.last_used.isoformat() if entry.last_used else None,
                "candidate": entry.uses <= candidate_threshold,
            }
        )
    return sorted(rows, key=lambda row: (-int(row["uses"]), str(row["skill"])))
```

- [ ] **Step 4: Wire `stats` command with character-graphics report**

Replace `main` in `ufailure_once.py` with:

```python
BAR_PARTIALS = " ▏▎▍▌▋▊▉"
BAR_FULL = "█"
BAR_WIDTH = 16
RULE = "─"


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
        return "从未使用"
    try:
        last = datetime.fromisoformat(str(last_used))
    except ValueError:
        return str(last_used)
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    delta = (now - last).days
    if delta <= 0:
        return "今天"
    if delta == 1:
        return "昨天"
    return f"{delta} 天前"


def print_text_report(rows: list[dict[str, object]], since_days: int) -> None:
    if not rows:
        print("未发现可移除的 user skill（仅扫描 ~/.codex/skills/ 与 ~/.claude/skills/）。")
        return
    now = datetime.now(timezone.utc)
    max_uses = max((int(row["uses"]) for row in rows), default=0)
    actives = [row for row in rows if not row["candidate"]]
    candidates = [row for row in rows if row["candidate"]]

    print(f"  本地 Skill 用量 · 近 {since_days} 天")
    print("  " + RULE * 66)
    print(f"  {'Skill':28}  {'Uses':>4}  {'Bar':<16}  Last used")
    print("  " + RULE * 66)
    for row in actives:
        bar = render_bar(int(row["uses"]), max_uses)
        last = render_relative(row["last_used"], now)
        name = str(row["skill"])[:28]
        print(f"  {name:28}  {int(row['uses']):>4}  {bar}  {last}")
    if candidates:
        print("  " + RULE * 6 + " 低使用率候选（uses ≤ 1） " + RULE * 30)
        for index, row in enumerate(candidates, start=1):
            bar = render_bar(int(row["uses"]), max_uses)
            last = render_relative(row["last_used"], now)
            prefix = f"[{index}]"
            name = str(row["skill"])[:23]
            print(f"  {prefix:<4} {name:23}  {int(row['uses']):>4}  {bar}  {last}")
    print("  " + RULE * 66)
    print(f"  共 {len(rows)} 个 · 活跃 {len(actives)} · 候选 {len(candidates)}")
    if candidates:
        print()
        print("  要移除哪些？回复编号（如 1,2 或 all），或 skip 跳过。")


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
        parser.error("remove is not implemented yet")

    return 0
```

- [ ] **Step 5: Run tests**

Run: `python3 -m pytest tests/test_ufailure_once.py -q`

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add ufailure_once.py tests/test_ufailure_once.py
git commit -m "feat: report ranked skill usage"
```

---

### Task 5: Implement Safe Remove

**Files:**
- Modify: `ufailure_once.py`
- Modify: `tests/test_ufailure_once.py`

- [ ] **Step 1: Add failing tests for safe path resolution**

Append to `tests/test_ufailure_once.py`:

```python
from ufailure_once import find_removable_skill_paths


def test_find_removable_skill_paths_only_allows_user_skill_roots(tmp_path):
    codex_root = tmp_path / ".codex" / "skills"
    allowed = codex_root / "rare"
    allowed.mkdir(parents=True)
    (allowed / "SKILL.md").write_text("# Rare\n", encoding="utf-8")

    paths = find_removable_skill_paths("rare", home=tmp_path)

    assert paths == [allowed]


def test_find_removable_skill_paths_rejects_missing_skill(tmp_path):
    assert find_removable_skill_paths("missing", home=tmp_path) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_ufailure_once.py::test_find_removable_skill_paths_only_allows_user_skill_roots -q`

Expected: FAIL with missing `find_removable_skill_paths`.

- [ ] **Step 3: Implement remove helpers**

Add to `ufailure_once.py`:

```python
import shutil


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
```

- [ ] **Step 4: Wire `remove` command**

Replace the `remove` branch in `main` with:

```python
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
```

- [ ] **Step 5: Add CLI remove test**

Append to `tests/test_ufailure_once.py`:

```python
from ufailure_once import remove_skill


def test_remove_skill_deletes_only_when_confirmed(tmp_path):
    target = tmp_path / ".codex" / "skills" / "rare"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("# Rare\n", encoding="utf-8")

    dry_run_paths = remove_skill("rare", confirm=False, home=tmp_path)
    assert dry_run_paths == [target]
    assert target.exists()

    confirmed_paths = remove_skill("rare", confirm=True, home=tmp_path)
    assert confirmed_paths == [target]
    assert not target.exists()
```

- [ ] **Step 6: Run tests**

Run: `python3 -m pytest tests/test_ufailure_once.py -q`

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add ufailure_once.py tests/test_ufailure_once.py
git commit -m "feat: safely remove selected user skills"
```

---

### Task 6: Write GitHub Homepage GuidePrompt

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Replace README with concise product page**

Write `README.md`:

````markdown
# UFailureSkill

UFailureSkill is a one-shot GuidePrompt for Codex and ClaudeCode. It ranks your local skills by visible historical usage and lets you remove selected low-use user skills without installing a permanent tool.

## GuidePrompt

Copy this prompt into Codex or ClaudeCode:

```text
请打开 https://github.com/<OWNER>/UFailureSkill 并运行 UFailureSkill。
```

Before publishing, replace `<OWNER>` with the GitHub owner or organization that hosts this repository.

## One-Shot Procedure

This section is for Codex and ClaudeCode agents after the user pastes the GuidePrompt. Do not expose these operational details unless the user asks. Do not install packages, do not create a persistent skill, and do not write configuration.

1. Resolve the raw URL for `ufailure_once.py` from this repository.
2. Download it to `/tmp/ufailure_once.py`.
3. Run the user-facing report and show its **stdout verbatim inside a fenced code block**. Do not paraphrase, re-render as a Markdown table, or strip the bar/divider characters — the script owns the visual:

```bash
python3 /tmp/ufailure_once.py stats --since 90d
```

4. Run the JSON variant once silently to build an `index → skill-name` map for the user's reply:

```bash
python3 /tmp/ufailure_once.py stats --since 90d --json
```

5. The script's last line already asks `要移除哪些？回复编号（如 1,2 或 all），或 skip 跳过。` — do not ask again. Wait for the user's reply.
6. For each selected skill, run dry-run first:

```bash
python3 /tmp/ufailure_once.py remove <skill-name> --dry-run
```

7. Only if every dry-run path is under `~/.codex/skills/<name>` or `~/.claude/skills/<name>`, run:

```bash
python3 /tmp/ufailure_once.py remove <skill-name> --confirm
```

8. Delete `/tmp/ufailure_once.py`.
9. Report removed skills, skipped skills, and reasons for anything not removed. Show the script's `✓ Removed: <path>` lines verbatim.
10. Keep the user-facing interaction minimal: show the ranked table from the script, wait for the reply, run the dry-run/confirm pair, show the result.

## What It Looks Like

The script's `stats` text output is the visualization. Example:

```text
  本地 Skill 用量 · 近 90 天
  ──────────────────────────────────────────────────────────────────
  Skill                         Uses  Bar               Last used
  ──────────────────────────────────────────────────────────────────
  brainstorming                   23  ████████████████  2 天前
  writing-plans                   12  ████████▌         5 天前
  test-driven-development          8  █████▋            12 天前
  debugging                        3  ██▏               23 天前
  ────── 低使用率候选（uses ≤ 1） ──────────────────────────────────
  [1]  writer                      1  ▏                 61 天前
  [2]  unused                      0  ·                 从未使用
  ──────────────────────────────────────────────────────────────────
  共 6 个 · 活跃 4 · 候选 2

  要移除哪些？回复编号（如 1,2 或 all），或 skip 跳过。
```

Bars are normalized against the highest-use skill in the run (1/8-block resolution). `·` marks zero uses. Last-used dates render as `今天 / 昨天 / N 天前 / 从未使用` so users do not have to do date math.

After the user picks `1,2`, the agent shows the script's removal lines verbatim:

```text
  · Would remove: /Users/<you>/.claude/skills/writer
  ✓ Removed: /Users/<you>/.claude/skills/writer
  · Would remove: /Users/<you>/.codex/skills/unused
  ✓ Removed: /Users/<you>/.codex/skills/unused
```

## What It Reads

- `~/.codex/sessions/**/*.jsonl`
- `~/.codex/archived_sessions/*.jsonl`
- `~/.claude/projects/**/*.jsonl`
- `~/.codex/skills/*/SKILL.md`
- `~/.claude/skills/*/SKILL.md`

Usage is counted from two signals: structured `tool_use` invocations of the Skill tool in ClaudeCode transcripts, and visible textual mentions in either harness (`Using <skill> skill`, `/<skill>` slash commands, Markdown links to a skill's `SKILL.md`). Skill-listing blocks (e.g. `### Available skills`, `The following skills are available`) are skipped at the leaf-string level so they do not inflate counts.

## What It Can Remove

Only explicitly selected user skills under:

- `~/.codex/skills/<name>`
- `~/.claude/skills/<name>`

It does not remove system skills, plugin skills, project-local skills, or arbitrary paths.

## Plugin Skills Are Out of Scope

Skills installed via Claude Code plugins live under `~/.claude/plugins/` and appear with namespaced names like `superpowers:brainstorming` or `anthropic-skills:docx`. UFailureSkill neither lists nor removes these — manage them with the `/plugin` command instead. Plugin-installed skills will not show up in the candidate list even if they are heavily used or unused, because the script only ranks skills it can also safely remove.

## Traces

UFailureSkill does not install itself or write persistent configuration. The expected traces are limited to the current Codex/ClaudeCode conversation, temporary `/tmp/ufailure_once.py` while it runs, and any skills the user explicitly chooses to remove.
````

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add one-shot guide prompt"
```

---

### Task 7: End-to-End Local Verification

**Files:**
- Modify only if verification exposes a defect: `ufailure_once.py`, `tests/test_ufailure_once.py`, or `README.md`

- [ ] **Step 1: Run all unit tests**

Run: `python3 -m pytest -q`

Expected: all tests pass.

- [ ] **Step 2: Run script against a synthetic home directory**

Create a temporary synthetic home and run:

```bash
tmp_home="$(mktemp -d)"
mkdir -p "$tmp_home/.codex/skills/rare" "$tmp_home/.codex/sessions/2026/04/28"
printf '# Rare\n' > "$tmp_home/.codex/skills/rare/SKILL.md"
printf '%s\n' '{"timestamp":"2026-04-28T00:00:00Z","payload":{"content":[{"text":"/rare do work"}]}}' > "$tmp_home/.codex/sessions/2026/04/28/session.jsonl"
HOME="$tmp_home" python3 ufailure_once.py stats --since 90d
```

Expected output includes `rare` with `1` use.

- [ ] **Step 3: Verify dry-run does not delete**

Run:

```bash
HOME="$tmp_home" python3 ufailure_once.py remove rare --dry-run
test -d "$tmp_home/.codex/skills/rare"
```

Expected: command prints `Would remove:` and `test -d` exits 0.

- [ ] **Step 4: Verify confirm deletes**

Run:

```bash
HOME="$tmp_home" python3 ufailure_once.py remove rare --confirm
test ! -d "$tmp_home/.codex/skills/rare"
```

Expected: command prints `Removed:` and `test ! -d` exits 0.

- [ ] **Step 5: Clean synthetic home**

Run:

```bash
rm -rf "$tmp_home"
```

Expected: directory is removed.

- [ ] **Step 6: Commit fixes if any were needed**

```bash
git status --short
git add ufailure_once.py tests/test_ufailure_once.py README.md
git commit -m "fix: harden one-shot verification"
```

Skip the commit if `git status --short` is empty.

---

## Self-Review

- Spec coverage: The plan covers the concise GitHub homepage GuidePrompt, agent-facing one-shot procedure, no-install one-shot script, local log scanning (both structured `tool_use` Skill invocations and textual mentions), file-mtime fallback for the `--since` window, leaf-level skipping of skill-listing blocks, usage ranking, one-question user selection, dry-run before confirm, safe path constraints scoped to `~/.codex/skills/` and `~/.claude/skills/`, explicit out-of-scope statement for `~/.claude/plugins/` skills, temporary cleanup instructions, and minimal traces.
- Placeholder scan: The plan contains no unfinished implementation markers or unspecified implementation tasks. The README intentionally contains `<OWNER>` because the repository owner is not known until publication; the step explicitly instructs replacing it before publishing.
- Type consistency: The plan consistently uses `Usage`, `discover_user_skills`, `iter_json_nodes`, `extract_tool_use_skills`, `extract_text_skills`, `collect_usage`, `build_report_rows`, `render_bar`, `render_relative`, `print_text_report`, `find_removable_skill_paths`, and `remove_skill` with matching signatures across tests and implementation steps.
- Visualization ownership: The script owns the user-facing rendering (block-character bars, `今天/N 天前/从未使用` relative dates, `[N]` candidate selectors, `✓` / `·` glyphs on remove output). The README explicitly tells the agent to pass stdout through verbatim instead of re-rendering, so Codex and ClaudeCode produce identical visuals.
