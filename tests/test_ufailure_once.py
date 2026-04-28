from ufailure_once import main


def test_stats_command_exists():
    assert main(["stats", "--since", "90d"]) == 0
