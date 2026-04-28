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
3. Run the user-facing report and show its **stdout verbatim inside a fenced code block**. Do not paraphrase, re-render as a Markdown table, or strip the bar/divider characters; the script owns the visual:

```bash
python3 /tmp/ufailure_once.py stats --since 90d
```

4. Run the JSON variant once silently to build an `index -> skill-name` map for the user's reply:

```bash
python3 /tmp/ufailure_once.py stats --since 90d --json
```

5. The script's last line already asks `要移除哪些？回复编号(如 1,2 或 all),或 skip 跳过。` Do not ask again. Wait for the user's reply.
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
  ────── 低使用率候选(uses ≤ 1) ──────────────────────────────
  [1]  writer                      1  ▏                 61 天前
  [2]  unused                      0  ·                 从未使用
  ──────────────────────────────────────────────────────────────────
  共 6 个 · 活跃 4 · 候选 2

  要移除哪些？回复编号(如 1,2 或 all),或 skip 跳过。
```

Bars are normalized against the highest-use skill in the run with 1/8-block resolution. `·` marks zero uses. Last-used dates render as `今天`, `昨天`, `N 天前`, or `从未使用` so users do not have to do date math.

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

Usage is counted from two signals: structured `tool_use` invocations of the Skill tool in ClaudeCode transcripts, and visible textual mentions in either harness (`Using <skill> skill`, `/<skill>` slash commands, Markdown links to a skill's `SKILL.md`). Skill-listing blocks such as `### Available skills` and `The following skills are available` are skipped at the leaf-string level so they do not inflate counts.

## What It Can Remove

Only explicitly selected user skills under:

- `~/.codex/skills/<name>`
- `~/.claude/skills/<name>`

It does not remove system skills, plugin skills, project-local skills, or arbitrary paths.

## Plugin Skills Are Out Of Scope

Skills installed via Claude Code plugins live under `~/.claude/plugins/` and appear with namespaced names like `superpowers:brainstorming` or `anthropic-skills:docx`. UFailureSkill neither lists nor removes these; manage them with the `/plugin` command instead. Plugin-installed skills will not show up in the candidate list even if they are heavily used or unused, because the script only ranks skills it can also safely remove.

## Traces

UFailureSkill does not install itself or write persistent configuration. The expected traces are limited to the current Codex/ClaudeCode conversation, temporary `/tmp/ufailure_once.py` while it runs, and any skills the user explicitly chooses to remove.
