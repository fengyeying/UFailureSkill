from pathlib import Path

from ufailure_once import discover_user_skills, main


def test_stats_command_exists():
    assert main(["stats", "--since", "90d"]) == 0


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
    assert rows[0]["percent"] == 83.3
    assert rows[1]["percent"] == 16.7
    assert rows[2]["percent"] == 0.0
    assert rows[0]["candidate"] is False
    assert rows[1]["candidate"] is True
    assert rows[2]["candidate"] is True


from ufailure_once import find_removable_skill_paths, remove_skill


def test_find_removable_skill_paths_only_allows_user_skill_roots(tmp_path):
    codex_root = tmp_path / ".codex" / "skills"
    allowed = codex_root / "rare"
    allowed.mkdir(parents=True)
    (allowed / "SKILL.md").write_text("# Rare\n", encoding="utf-8")

    paths = find_removable_skill_paths("rare", home=tmp_path)

    assert paths == [allowed]


def test_find_removable_skill_paths_rejects_missing_skill(tmp_path):
    assert find_removable_skill_paths("missing", home=tmp_path) == []


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


def test_build_report_rows_includes_paths_count(tmp_path):
    usage = {
        "dup": Usage("dup", uses=0, last_used=None),
    }
    paths_by_skill = {
        "dup": [tmp_path / ".codex" / "skills" / "dup", tmp_path / ".claude" / "skills" / "dup"],
    }

    rows = build_report_rows(usage, candidate_threshold=1, paths_by_skill=paths_by_skill)

    assert rows[0]["paths"] == 2


import io
from contextlib import redirect_stdout

from ufailure_once import print_text_report


def test_print_text_report_renders_scope_sections_and_plugin_pointer():
    rows = [
        {"skill": "lonely", "uses": 0, "percent": 0.0, "last_used": None, "candidate": True, "paths": 1, "scope": "user", "removable": True},
        {"skill": "superpowers:brainstorming", "uses": 5, "percent": 100.0, "last_used": None, "candidate": False, "paths": 1, "scope": "plug", "removable": False},
    ]
    buf = io.StringIO()
    with redirect_stdout(buf):
        print_text_report(rows, since_days=90)
    out = buf.getvalue()

    # Each scope gets its own titled section so user can see at a glance
    # what is global, project, and plugin.
    assert "Global skills" in out
    assert "Project skills" in out
    assert "Plugin skills" in out
    # The plugin pointer + read-only marker are shown in the section header
    # so the user knows to use /plugin to manage them.
    assert "/plugin" in out
    assert "read-only" in out


def test_print_text_report_shows_section_for_empty_project_scope():
    rows = [
        {"skill": "writer", "uses": 1, "percent": 100.0, "last_used": None, "candidate": True, "paths": 1, "scope": "user", "removable": True},
    ]
    buf = io.StringIO()
    with redirect_stdout(buf):
        print_text_report(rows, since_days=90)
    out = buf.getvalue()

    # Project section is always rendered (with "(none)" if empty) so the
    # user can see the distinction even when they have no project skills.
    assert "Project skills" in out
    assert "(none)" in out


def test_collect_usage_counts_codex_skill_md_path_mentions(tmp_path):
    """Codex Desktop invokes skills by reading their SKILL.md path through
    exec_command. The transcript records the path in function_call args; we
    must recognise it as a skill invocation."""
    write_jsonl(
        tmp_path / ".codex" / "sessions" / "2026" / "04" / "29" / "rollout.jsonl",
        [
            {
                "timestamp": "2026-04-29T00:00:00Z",
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "exec_command",
                    "arguments": {"command": "cat /Users/me/.codex/skills/deep-research/SKILL.md"},
                },
            },
        ],
    )

    result = collect_usage(home=tmp_path, known_skills={"deep-research"}, since_days=90)

    assert result["deep-research"].uses == 1


def test_collect_usage_resolves_plugin_namespace_from_path(tmp_path):
    """A path under ~/.claude/plugins/<plugin>/.../skills/<name>/SKILL.md
    should count against the namespaced known-skill name."""
    write_jsonl(
        tmp_path / ".codex" / "sessions" / "2026" / "04" / "29" / "rollout.jsonl",
        [
            {
                "timestamp": "2026-04-29T00:00:00Z",
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "exec_command",
                    "arguments": {
                        "command": "cat /Users/me/.claude/plugins/cache/m/superpowers/5.0.6/skills/brainstorming/SKILL.md"
                    },
                },
            },
        ],
    )

    result = collect_usage(
        home=tmp_path,
        known_skills={"superpowers:brainstorming", "brainstorming"},
        since_days=90,
    )

    # The namespaced form should win; the bare form should not double-count.
    assert result["superpowers:brainstorming"].uses == 1
    assert result["brainstorming"].uses == 0


def test_print_text_report_lists_all_plugin_skills_regardless_of_uses():
    rows = [
        {"skill": "superpowers:used", "uses": 3, "percent": 100.0, "last_used": None, "candidate": False, "paths": 1, "scope": "plug", "removable": False},
        {"skill": "superpowers:unused", "uses": 0, "percent": 0.0, "last_used": None, "candidate": False, "paths": 1, "scope": "plug", "removable": False},
        {"skill": "anthropic-skills:also-unused", "uses": 0, "percent": 0.0, "last_used": None, "candidate": False, "paths": 1, "scope": "plug", "removable": False},
    ]
    buf = io.StringIO()
    with redirect_stdout(buf):
        print_text_report(rows, since_days=90)
    out = buf.getvalue()

    # All three plugin rows must appear, including the two with zero uses.
    assert "superpowers:used" in out
    assert "superpowers:unused" in out
    assert "anthropic-skills:also-unused" in out
    # No "hidden" message since we no longer gate plugin display.
    assert "hidden" not in out.lower()


from ufailure_once import truncate_name


def test_truncate_name_uses_ellipsis_for_long_names():
    assert truncate_name("short", 10) == "short"
    long = "superpowers-test-driven-development-extended"
    truncated = truncate_name(long, 23)
    assert len(truncated) == 23
    assert "…" in truncated
    assert truncated.startswith("superpowers")
    assert truncated.endswith("extended")
    assert truncate_name("a" * 24, 23).endswith("a") and "…" in truncate_name("a" * 24, 23)


import pytest

from ufailure_once import parse_days


def test_parse_days_rejects_non_positive():
    with pytest.raises(ValueError):
        parse_days("0d")
    with pytest.raises(ValueError):
        parse_days("-7d")
    with pytest.raises(ValueError):
        parse_days("0")


from ufailure_once import (
    ASCII_GLYPHS,
    RICH_GLYPHS,
    render_bar,
    select_glyphs,
)


def test_select_glyphs_defaults_to_ascii_when_not_a_tty():
    glyphs = select_glyphs(isatty=False)
    assert glyphs is ASCII_GLYPHS


def test_select_glyphs_uses_rich_when_tty():
    glyphs = select_glyphs(isatty=True)
    assert glyphs is RICH_GLYPHS


def test_select_glyphs_force_ascii_overrides_tty():
    glyphs = select_glyphs(force_ascii=True, isatty=True)
    assert glyphs is ASCII_GLYPHS


def test_select_glyphs_force_rich_overrides_pipe():
    glyphs = select_glyphs(force_rich=True, isatty=False)
    assert glyphs is RICH_GLYPHS


def test_print_text_report_ascii_mode_emits_only_ascii():
    rows = [
        {"skill": "active", "uses": 5, "percent": 71.4, "last_used": None, "candidate": False, "paths": 1},
        {"skill": "lonely", "uses": 0, "percent": 0.0, "last_used": None, "candidate": True, "paths": 2},
    ]
    buf = io.StringIO()
    with redirect_stdout(buf):
        print_text_report(rows, since_days=90, glyphs=ASCII_GLYPHS)
    out = buf.getvalue()

    assert out.isascii(), f"non-ASCII char leaked: {out!r}"
    assert "#" in out  # ASCII bar
    assert "!" in out  # ASCII warn glyph for the dual-path candidate
    assert "-" * 10 in out  # ASCII rule


def test_render_bar_ascii_mode_uses_only_ascii():
    bar = render_bar(uses=3, max_uses=10, glyphs=ASCII_GLYPHS, width=10)
    assert bar.isascii()
    assert "#" in bar
    assert "█" not in bar


def test_truncate_name_ascii_mode_uses_double_dot():
    long = "superpowers-test-driven-development-extended"
    truncated = truncate_name(long, 23, ASCII_GLYPHS)
    assert truncated.isascii()
    assert ".." in truncated
    assert "…" not in truncated
    assert len(truncated) == 23


from ufailure_once import (
    SCOPE_PLUGIN,
    SCOPE_PROJECT,
    SCOPE_USER,
    discover_skills,
)


def test_discover_skills_labels_user_global_scope(tmp_path):
    (tmp_path / ".claude" / "skills" / "writer").mkdir(parents=True)
    (tmp_path / ".claude" / "skills" / "writer" / "SKILL.md").write_text("# w\n", encoding="utf-8")

    skills = discover_skills(home=tmp_path, project_root=tmp_path / "elsewhere")

    assert len(skills) == 1
    assert skills[0].name == "writer"
    assert skills[0].scope == SCOPE_USER
    assert skills[0].removable is True


def test_discover_skills_finds_project_local_skills(tmp_path):
    project = tmp_path / "myproj"
    (project / ".claude" / "skills" / "linter").mkdir(parents=True)
    (project / ".claude" / "skills" / "linter" / "SKILL.md").write_text("# l\n", encoding="utf-8")
    home = tmp_path / "fakehome"
    home.mkdir()

    skills = discover_skills(home=home, project_root=project)
    by_scope = {(s.scope, s.name): s for s in skills}

    assert (SCOPE_PROJECT, "linter") in by_scope
    assert by_scope[(SCOPE_PROJECT, "linter")].removable is True


def test_discover_skills_skips_project_when_same_as_home(tmp_path):
    (tmp_path / ".claude" / "skills" / "writer").mkdir(parents=True)
    (tmp_path / ".claude" / "skills" / "writer" / "SKILL.md").write_text("# w\n", encoding="utf-8")

    skills = discover_skills(home=tmp_path, project_root=tmp_path)

    # Only one entry, in user scope (not duplicated as project).
    assert len(skills) == 1
    assert skills[0].scope == SCOPE_USER


def test_discover_skills_finds_plugin_skills_with_namespaced_names(tmp_path):
    plugin_path = tmp_path / ".claude" / "plugins" / "cache" / "marketplace" / "superpowers" / "5.0.6" / "skills" / "brainstorming"
    plugin_path.mkdir(parents=True)
    (plugin_path / "SKILL.md").write_text("# b\n", encoding="utf-8")

    skills = discover_skills(home=tmp_path, project_root=tmp_path / "elsewhere")

    plugin_entries = [s for s in skills if s.scope == SCOPE_PLUGIN]
    assert len(plugin_entries) == 1
    assert plugin_entries[0].name == "superpowers:brainstorming"
    assert plugin_entries[0].removable is False


def test_discover_skills_finds_plugin_skills_in_marketplace_layout(tmp_path):
    plugin_path = tmp_path / ".claude" / "plugins" / "marketplaces" / "marketplace" / "external_plugins" / "imessage" / "skills" / "access"
    plugin_path.mkdir(parents=True)
    (plugin_path / "SKILL.md").write_text("# a\n", encoding="utf-8")

    skills = discover_skills(home=tmp_path, project_root=tmp_path / "elsewhere")

    plugin_entries = [s for s in skills if s.scope == SCOPE_PLUGIN]
    assert len(plugin_entries) == 1
    assert plugin_entries[0].name == "imessage:access"


def test_collect_usage_counts_namespaced_plugin_skill_invocations(tmp_path):
    write_jsonl(
        tmp_path / ".claude" / "projects" / "p" / "s.jsonl",
        [
            {
                "timestamp": "2026-04-28T00:00:00Z",
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": "Skill", "input": {"skill": "superpowers:brainstorming"}},
                    ],
                },
            },
        ],
    )

    result = collect_usage(home=tmp_path, known_skills={"superpowers:brainstorming"}, since_days=90)

    assert result["superpowers:brainstorming"].uses == 1


def test_find_removable_skill_paths_rejects_namespaced_plugin_names(tmp_path):
    # Even if a path were somehow discovered, the colon in the name should
    # short-circuit removal.
    paths = find_removable_skill_paths("superpowers:brainstorming", home=tmp_path)
    assert paths == []


def test_build_report_rows_marks_plugin_skills_non_candidate_even_when_low_use():
    usage = {
        "superpowers:brainstorming": Usage("superpowers:brainstorming", uses=0, last_used=None),
        "writer": Usage("writer", uses=0, last_used=None),
    }
    skill_scopes = {
        "superpowers:brainstorming": SCOPE_PLUGIN,
        "writer": SCOPE_USER,
    }
    removable = {"writer"}

    rows = build_report_rows(
        usage,
        candidate_threshold=1,
        skill_scopes=skill_scopes,
        removable_skills=removable,
    )
    by_skill = {row["skill"]: row for row in rows}

    assert by_skill["writer"]["candidate"] is True
    assert by_skill["superpowers:brainstorming"]["candidate"] is False
    assert by_skill["superpowers:brainstorming"]["scope"] == SCOPE_PLUGIN
    assert by_skill["superpowers:brainstorming"]["removable"] is False
