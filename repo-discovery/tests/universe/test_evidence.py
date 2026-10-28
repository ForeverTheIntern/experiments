from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from repo_discovery.contract import Universe
from repo_discovery.universe.evidence import evidence_dir, extract_repo, materialize, meta_path

FILES = {
    "README.md": "# Demo\n",
    "docs/guide.md": "Guide\n",
    "package.json": '{"name": "demo"}\n',
    "package-lock.json": "{}\n",
    "api/openapi.yaml": "openapi: 3.0.0\ninfo:\n  title: Demo\n",
    "api/notes.yaml": "just: data\n",
    "deploy/deployment.yaml": "apiVersion: apps/v1\nkind: Deployment\n",
    "proto/demo.proto": 'syntax = "proto3";\n',
    "src/main.py": "print('hi')\n",
    "tests/fixtures/package.json": "{}\n",
}


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


def commit_all(repo: Path, message: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def origin(tmp_path: Path) -> tuple[Path, list[str]]:
    repo = tmp_path / "origin"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "uploadpack.allowFilter", "true")
    git(repo, "config", "uploadpack.allowAnySHA1InWant", "true")
    for path, text in FILES.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    (repo / "logo.png").write_bytes(b"\x89PNG\0\0binary")
    first = commit_all(repo, "one")
    (repo / "README.md").write_text("# Demo v2\n")
    second = commit_all(repo, "two")
    return repo, [first, second]


def test_extract_and_materialize(origin: tuple[Path, list[str]], tmp_path: Path) -> None:
    repo_path, commits = origin
    corpus, git_root = tmp_path / "corpus", tmp_path / "git"
    rows = extract_repo("acme/demo", commits, corpus, git_root, url=f"file://{repo_path}")
    assert [r["status"] for r in rows if "commit" in r] == ["ok", "ok"]
    assert not any(git_root.iterdir())

    first, second = (evidence_dir(corpus, "acme/demo", c) for c in commits)
    meta = json.loads(meta_path(corpus, "acme/demo", commits[0]).read_text())
    assert meta["n_paths"] == len(FILES) + 1
    assert meta["files"] == {"readme": 1, "docs": 1, "manifest": 1, "contract": 3}
    records = {r["path"]: r["subkind"] for r in meta["evidence"]}
    assert records["api/openapi.yaml"] == "openapi"
    assert records["deploy/deployment.yaml"] == "kubernetes"
    assert records["proto/demo.proto"] == "protobuf"
    assert "api/notes.yaml" not in records and "package-lock.json" not in records
    packet = sorted(p.relative_to(first).as_posix() for p in first.rglob("*") if p.is_file())
    assert packet == sorted(["paths.txt", *records])
    assert (first / "README.md").read_text() == "# Demo\n"
    assert (second / "README.md").read_text() == "# Demo v2\n"
    assert sorted((first / "paths.txt").read_text().split()) == sorted([*FILES, "logo.png"])
    assert (first / "package.json").stat().st_ino == (second / "package.json").stat().st_ino
    assert json.loads(meta_path(corpus, "acme/demo", commits[1]).read_text())["blobs_fetched"] == 1

    again = extract_repo("acme/demo", commits, corpus, git_root, url=f"file://{repo_path}")
    assert {r["status"] for r in again} == {"cached"}

    universe_dir = tmp_path / "universes" / "case-1"
    universe_dir.mkdir(parents=True)
    lines = [
        {"repo": "acme/demo", "commit": commits[1], "snapshot_date": "2024-01-31", "role": "gold"},
        {
            "repo": "acme/gone",
            "commit": "0" * 40,
            "snapshot_date": "2024-01-31",
            "role": "distractor",
        },
    ]
    (universe_dir / "universe.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
    linked, missing = materialize(universe_dir, corpus)
    assert (linked, missing) == (1, [f"acme/gone@{'0' * 40}"])
    universe = Universe.load(universe_dir)
    assert universe.missing_evidence() == ["acme/gone"]
    link = universe_dir / "repos"
    assert link.is_symlink() and not Path(link.readlink()).is_absolute()
    packet = universe.evidence_dir(universe.entries[0])
    assert (packet / "README.md").read_text() == "# Demo v2\n"
    assert materialize(universe_dir, corpus) == (1, [f"acme/gone@{'0' * 40}"])
