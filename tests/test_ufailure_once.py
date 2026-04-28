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
