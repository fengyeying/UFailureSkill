# UFailureSkill Codex Round 1 Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the six prioritized issues in [docs/qa/2026-04-28-codex-round-1-findings.md](../../qa/2026-04-28-codex-round-1-findings.md) — two crash/correctness bugs (F1 naive-timestamp `TypeError`, F2 slash false-positives), one safety annotation (F5 dual-root warning), one framing fix (F4 plugin-skill hint), and two polish items (F6 long-name ellipsis, F7 negative-days rejection).

**Architecture:** Edits are confined to `ufailure_once.py` and `tests/test_ufailure_once.py`. Each fix gets a failing test first (TDD), then the minimal implementation change, then a green test run, then a commit. No new files. F3 (documentation-quote false positive) and F8 (`90D`/`-1d` argparse polish) are intentionally out of scope — F3 is a known limitation, F8 is cosmetic.

**Tech Stack:** Python 3 standard library, pytest via `uv run --with pytest`.

---

## File Structure

- Modify: `ufailure_once.py`
  - `parse_timestamp` (Task 1): tz-normalize.
  - `SLASH_RE` constant + `extract_text_skills` (Task 2): drop bare slash matching, add a leading-slash extractor that only fires at the start of a string node.
  - `discover_user_skills` keeps returning paths-per-name; `build_report_rows` (Task 3): accept the path map and emit a `paths` count field.
  - `print_text_report` (Tasks 3, 4, 5): show `(×N paths)` annotation, plugin hint, ellipsis-truncate long names.
  - `parse_days` (Task 6): reject non-positive values.
- Modify: `tests/test_ufailure_once.py`
  - One new failing test per task, kept next to its sibling tests.

---

### Task 1: Fix F1 — Normalize naive ISO timestamps

**Files:**
- Modify: `ufailure_once.py`
- Modify: `tests/test_ufailure_once.py`

- [ ] **Step 1: Add failing test for naive timestamp**

Append to `tests/test_ufailure_once.py`:

```python
def test_collect_usage_handles_naive_timestamp(tmp_path):
    write_jsonl(
        tmp_path / ".claude" / "projects" / "p" / "s.jsonl",
        [
            {
                "timestamp": "2026-04-28T00:00:00",
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": "Skill", "input": {"skill": "foo"}},
                    ],
                },
            },
        ],
    )

    result = collect_usage(home=tmp_path, known_skills={"foo"}, since_days=90)

    assert result["foo"].uses == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --with pytest python3 -m pytest tests/test_ufailure_once.py::test_collect_usage_handles_naive_timestamp -q`

Expected: FAIL with `TypeError: can't compare offset-naive and offset-aware datetimes`.

- [ ] **Step 3: Normalize naive datetimes to UTC**

In `ufailure_once.py`, replace the `parse_timestamp` function with:

```python
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
```

- [ ] **Step 4: Run all tests**

Run: `uv run --with pytest python3 -m pytest tests/test_ufailure_once.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add ufailure_once.py tests/test_ufailure_once.py
git commit -m "fix: normalize naive ISO timestamps to UTC (F1)"
```

---

### Task 2: Fix F2 — Stop counting `/<name>` inside file paths

**Files:**
- Modify: `ufailure_once.py`
- Modify: `tests/test_ufailure_once.py`

**Approach:** the four scanned regexes treat any string node as raw text and match `/<name>` anywhere — file paths and URL components leak in. The replacement matches a slash-prefixed skill name only when it is the **first non-whitespace token of a string node**, which is what an actual `/skill` slash command looks like in transcript user messages. Subsequent slashes in the same node (e.g. `/tmp/foo.py`) are ignored.

- [ ] **Step 1: Add failing test for slash false-positive**

Append to `tests/test_ufailure_once.py`:

```python
def test_collect_usage_ignores_slash_inside_file_paths(tmp_path):
    write_jsonl(
        tmp_path / ".claude" / "projects" / "p" / "s.jsonl",
        [
            {
                "timestamp": "2026-04-28T00:00:00Z",
                "payload": {"content": [{"text": "see /tmp/foo.py for details"}]},
            },
        ],
    )

    result = collect_usage(home=tmp_path, known_skills={"tmp"}, since_days=90)

    assert result["tmp"].uses == 0


def test_collect_usage_still_counts_leading_slash_command(tmp_path):
    write_jsonl(
        tmp_path / ".claude" / "projects" / "p" / "s.jsonl",
        [
            {
                "timestamp": "2026-04-28T00:00:00Z",
                "payload": {"content": [{"type": "input_text", "text": "/writer draft this"}]},
            },
        ],
    )

    result = collect_usage(home=tmp_path, known_skills={"writer"}, since_days=90)

    assert result["writer"].uses == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --with pytest python3 -m pytest tests/test_ufailure_once.py -q -k "slash"`

Expected: `test_collect_usage_ignores_slash_inside_file_paths` FAILS (currently counts the path); `test_collect_usage_still_counts_leading_slash_command` may pass coincidentally — the failure of the first is enough.

- [ ] **Step 3: Replace `SLASH_RE` with a leading-slash matcher**

In `ufailure_once.py`, replace this line:

```python
SLASH_RE = re.compile(r"(?<!\w)/([A-Za-z0-9_.:-]+)\b")
```

with:

```python
LEADING_SLASH_RE = re.compile(r"\A\s*/([A-Za-z0-9_.:-]+)\b")
```

Then in `extract_text_skills`, replace the regex tuple:

```python
        for regex in (USING_RE, CN_USING_RE, MD_SKILL_RE, SLASH_RE):
```

with:

```python
        for regex in (USING_RE, CN_USING_RE, MD_SKILL_RE, LEADING_SLASH_RE):
```

The `\A` anchor forces the match at the start of each string node (each leaf yielded by `iter_json_nodes`), so `/tmp/foo.py` embedded mid-text no longer matches but a user-message text starting with `/writer ...` still does.

- [ ] **Step 4: Run all tests**

Run: `uv run --with pytest python3 -m pytest tests/test_ufailure_once.py -q`

Expected: all tests pass, including the existing `test_collect_usage_counts_visible_skill_invocations` (which has `"/writer draft this"` as a full string node).

- [ ] **Step 5: Commit**

```bash
git add ufailure_once.py tests/test_ufailure_once.py
git commit -m "fix: only count leading-slash skill commands, not embedded paths (F2)"
```

---

### Task 3: Fix F5 — Annotate dual-root candidates

**Files:**
- Modify: `ufailure_once.py`
- Modify: `tests/test_ufailure_once.py`

**Approach:** `discover_user_skills` already returns `dict[str, list[Path]]` with one entry per discovered root. Plumb the path-count through `build_report_rows` and surface `(×2 paths)` next to the skill name in `print_text_report` so users can see before they commit.

- [ ] **Step 1: Add failing test for `paths` field on report rows**

Append to `tests/test_ufailure_once.py`:

```python
def test_build_report_rows_includes_paths_count(tmp_path):
    usage = {
        "dup": Usage("dup", uses=0, last_used=None),
    }
    paths_by_skill = {
        "dup": [tmp_path / ".codex" / "skills" / "dup", tmp_path / ".claude" / "skills" / "dup"],
    }

    rows = build_report_rows(usage, candidate_threshold=1, paths_by_skill=paths_by_skill)

    assert rows[0]["paths"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --with pytest python3 -m pytest tests/test_ufailure_once.py::test_build_report_rows_includes_paths_count -q`

Expected: FAIL with `TypeError: build_report_rows() got an unexpected keyword argument 'paths_by_skill'`.

- [ ] **Step 3: Extend `build_report_rows`**

In `ufailure_once.py`, replace `build_report_rows` with:

```python
def build_report_rows(
    usage: dict[str, Usage],
    candidate_threshold: int,
    paths_by_skill: dict[str, list[Path]] | None = None,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    total_uses = sum(entry.uses for entry in usage.values())
    paths_by_skill = paths_by_skill or {}
    for entry in usage.values():
        percent = (entry.uses / total_uses * 100) if total_uses else 0.0
        rows.append(
            {
                "skill": entry.skill,
                "uses": entry.uses,
                "percent": round(percent, 1),
                "last_used": entry.last_used.isoformat() if entry.last_used else None,
                "candidate": entry.uses <= candidate_threshold,
                "paths": len(paths_by_skill.get(entry.skill, [])),
            }
        )
    return sorted(rows, key=lambda row: (-int(row["uses"]), str(row["skill"])))
```

- [ ] **Step 4: Surface the count in the candidate column**

In `print_text_report`, locate the candidate-row print:

```python
            print(f"  {prefix:<4} {name:23}  {int(row['uses']):>4}  {float(row['percent']):>5.1f}%  {bar}  {last}")
```

Replace with:

```python
            paths_count = int(row.get("paths", 0))
            suffix = f"  ⚠ {paths_count} paths" if paths_count > 1 else ""
            print(f"  {prefix:<4} {name:23}  {int(row['uses']):>4}  {float(row['percent']):>5.1f}%  {bar}  {last}{suffix}")
```

- [ ] **Step 5: Pass the path map from `main`**

In `ufailure_once.py`, replace this block in `main`:

```python
    if args.command == "stats":
        since_days = parse_days(args.since)
        skills = discover_user_skills()
        usage = collect_usage(home=None, known_skills=set(skills), since_days=since_days)
        rows = build_report_rows(usage, candidate_threshold=1)
```

with:

```python
    if args.command == "stats":
        since_days = parse_days(args.since)
        skills = discover_user_skills()
        usage = collect_usage(home=None, known_skills=set(skills), since_days=since_days)
        rows = build_report_rows(usage, candidate_threshold=1, paths_by_skill=skills)
```

- [ ] **Step 6: Run all tests**

Run: `uv run --with pytest python3 -m pytest tests/test_ufailure_once.py -q`

Expected: all tests pass. The previously written `test_build_report_rows_sorts_and_marks_low_use_candidates` still passes because `paths_by_skill` defaults to `None`.

- [ ] **Step 7: Commit**

```bash
git add ufailure_once.py tests/test_ufailure_once.py
git commit -m "fix: warn when a candidate has multiple skill-root paths (F5)"
```

---

### Task 4: Fix F4 — Hint when nothing is active

**Files:**
- Modify: `ufailure_once.py`
- Modify: `tests/test_ufailure_once.py`

**Approach:** when the visible report shows zero active skills, print a one-line hint right after the header so users know they may have plugin skills outside the script's scope. Keep it ASCII so the warning travels through the agent's verbatim passthrough.

- [ ] **Step 1: Add failing test for the hint**

Append to `tests/test_ufailure_once.py`:

```python
import io
from contextlib import redirect_stdout

from ufailure_once import print_text_report


def test_print_text_report_shows_plugin_hint_when_zero_active():
    rows = [
        {"skill": "lonely", "uses": 0, "percent": 0.0, "last_used": None, "candidate": True, "paths": 1},
    ]
    buf = io.StringIO()
    with redirect_stdout(buf):
        print_text_report(rows, since_days=90)
    out = buf.getvalue()

    assert "plugin" in out.lower()
    assert "/plugin" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --with pytest python3 -m pytest tests/test_ufailure_once.py::test_print_text_report_shows_plugin_hint_when_zero_active -q`

Expected: FAIL — current output has no "plugin" mention.

- [ ] **Step 3: Emit the hint when active count is 0**

In `print_text_report`, find this block:

```python
    print(f"  Local Skill Usage · Last {since_days} days")
    print("  " + RULE * 76)
```

Replace with:

```python
    print(f"  Local Skill Usage · Last {since_days} days")
    if not actives and candidates:
        print("  Note: only ~/.codex/skills/ and ~/.claude/skills/ are scanned.")
        print("        Plugin-installed skills (~/.claude/plugins/) are excluded; manage those with /plugin.")
    print("  " + RULE * 76)
```

The `actives` and `candidates` lists must already be computed before the header — move their computation up if needed:

```python
    if not rows:
        print("No removable user skills found (scanned only ~/.codex/skills/ and ~/.claude/skills/).")
        return
    now = datetime.now(timezone.utc)
    max_uses = max((int(row["uses"]) for row in rows), default=0)
    actives = [row for row in rows if not row["candidate"]]
    candidates = [row for row in rows if row["candidate"]]

    print(f"  Local Skill Usage · Last {since_days} days")
    if not actives and candidates:
        print("  Note: only ~/.codex/skills/ and ~/.claude/skills/ are scanned.")
        print("        Plugin-installed skills (~/.claude/plugins/) are excluded; manage those with /plugin.")
    print("  " + RULE * 76)
```

- [ ] **Step 4: Run all tests**

Run: `uv run --with pytest python3 -m pytest tests/test_ufailure_once.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add ufailure_once.py tests/test_ufailure_once.py
git commit -m "fix: hint about plugin scope when zero active skills (F4)"
```

---

### Task 5: Fix F6 — Ellipsis-truncate long skill names

**Files:**
- Modify: `ufailure_once.py`
- Modify: `tests/test_ufailure_once.py`

**Approach:** instead of silently dropping characters past 28/23, render `head…tail` with a single `…` so users can recognize the full identity at a glance. The bar/percent/last-used columns stay aligned because total visible width is unchanged.

- [ ] **Step 1: Add failing test for ellipsis**

Append to `tests/test_ufailure_once.py`:

```python
from ufailure_once import truncate_name


def test_truncate_name_uses_ellipsis_for_long_names():
    assert truncate_name("short", 10) == "short"
    assert truncate_name("superpowers-test-driven-development-extended", 23) == "superpowers-te…extended"
    assert truncate_name("a" * 24, 23).endswith("a") and "…" in truncate_name("a" * 24, 23)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --with pytest python3 -m pytest tests/test_ufailure_once.py::test_truncate_name_uses_ellipsis_for_long_names -q`

Expected: FAIL with `ImportError: cannot import name 'truncate_name'`.

- [ ] **Step 3: Add the helper and use it**

In `ufailure_once.py`, add this helper near the other render helpers (e.g. just below `render_relative`):

```python
def truncate_name(name: str, width: int) -> str:
    if len(name) <= width:
        return name
    if width <= 1:
        return name[:width]
    head = (width - 1) // 2
    tail = width - 1 - head
    return f"{name[:head]}…{name[-tail:]}"
```

Then in `print_text_report`, replace these two lines:

```python
        name = str(row["skill"])[:28]
```

with:

```python
        name = truncate_name(str(row["skill"]), 28)
```

and:

```python
            name = str(row["skill"])[:23]
```

with:

```python
            name = truncate_name(str(row["skill"]), 23)
```

- [ ] **Step 4: Run all tests**

Run: `uv run --with pytest python3 -m pytest tests/test_ufailure_once.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add ufailure_once.py tests/test_ufailure_once.py
git commit -m "fix: ellipsis-truncate long skill names in report (F6)"
```

---

### Task 6: Fix F7 — Reject non-positive `--since` values

**Files:**
- Modify: `ufailure_once.py`
- Modify: `tests/test_ufailure_once.py`

- [ ] **Step 1: Add failing test**

Append to `tests/test_ufailure_once.py`:

```python
import pytest

from ufailure_once import parse_days


def test_parse_days_rejects_non_positive():
    with pytest.raises(ValueError):
        parse_days("0d")
    with pytest.raises(ValueError):
        parse_days("-7d")
    with pytest.raises(ValueError):
        parse_days("0")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --with pytest python3 -m pytest tests/test_ufailure_once.py::test_parse_days_rejects_non_positive -q`

Expected: FAIL — current `parse_days` returns `0` and `-7` happily.

- [ ] **Step 3: Validate in `parse_days`**

In `ufailure_once.py`, replace `parse_days` with:

```python
def parse_days(value: str) -> int:
    raw = value[:-1] if value.endswith("d") else value
    days = int(raw)
    if days <= 0:
        raise ValueError(f"--since must be a positive number of days, got {value!r}")
    return days
```

- [ ] **Step 4: Run all tests**

Run: `uv run --with pytest python3 -m pytest tests/test_ufailure_once.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add ufailure_once.py tests/test_ufailure_once.py
git commit -m "fix: reject zero/negative --since values (F7)"
```

---

### Task 7: End-to-End Verification

**Files:**
- Modify only if verification exposes a defect: `ufailure_once.py`, `tests/test_ufailure_once.py`

- [ ] **Step 1: Run full test suite**

Run: `uv run --with pytest python3 -m pytest tests/test_ufailure_once.py -q`

Expected: all tests pass.

- [ ] **Step 2: Verify F1 — naive timestamp no longer crashes (real path through the CLI)**

```bash
tmp="$(mktemp -d)" && mkdir -p "$tmp/.claude/skills/foo" "$tmp/.claude/projects/p"
printf '# foo\n' > "$tmp/.claude/skills/foo/SKILL.md"
printf '%s\n' '{"timestamp":"2026-04-28T00:00:00","type":"assistant","message":{"content":[{"type":"tool_use","name":"Skill","input":{"skill":"foo"}}]}}' > "$tmp/.claude/projects/p/s.jsonl"
HOME="$tmp" python3 ufailure_once.py stats --since 90d --json
rm -rf "$tmp"
```

Expected: JSON output shows `foo` with `uses: 1`, no `TypeError`.

- [ ] **Step 3: Verify F2 — embedded `/tmp/...` path no longer counts**

```bash
tmp="$(mktemp -d)" && mkdir -p "$tmp/.claude/skills/tmp" "$tmp/.claude/projects/p"
printf '# tmp\n' > "$tmp/.claude/skills/tmp/SKILL.md"
printf '%s\n' '{"timestamp":"2026-04-28T00:00:00Z","payload":{"content":[{"text":"see /tmp/foo.py for details"}]}}' > "$tmp/.claude/projects/p/s.jsonl"
HOME="$tmp" python3 ufailure_once.py stats --since 90d --json
rm -rf "$tmp"
```

Expected: `tmp` shows `uses: 0`.

- [ ] **Step 4: Verify F5 — dual-root annotation**

```bash
tmp="$(mktemp -d)" && mkdir -p "$tmp/.claude/skills/dup" "$tmp/.codex/skills/dup"
printf '# c\n' > "$tmp/.claude/skills/dup/SKILL.md"
printf '# x\n' > "$tmp/.codex/skills/dup/SKILL.md"
HOME="$tmp" python3 ufailure_once.py stats --since 90d
rm -rf "$tmp"
```

Expected: candidate row for `dup` includes `⚠ 2 paths` suffix.

- [ ] **Step 5: Verify F4 — plugin hint appears**

```bash
tmp="$(mktemp -d)" && mkdir -p "$tmp/.claude/skills/lonely"
printf '# lonely\n' > "$tmp/.claude/skills/lonely/SKILL.md"
HOME="$tmp" python3 ufailure_once.py stats --since 90d
rm -rf "$tmp"
```

Expected: header line is followed by a `Note: only ~/.codex/skills/ ...` hint mentioning `/plugin`.

- [ ] **Step 6: Verify F6 — long-name ellipsis**

```bash
tmp="$(mktemp -d)" && mkdir -p "$tmp/.claude/skills/superpowers-test-driven-development-extended"
printf '# long\n' > "$tmp/.claude/skills/superpowers-test-driven-development-extended/SKILL.md"
HOME="$tmp" python3 ufailure_once.py stats --since 90d
rm -rf "$tmp"
```

Expected: candidate row shows the name with `…` in the middle (e.g. `superpowers-te…extended`), not a hard cut.

- [ ] **Step 7: Verify F7 — non-positive `--since` rejected**

```bash
python3 ufailure_once.py stats --since 0d 2>&1 | tail -3
python3 ufailure_once.py stats --since 7 --json >/dev/null && echo "positive int still works"
```

Expected: first command exits non-zero with a `ValueError` message; second prints `positive int still works`.

- [ ] **Step 8: Commit fixes if any verification step exposed a defect**

```bash
git status --short
```

Skip this step if `git status --short` is empty.

---

## Self-Review

- **Spec coverage:** F1 (Task 1), F2 (Task 2), F5 (Task 3), F4 (Task 4), F6 (Task 5), F7 (Task 6). F3 and F8 are explicitly out of scope per the goal statement.
- **Placeholder scan:** every code change ships full code; tests show full asserts; commands show expected output.
- **Type consistency:** `build_report_rows` keeps the existing `dict[str, list[Path]]` argument type for `paths_by_skill`, matching `discover_user_skills`'s return type. `truncate_name` is `(str, int) -> str`. `parse_timestamp` keeps its return signature.
