from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from repo_discovery import wideswe
from repo_discovery.cases import Case


def test_load_builds_cases(mini_cases: list[Case]) -> None:
    by_id = {c.case_id: c for c in mini_cases}
    assert list(by_id) == [
        "acme_billing_ledger_000000000001",
        "acme_billing_portal_000000000002",
        "globex_core_cli_docs_000000000003",
    ]
    first = by_id["acme_billing_ledger_000000000001"]
    assert first.source == "wideswe"
    assert first.ecosystem == "acme"
    assert first.category == "bugfix"
    assert first.prompt.startswith("Monthly invoices")
    assert [(g.repo, g.commit) for g in first.gold] == [
        ("acme/billing", "a1" * 20),
        ("acme/ledger", "b2" * 20),
    ]
    assert [(c.repo, c.commit) for c in first.context] == [("acme/portal", "c3" * 20)]
    assert first.cutoff == "2026-03-10T08:32:36+00:00"
    assert by_id["globex_core_cli_docs_000000000003"].gold_repos[-1] == "globex/docs-site"


def test_visibility_from_alias_matching(mini_cases: list[Case]) -> None:
    assert [(c.scope_visibility, c.visible_gold) for c in mini_cases] == [
        ("none", ()),
        ("partial", ("acme/portal",)),
        ("complete", ("globex/core", "globex/cli", "globex/docs-site")),
    ]


def test_index_disagreement_is_an_error(tmp_path: Path, wideswe_mini: Path) -> None:
    root = tmp_path / "wideswe"
    shutil.copytree(wideswe_mini, root)
    index = root / wideswe.INDEX_FILE
    index.write_text(index.read_text().replace("billing, ledger", "billing, ledger, extra"))
    with pytest.raises(ValueError, match="disagree"):
        wideswe.load(root)


def test_numeric_yaml_commit_is_an_error(tmp_path: Path, wideswe_mini: Path) -> None:
    root = tmp_path / "wideswe"
    shutil.copytree(wideswe_mini, root)
    repos = root / "cases/acme/acme_billing_ledger_000000000001/repos.yaml"
    repos.write_text(repos.read_text().replace("a1" * 20, "1" * 40))
    with pytest.raises(ValueError, match="40-character commit"):
        wideswe.load(root)


def test_github_repo() -> None:
    assert wideswe.github_repo("https://github.com/elastic/eui.git") == "elastic/eui"
    assert wideswe.github_repo("https://github.com/twigphp/Twig") == "twigphp/Twig"
    with pytest.raises(ValueError):
        wideswe.github_repo("https://gitlab.com/a/b.git")


def test_cache_root_honours_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("REPO_DISCOVERY_CACHE", str(tmp_path / "c"))
    assert wideswe.cache_root() == tmp_path / "c"
    monkeypatch.delenv("REPO_DISCOVERY_CACHE")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert wideswe.cache_root() == tmp_path / "xdg" / "repo-discovery"


def _git(*args: str, cwd: Path) -> str:
    out = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return out.stdout.strip()


def test_fetch_checks_out_only_needed_files(
    tmp_path: Path, wideswe_mini: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    shutil.copytree(wideswe_mini, source)
    patch = source / "cases/acme/acme_billing_ledger_000000000001/gold/billing.patch"
    patch.parent.mkdir()
    patch.write_text("diff --git a/x b/x\n")
    _git("init", "--quiet", cwd=source)
    _git("add", ".", cwd=source)
    _git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "init", cwd=source)
    _git("config", "uploadpack.allowFilter", "true", cwd=source)
    _git("config", "uploadpack.allowAnySHA1InWant", "true", cwd=source)
    commit = _git("rev-parse", "HEAD", cwd=source)

    dest = wideswe.fetch(tmp_path / "cache", commit=commit, url=source.as_uri())
    assert dest == tmp_path / "cache" / "wideswe" / commit
    assert len(wideswe.load(dest)) == 3
    assert not (dest / patch.relative_to(source)).exists()

    def no_git(*args: str, cwd: Path) -> str:
        raise AssertionError("cached checkout should not run git")

    monkeypatch.setattr(wideswe, "_git", no_git)
    assert wideswe.fetch(tmp_path / "cache", commit=commit, url=source.as_uri()) == dest
