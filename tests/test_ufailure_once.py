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
