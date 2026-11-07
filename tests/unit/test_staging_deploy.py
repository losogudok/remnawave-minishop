"""Check staging failure boundaries without a remote host or Docker daemon."""

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/deploy-staging.sh"
COMMIT = "a" * 40


@pytest.mark.parametrize(
    ("scenario", "returncode", "stops", "starts"),
    [
        ("success", 0, True, True),
        ("stale", 0, False, False),
        ("dirty", 1, False, False),
        ("pull-fails", 1, False, False),
        ("wrong-revision", 1, False, False),
        ("migration-fails", 1, True, False),
        ("stdin-consumer", 0, True, True),
    ],
)
def test_staging_deployment_boundaries(
    tmp_path: Path, scenario: str, returncode: int, *, stops: bool, starts: bool
) -> None:
    stage = tmp_path / "stage"
    (stage / "source/.git").mkdir(parents=True)
    (stage / "stage.env").write_text(
        "APP_RUNTIME_MODE=staging\nQA_AUTH_ENABLED=False\nQA_PAYMENT_ENABLED=False\n"
    )
    commands = tmp_path / "bin"
    commands.mkdir()
    log = tmp_path / "docker.log"
    stubs = {
        "git": """#!/bin/bash
case "$1 $2" in
  'remote get-url') echo https://github.com/losogudok/remnawave-minishop.git;;
  'branch --show-current') echo dev;;
  'status --porcelain') if [[ $SCENARIO == dirty ]]; then echo ' M tracked'; fi;;
  'rev-parse origin/dev')
    if [[ $SCENARIO == stale ]]; then printf '%040d\\n' 0; else echo "$COMMIT"; fi;;
  'rev-parse HEAD') echo "$COMMIT";;
esac
exit 0
""",
        "docker": """#!/bin/bash
echo "$*" >> "$COMMAND_LOG"
if [[ $SCENARIO == stdin-consumer ]]; then cat > /dev/null; fi
if [[ " $* " == *' run '* && " $* " == *' --no-build '* ]]; then
  echo 'unknown flag: --no-build' >&2; exit 1
fi
if [[ $1 == image ]]; then
  if [[ $SCENARIO == wrong-revision ]]; then echo wrong; else echo "$COMMIT"; fi
  exit 0
fi
if [[ $SCENARIO == pull-fails && " $* " == *' pull '* ]]; then exit 1; fi
if [[ $SCENARIO == migration-fails && " $* " == *' run '* ]]; then exit 1; fi
if [[ " $* " == *' exec '* ]]; then echo mock-database-dump; fi
exit 0
""",
    }
    for name, contents in stubs.items():
        executable = commands / name
        executable.write_text(contents)
        executable.chmod(0o700)
    images = [
        f"ghcr.io/losogudok/remnawave-minishop-{target}@sha256:{'b' * 64}"
        for target in ("backend", "worker", "frontend")
    ]
    result = subprocess.run(
        ["bash", "-s", "--", COMMIT, *images],
        env={
            **os.environ,
            "PATH": f"{commands}:{os.environ['PATH']}",
            "STAGING_DIR": str(stage),
            "SCENARIO": scenario,
            "COMMIT": COMMIT,
            "COMMAND_LOG": str(log),
        },
        capture_output=True,
        input=SCRIPT.read_text(),
        text=True,
        check=False,
    )
    assert result.returncode == returncode, result.stdout + result.stderr
    calls = log.read_text() if log.exists() else ""
    assert ("stop stage-backend stage-worker" in calls) == stops
    assert ("180 stage-backend stage-worker stage-frontend" in calls) == starts
    assert " build" not in calls
    assert ":latest" not in calls
    if starts:
        assert calls.index(" pull") < calls.index(" stop")
        assert calls.index(" exec") < calls.index(" run")
        assert calls.index(" run") < calls.index("180 stage-backend")
        assert (stage / "deployed-commit").read_text().strip() == COMMIT
        assert images[0] in (stage / "images.env").read_text()
        backups = list((stage / "staging-backups").iterdir())
        assert len(backups) == 1
        assert (backups[0] / "shop.dump").stat().st_mode & 0o077 == 0
    else:
        assert not (stage / "deployed-commit").exists()


def test_staging_rejects_mutable_images(tmp_path: Path) -> None:
    result = subprocess.run(
        ["bash", str(SCRIPT), COMMIT, "backend:dev", "worker:dev", "frontend:dev"],
        env={**os.environ, "STAGING_DIR": str(tmp_path)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "immutable backend" in result.stderr
    assert not (tmp_path / ".staging-deploy.lock").exists()
