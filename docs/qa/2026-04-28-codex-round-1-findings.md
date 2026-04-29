# Codex Round 1 — QA Findings

**Date:** 2026-04-28
**Tester:** Claude (Opus 4.7)
**Subject:** `ufailure_once.py` after Codex's first development round
**Test corpus:** 9 unit tests + 12 synthetic scenarios + read-only run against `~/.codex` and `~/.claude` on this machine
**Verdict:** Core flow works. Two correctness bugs need fixing before production. Several UX/edge issues are nice-to-have.

---

## P0 — Crash bugs (fix before shipping)

### F1. Naive ISO timestamps crash the script with `TypeError`

**Trigger:** any JSONL row with a timestamp lacking a timezone offset, e.g. `"timestamp":"2026-04-28T00:00:00"` instead of `...T00:00:00Z`.

**Repro:**
```bash
tmp="$(mktemp -d)" && mkdir -p "$tmp/.claude/skills/foo" "$tmp/.claude/projects/p"
printf '# foo\n' > "$tmp/.claude/skills/foo/SKILL.md"
printf '%s\n' '{"timestamp":"2026-04-28T00:00:00","type":"assistant","message":{"content":[{"type":"tool_use","name":"Skill","input":{"skill":"foo"}}]}}' > "$tmp/.claude/projects/p/s.jsonl"
HOME="$tmp" python3 ufailure_once.py stats --since 90d
```

**Output:**
```
TypeError: can't compare offset-naive and offset-aware datetimes
  at ufailure_once.py:119  →  if ts is not None and ts < cutoff
```

**Why it matters:** ClaudeCode and Codex normally emit `Z`-suffixed timestamps, but third-party tooling, exported sessions, or older formats may not. One bad row hard-fails the entire `stats` run with no recovery — the user sees a Python traceback instead of a report.

**Suggested fix:** in [ufailure_once.py:59](ufailure_once.py#L59) `parse_timestamp`, normalize to UTC when no offset is present:

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

### F2. `SLASH_RE` false-positives on file paths in transcripts

**Trigger:** any user with a skill name that overlaps a Unix path component (`tmp`, `home`, `users`, `var`, `bin`, `etc`, `opt`, `dev`, `usr`) — and assistant text containing such paths.

**Repro:**
```bash
tmp="$(mktemp -d)" && mkdir -p "$tmp/.claude/skills/tmp" "$tmp/.claude/projects/p"
printf '# tmp\n' > "$tmp/.claude/skills/tmp/SKILL.md"
printf '%s\n' '{"timestamp":"2026-04-28T00:00:00Z","payload":{"content":[{"text":"see /tmp/foo.py for details"}]}}' > "$tmp/.claude/projects/p/s.jsonl"
HOME="$tmp" python3 ufailure_once.py stats --since 90d --json
```

**Output:** `tmp` reported as `uses: 1` despite zero real invocations.

**Why it matters:** Inflated counts mask genuine low-use skills, OR (worse) move actively-used-looking skills out of the candidate list when the actual usage came purely from path mentions. Inverts the tool's purpose.

**Suggested fix:** restrict `SLASH_RE` at [ufailure_once.py:18](ufailure_once.py#L18) to message-leading slash commands only — i.e. require start-of-text or only check string nodes whose content starts with `/<word>`. Stronger: drop `SLASH_RE` entirely and rely on `tool_use` (Claude) plus `USING_RE` / `MD_SKILL_RE` (text) — slash commands in real Codex transcripts are rare and don't show up as bare `/<name>` strings anyway (they're embedded in slash-command tool-use payloads).

---

## P1 — Counting accuracy

### F3. `Using <skill> skill` in documentation/quoted text counts as a usage

**Repro:**
```bash
# JSONL row containing: 'Documentation: "Using foo skill is recommended"'
# Result: foo reported as uses=1
```

**Why it matters:** False positive on quoted explanations, README excerpts, or assistant text that mentions a skill without invoking it.

**Suggested fix:** lower priority than F1/F2. Could require the phrase be at sentence start, or require an action verb context. Or accept this as a known limitation and document it.

### F4. Plugin skills create a misleading "everything is unused" report

**Trigger:** users whose primary skills are plugin-installed (`~/.claude/plugins/...`).

**Observation on this machine:**
```
Total 3 · Active 0 · Failure Skills 3
```
All 3 of the user's `~/.claude/skills/` user skills (`ask-questions-if-underspecified`, `deep-research`, `frontend-design`) show 0 uses, while the user actively invoked `superpowers:using-superpowers`, `superpowers:executing-plans`, etc. (plugin skills) during this very session — those are correctly excluded per `~/.claude/plugins/` scope rule.

**Why it matters:** README says plugin skills are out of scope, but the practical effect is that the report can show "0 active, all are failures" — pushing users to delete every user skill they have installed. The data is *technically correct* but the framing is dangerous.

**Suggested fix:** add a one-line note above the table when `Active == 0` and plugin-skill traces exist in transcripts, e.g.:

> _Note: only `~/.codex/skills/` and `~/.claude/skills/` are scanned. If most of your skills come from plugins, this report will look empty. Use `/plugin` to manage plugin skills._

---

## P1 — Safety

### F5. Same skill name in both Codex and Claude roots removes BOTH without warning

**Trigger:** user has, e.g., `~/.codex/skills/foo` and `~/.claude/skills/foo` simultaneously.

**Repro:**
```bash
tmp="$(mktemp -d)" && mkdir -p "$tmp/.claude/skills/dup" "$tmp/.codex/skills/dup"
printf '# claude-dup\n' > "$tmp/.claude/skills/dup/SKILL.md"
printf '# codex-dup\n' > "$tmp/.codex/skills/dup/SKILL.md"
HOME="$tmp" python3 ufailure_once.py remove dup --confirm
# Both directories deleted with no warning that two paths shared the name.
```

**Why it matters:** A user picking `[1] dup` from the candidate list gets two skills deleted. The dry-run output does list both paths (good), but the relationship to the candidate is silent — easy to miss in a long run.

**Suggested fix:** in `print_text_report`, if any candidate name has multiple paths, append a marker like `(2 paths)`. Or split same-name skills into two distinct candidate entries with `[1] dup (codex)` / `[2] dup (claude)`.

---

## P2 — UX

### F6. Long skill names silently truncated in candidate column

**Trigger:** skill names longer than 23 chars in the candidate section (28 chars in active section).

**Example:** `superpowers-test-driven-development-extended` displays as `superpowers-test-driven` in the candidate row.

**Suggested fix:** widen the column, or use ellipsis (`super…tended`), or print full name on a continuation line when truncated.

### F7. `parse_days` accepts negative values

**Repro:** `python3 -c "from ufailure_once import parse_days; print(parse_days('-7d'))"` returns `-7`.

A negative cutoff means "future cutoff" — every row is older than the cutoff, every row is filtered out. `--since -1d` then returns 0 uses for everything.

**Suggested fix:** raise `ValueError` if result is negative.

### F8. `parse_days` formatting quirks

- `90D` (uppercase) raises raw `ValueError` — should accept or reject with friendlier message.
- Empty string raises `ValueError`.
- `--since -1d` fails argparse (`-` looks like a flag).

Low priority; the documented `--since 90d` form works.

---

## P3 — Notes (not bugs, worth tracking)

- **`--since 0d`** returns 0 uses for everything because `cutoff == now` and `ts < cutoff` is strict. Unintuitive but not wrong.
- **README hardcodes** `https://github.com/fengyeying/UFailureSkill`. Repo must exist (or the GuidePrompt sends users to a 404) before publishing.
- **CJK divider width:** `────── Failure Skills (uses <= 1) ──────` is shorter than the 76-char outer rule because of CJK math (well, the script uses ASCII now so this is fine after Codex's English rewrite). The outer rule is 76 chars; the divider has fewer total chars but ASCII so visually consistent.

---

## Verified working

- `discover_user_skills`: skips `.system`, dot-prefix, plugin paths (`~/.claude/plugins/`).
- `find_removable_skill_paths`: rejects `.hidden`, `../traversal`, missing skills.
- `remove --dry-run` does not delete; `--confirm` does.
- Malformed JSONL lines are silently skipped (no crash).
- Empty home reports cleanly.
- JSON output schema: `{skill, uses, percent, last_used, candidate}` — agent-mappable for index→name lookup.
- `tool_use` parsing catches ClaudeCode `Skill` invocations.
- `### Available skills` blocks are skipped at the leaf-string level (the row containing one isn't entirely dropped).
- 9 unit tests pass.

---

## Recommended order to fix

1. **F1** (timestamp crash) — one-line fix in `parse_timestamp`, prevents hard failure.
2. **F2** (slash false-positive) — drop or constrain `SLASH_RE`, prevents inverted candidate ranking.
3. **F4** (empty-report framing) — small UX text, prevents user from mass-deleting user skills they want to keep.
4. **F5** (dual-root removal warning) — small print change, prevents surprise double-delete.
5. F3, F6, F7, F8 — polish.
