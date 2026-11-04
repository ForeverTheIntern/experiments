from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from repo_discovery.cases import Case, RepoAtCommit
from repo_discovery.split import REGISTRATION, check_registered, run_cases


def git(cwd: Path, *args: str) -> str:
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "PATH": "/usr/bin:/bin:/usr/local/bin",
    }
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True
    ).stdout.strip()


def case(case_id: str, split: str) -> Case:
    return Case(
        case_id=case_id,
        source="wideswe",
        ecosystem="acme",
        prompt="x",
        gold=(RepoAtCommit("acme/a", "a" * 40),),
        cutoff="2026-01-01T00:00:00+00:00",
        split=split,
    )


def test_dev_needs_no_registration() -> None:
    cases = [case("d1", "dev"), case("t1", "test")]
    assert [c.case_id for c in run_cases(cases, "dev")] == ["d1"]
    with pytest.raises(SystemExit, match="--registered"):
        run_cases(cases, "test")
    with pytest.raises(SystemExit, match="--registered"):
        run_cases(cases, "all")


def test_registration_must_hold_the_plan_and_precede_head(tmp_path: Path) -> None:
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "README.md").write_text("x\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "before")
    before = git(tmp_path, "rev-parse", "HEAD")
    plan = tmp_path / REGISTRATION
    plan.parent.mkdir(parents=True)
    plan.write_text("# Preregistration\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "register")
    registered = git(tmp_path, "rev-parse", "HEAD")
    assert check_registered(registered[:10], tmp_path) == registered
    with pytest.raises(SystemExit, match="not in that commit"):
        check_registered(before, tmp_path)
    git(tmp_path, "checkout", "-q", before)
    with pytest.raises(SystemExit, match="not an ancestor"):
        check_registered(registered, tmp_path)
    with pytest.raises(SystemExit, match="not a commit"):
        check_registered("f" * 40, tmp_path)
