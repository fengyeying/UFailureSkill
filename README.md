# UFailureSkill

UFailureSkill is a one-shot GuidePrompt for Codex and ClaudeCode. It ranks your local skills by visible historical usage and lets you remove selected low-use user skills without installing a permanent tool.

## GuidePrompt

Copy this prompt into Codex or ClaudeCode:

```text
Open https://github.com/fengyeying/UFailureSkill and run UFailureSkill.
```

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

5. The script's last line already asks `Which Failure Skills should be removed? Reply with numbers (for example 1,2), all, or skip.` Do not ask again. Wait for the user's reply.
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

The script's `stats` text output is the visualization. When piped (the default for agent harnesses) it emits pure ASCII so renderers can't mangle it:

```text
  Local Skill Usage - Last 90 days
  Scope codes: user = ~/.claude/skills/  proj = ./.claude/skills/  plug = plugin (read-only, manage via /plugin).
  --------------------------------------------------------------------------------
  Skill                         Scope  Uses   Share  Bar               Last used
  --------------------------------------------------------------------------------
  superpowers:executing-plans   plug      2   66.7%  ################  Today
  superpowers:writing-plans     plug      1   33.3%  ########          Today
  ... 37 unused plugin skills hidden (use --all to show)
  ------ Failure Skills (removable, uses <= 1) -----------------------------------
  [1]  ask-questi..erspecified  user      0    0.0%  .                 Never used
  [2]  deep-research            user      0    0.0%  .                 Never used
  [3]  frontend-design          user      0    0.0%  .                 Never used
  --------------------------------------------------------------------------------
  Total 42 - Used 2 - Failure Skills 3 - Plugin 39 (read-only)

  Which Failure Skills should be removed? Reply with numbers (for example 1,2), all, or skip.
```

The same report renders with Unicode block-characters for finer-grained bars when stdout is a TTY (or with `--rich`). Force ASCII regardless with `--ascii`.

## Scope Column

Each skill carries a 4-letter scope code so you can see at a glance where it lives and whether you can act on it:

| Code | Origin | Removable? |
|------|--------|------------|
| `user` | `~/.codex/skills/<name>/` or `~/.claude/skills/<name>/` | yes |
| `proj` | `<cwd>/.codex/skills/<name>/` or `<cwd>/.claude/skills/<name>/` | yes |
| `plug` | `~/.claude/plugins/.../<plugin>/.../skills/<name>/`, named `<plugin>:<skill>` | no — manage via `/plugin` |

Plugin skills are listed for awareness (and so their usage is counted correctly when invoked via the `Skill` tool), but they are never deletion candidates and never get an `[N]` selector.

By default the text report hides plugin skills with zero uses to keep things readable; pass `--all` to see the full inventory.

The `Share` column shows each skill's percentage of total visible usage in the scanned window. Bars are normalized against the highest-use skill in the run. `.` (or `·` in rich mode) marks zero uses. Low-use removable skills are grouped under `Failure Skills` with `[N]` selectors for the deletion question.

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
