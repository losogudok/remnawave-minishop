"""Guard the staging checks-to-promotion boundary and validate candidate artifacts."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github/workflows"
COMMIT = "a" * 40


def _job(workflow: str, name: str) -> str:
    match = re.search(rf"^  {name}:\n(.*?)(?=^  \S|\Z)", workflow, re.M | re.S)
    assert match is not None
    return match.group(1)


def test_dev_candidates_start_early_but_promotion_and_deploy_wait_for_gates() -> None:
    workflow = (WORKFLOWS / "docker-dev.yml").read_text()
    publish = _job(workflow, "publish")
    assert "needs:" not in publish
    assert "promote_channel: false" in publish
    promote = _job(workflow, "promote")
    assert "needs: [checks, fullstack, security, publish]" in promote
    assert "if: github.ref == 'refs/heads/dev'" in promote
    assert "IMAGE_OWNER: ${{ github.repository_owner }}" in promote
    assert "--tag" in promote and ':dev" "$image"' in promote
    assert promote.index("Validate source and image references") < promote.index(
        "Promote scanned digests to dev"
    )
    assert "needs: promote" in _job(workflow, "deploy")
    reusable = (WORKFLOWS / "_docker-publish-ghcr.yml").read_text()
    assert "promote_channel:" in reusable
    assert "type: boolean\n        default: true" in reusable
    assert "Publish verified channel tags\n        if: ${{ inputs.promote_channel }}" in reusable
    assert reusable.index("Scan the published candidate") < reusable.index(
        "Record scanned image for deployment"
    )


def test_dev_qa_matches_staging_and_other_callers_keep_full_matrix() -> None:
    workflow = (WORKFLOWS / "docker-dev.yml").read_text()
    match = re.search(r"remnawave_versions: '(.+)'", _job(workflow, "fullstack"))
    assert match is not None
    versions = json.loads(match.group(1))
    compose = (ROOT / "deploy/staging/compose.yml").read_text()
    assert len(versions) == 1
    assert f"image: remnawave/backend:{versions[0]}\n" in compose
    qa = (WORKFLOWS / "fullstack-qa.yml").read_text()
    fallback = re.search(r"fromJSON\(inputs.remnawave_versions \|\| '(.+)'\)", qa)
    assert fallback is not None
    full_versions = json.loads(fallback.group(1))
    assert len(full_versions) == 15
    assert versions[0] in full_versions and "2.8.1" in full_versions
    assert "remnawave_versions:" not in (WORKFLOWS / "production.yml").read_text()


@pytest.mark.parametrize("invalid", [None, "commit", "digest", "registry", "missing"])
def test_promotion_validates_all_candidates_before_changing_tags(
    tmp_path: Path, invalid: str | None
) -> None:
    workflow = (WORKFLOWS / "docker-dev.yml").read_text()
    promote = _job(workflow, "promote")
    match = re.search(r"          python3 - <<'PY'\n(.*?)\n          PY", promote, re.S)
    assert match is not None
    script = "\n".join(line[10:] for line in match.group(1).splitlines())
    artifacts = tmp_path / "deployment-images"
    artifacts.mkdir()
    for target in ("backend", "worker", "frontend"):
        commit = COMMIT
        image = f"ghcr.io/losogudok/remnawave-minishop-{target}@sha256:{'b' * 64}"
        if target == "frontend":
            if invalid == "commit":
                commit = "c" * 40
            elif invalid == "digest":
                image = f"ghcr.io/losogudok/remnawave-minishop-{target}:dev"
            elif invalid == "registry":
                image = image.replace("losogudok", "untrusted")
            elif invalid == "missing":
                continue
        (artifacts / f"{target}.txt").write_text(f"{commit}\n{image}\n")
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env={**os.environ, "DEPLOY_COMMIT": COMMIT, "IMAGE_OWNER": "losogudok"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode == 0) == (invalid is None), result.stderr
