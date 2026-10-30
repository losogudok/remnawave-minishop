"""Exercise deployment failure boundaries without touching Docker or production."""

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/deploy-production.sh"
COMMIT = "a" * 40


@pytest.mark.parametrize(
    ("scenario", "returncode", "stops", "starts"),
    [
        ("success", 0, True, True),
        ("stale", 0, False, False),
        ("dirty", 1, False, False),
        ("build-fails", 1, False, False),
        ("migration-fails", 1, True, False),
    ],
)
def test_deployment_boundaries(
    tmp_path: Path, scenario: str, returncode: int, *, stops: bool, starts: bool
) -> None:
    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)
    (checkout / ".env").write_text("TEST_ONLY=1\n")
    commands = tmp_path / "bin"
    commands.mkdir()
    log = tmp_path / "docker.log"
    stubs = {
        "git": """#!/bin/bash
case "$1 $2" in
  'remote get-url') echo https://github.com/losogudok/remnawave-minishop.git;;
  'branch --show-current') echo main;;
  'status --porcelain') if [[ $SCENARIO == dirty ]]; then echo ' M tracked'; fi;;
  'rev-parse origin/main')
    if [[ $SCENARIO == stale ]]; then printf '%040d\\n' 0; else echo "$COMMIT"; fi;;
  'rev-parse HEAD') echo "$COMMIT";;
esac
exit 0
""",
        "df": (
            "#!/bin/bash\nprintf 'Filesystem 1024-blocks Used Available Capacity Mounted\\n"
            "mock 99999999 0 99999999 0%% /\\n'\n"
        ),
        "docker": """#!/bin/bash
echo "$*" >> "$COMMAND_LOG"
if [[ $SCENARIO == build-fails && $2 == build ]]; then exit 1; fi
if [[ $SCENARIO == migration-fails && $2 == run ]]; then exit 1; fi
if [[ $2 == exec ]]; then echo 'mock-database-dump'; fi
exit 0
""",
    }
    for name, contents in stubs.items():
        executable = commands / name
        executable.write_text(contents)
        executable.chmod(0o700)
    result = subprocess.run(
        ["bash", str(SCRIPT), COMMIT],
        env={
            **os.environ,
            "PATH": f"{commands}:{os.environ['PATH']}",
            "DEPLOY_DIR": str(checkout),
            "SCENARIO": scenario,
            "COMMIT": COMMIT,
            "COMMAND_LOG": str(log),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == returncode, result.stdout + result.stderr
    calls = log.read_text() if log.exists() else ""
    assert ("compose stop backend worker" in calls) == stops
    assert ("compose up -d" in calls) == starts
    if starts:
        assert calls.index("compose build") < calls.index("compose stop")
        assert calls.index("compose exec") < calls.index("compose run")
        assert calls.index("compose run") < calls.index("compose up")
        backups = list((checkout / "backups").iterdir())
        assert len(backups) == 1
        assert (backups[0] / "database.dump").stat().st_mode & 0o077 == 0
