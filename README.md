# UFailureSkill

> **A no-install, no-trace, one-shot way to find and remove unused local skills in Codex and Claude Code.**

You paste one prompt. Your agent fetches a single Python file to `/tmp`, runs it, asks you one question, deletes the file, and exits. Nothing is installed. No background process is started. No configuration is written. The only files that ever change on your disk are the skill directories you explicitly choose to delete.

---

## The One-Shot Promise

Before running:

- Nothing is installed (no `pip`, no `npm`, no plugin, no skill, no daemon).
- Nothing has been written to your `~/.codex` or `~/.claude` config.

While running:

- One Python file lives in `/tmp/ufailure_once.py`.
- The file reads — never writes — your local Codex / Claude Code session logs and skill directories.
- No network calls except the initial `curl` of the script itself.

After running:

- `/tmp/ufailure_once.py` is deleted by the agent.
- The only persistent disk changes are the skill directories you explicitly approved (each removal is shown twice — once as `Would remove:` in dry-run, once as `✓ Removed:` after your `confirm` — and is bounded to `~/.codex/skills/<name>/`, `~/.claude/skills/<name>/`, or the project-local equivalents).
- No log entries, no telemetry, no cache, no marker file. The expected trace is limited to the conversation in your agent's transcript.

If you change your mind partway through, reply `skip` and the script does nothing destructive.

---

## How To Run

Copy this prompt into Codex or Claude Code:

```text
Open https://github.com/fengyeying/UFailureSkill and run UFailureSkill.
```

That's it. The agent reads the procedure below from this README, runs the report, asks you which low-use skills to remove, and cleans up after itself.

---

## What It Looks Like

The script's `stats` text output is the visualization. Three scope sections (Global / Project / Plugin) are always rendered — empty ones say `(none)` — followed by a separate Failure Skills list with the deletion selectors. ASCII by default when piped (agent-safe); Unicode block-character bars when stdout is a TTY or with `--rich`.

```text
  Local Skill Usage - Last 90 days
  --------------------------------------------------------------------------------

  Global skills (~/.codex/skills/, ~/.claude/skills/ - removable)
  --------------------------------------------------------------------------------
  Skill                             Uses   Share  Bar               Last used
  deep-research                       31   40.3%  ################  Today
  frontend-design                      5    6.5%  ###               54 days ago
  ask-questions-if-underspecified      0    0.0%  .                 Never used

  Project skills (./.codex/skills/, ./.claude/skills/ - removable)
  --------------------------------------------------------------------------------
  (none)

  Plugin skills (~/.claude/plugins/ - read-only, manage via /plugin)
  --------------------------------------------------------------------------------
  Skill                             Uses   Share  Bar               Last used
  superpowers:brainstorming            6    7.8%  ###               Today
  superpowers:executing-plans          5    6.5%  ###               Today
  ... (more plugin rows)

  Failure Skills (removable, uses <= 1) - pick numbers to remove
  --------------------------------------------------------------------------------
  Sel  Skill                        Uses   Share  Bar               Last used
  [1]  ask-questions-if-underspeci     0    0.0%  .                 Never used  (user)

  --------------------------------------------------------------------------------
  Total 42 | Used 31 | Global 3 | Project 0 | Plugin 39 | Failure 1

  Which Failure Skills should be removed? Reply with numbers (for example 1,2), all, or skip.
```

You reply with the numbers you want gone (or `all`, or `skip`). Removal is two-step:

```text
  · Would remove: /Users/<you>/.claude/skills/ask-questions-if-underspecified
  ✓ Removed: /Users/<you>/.claude/skills/ask-questions-if-underspecified
```

---

## Scope Sections

Each skill belongs to exactly one scope. The report lists every skill under its section so you can see the full inventory:

| Section | Origin | Removable? |
|---------|--------|------------|
| **Global skills** | `~/.codex/skills/<name>/` or `~/.claude/skills/<name>/` | yes |
| **Project skills** | `<cwd>/.codex/skills/<name>/` or `<cwd>/.claude/skills/<name>/` | yes |
| **Plugin skills** | `~/.claude/plugins/.../<plugin>/.../skills/<name>/`, named `<plugin>:<skill>` | no — manage via `/plugin` |

Plugin skills are shown in full (used and unused) so their usage counts are visible alongside the global / project skills you might want to clean up. They are never deletion candidates and never get an `[N]` selector. Failure Skills carry a `(scope)` tag so selection stays unambiguous when the same name lives in multiple scopes.

The `Share` column shows each skill's percentage of total visible usage in the scanned window. Bars are normalized against the highest-use skill in the run. `.` (or `·` in rich mode) marks zero uses.

---

## What It Reads (and How Usage Is Counted)

Logs and skill manifests, all read-only:

- `~/.codex/sessions/**/*.jsonl`
- `~/.codex/archived_sessions/*.jsonl`
- `~/.claude/projects/**/*.jsonl`
- `~/.codex/skills/*/SKILL.md`
- `~/.claude/skills/*/SKILL.md`
- `~/.claude/plugins/**/skills/*/SKILL.md`
- `<cwd>/.codex/skills/*/SKILL.md` and `<cwd>/.claude/skills/*/SKILL.md`

Both Codex and Claude Code sessions are scanned. They invoke skills differently, so we count usage from four signals across all transcripts:

1. **Claude Code structured invocations** — `tool_use` nodes with `name: "Skill"` and `input.skill: "<name>"`. Plugin namespacing (e.g. `superpowers:executing-plans`) is preserved so each plugin skill counts against its own row.
2. **Codex SKILL.md path mentions** — Codex Desktop activates skills by `cat`-ing the SKILL.md path through `exec_command`. Any `/skills/<name>/SKILL.md` substring in a transcript is matched and resolved to the right scope (bare name for user/project paths, namespaced `<plugin>:<name>` for paths under `/plugins/`).
3. **Slash commands at the start of a string node** (e.g. `/writer draft this`).
4. **Natural-language mentions** like `Using <skill> skill` and `[<skill>](.../SKILL.md)` Markdown links.

Skill-listing blocks (`### Available skills`, `The following skills are available`) are skipped at the leaf-string level so they don't inflate counts.

---

## What It Can Remove

Only explicitly selected user skills under:

- `~/.codex/skills/<name>/`
- `~/.claude/skills/<name>/`
- `<cwd>/.codex/skills/<name>/` or `<cwd>/.claude/skills/<name>/` (when run from inside a project)

It does not remove system skills, plugin-installed skills, or arbitrary paths. Names containing `:` (the namespaced plugin form) short-circuit removal even if such a directory somehow exists. Every removal is preceded by a dry-run that prints the exact path; only after the dry-run passes does `--confirm` actually call `shutil.rmtree`.

---

## One-Shot Procedure (Agent-Facing)

This section is for Codex and Claude Code agents after the user pastes the prompt above. Do not expose these operational details unless the user asks. Do not install packages, do not create a persistent skill, and do not write configuration.

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

7. Only if every dry-run path is under a removable scope (`~/.codex/skills/<name>`, `~/.claude/skills/<name>`, or the project-local equivalents), run:

   ```bash
   python3 /tmp/ufailure_once.py remove <skill-name> --confirm
   ```

8. **Delete `/tmp/ufailure_once.py`.** This step is mandatory — the one-shot promise depends on it.
9. Report removed skills, skipped skills, and reasons for anything not removed. Show the script's `✓ Removed: <path>` lines verbatim.
10. Keep the user-facing interaction minimal: show the ranked table from the script, wait for the reply, run the dry-run/confirm pair, show the result, delete the script, done.
