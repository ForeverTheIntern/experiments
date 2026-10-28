"""Extract evidence for (repository, commit) pairs without full clones.

Each repository gets a throwaway bare partial repository. ``git fetch --depth 1
--filter=blob:none`` brings one commit and its trees (the complete path list,
with no API quota and no truncation), then a single ``git fetch --stdin`` of
the selected blob IDs brings only the evidence files.

Corpus layout::

    repos/<owner>__<name>/<commit>/        the packet a ranker reads
        paths.txt                          every file path at the commit
        <path>                             each extracted file at its repository path,
                                           cut at MAX_FILE_BYTES
    meta/<owner>__<name>/<commit>.json     snapshot metadata, extraction stats and one
                                           record (kind, subkind, blob id) per extracted file
    .blobs/<aa>/<oid>                      content-addressed store; packet files hard-link into it

Packets hold nothing but repository files and ``paths.txt``, so a reader that
classifies files by name sees no extraction metadata. The sidecar is written
last and marks the snapshot as complete.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

from repo_discovery.contract import Universe
from repo_discovery.contract import evidence_dir as contract_evidence_dir

from .select import MAX_FILE_BYTES, select, sniff

EXTRACTOR = "repo_discovery.universe.evidence/2"
PATH_LIST = "paths.txt"
# Root names a packet reader may take for the file list; repository files never use them.
RESERVED_ROOT_NAMES = frozenset([PATH_LIST, "files.txt", "tree.txt"])


def repo_dirname(repo: str) -> str:
    owner, name = repo.split("/", 1)
    return f"{owner}__{name}"


def evidence_dir(corpus: Path, repo: str, commit: str) -> Path:
    return contract_evidence_dir(Path(corpus), repo, commit)


def meta_path(corpus: Path, repo: str, commit: str) -> Path:
    return Path(corpus) / "meta" / repo_dirname(repo) / f"{commit}.json"


def is_extracted(corpus: Path, repo: str, commit: str) -> bool:
    return meta_path(corpus, repo, commit).exists() and evidence_dir(corpus, repo, commit).is_dir()


class GitError(RuntimeError):
    pass


class PartialRepo:
    def __init__(self, git_dir: Path, url: str):
        self.git_dir = Path(git_dir)
        self.url = url

    def run(self, *args: str, stdin: bytes | None = None, timeout: int = 900) -> bytes:
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        proc = subprocess.run(
            ["git", f"--git-dir={self.git_dir}", *args],
            input=stdin,
            capture_output=True,
            timeout=timeout,
            env=env,
        )
        if proc.returncode:
            raise GitError(
                f"git {' '.join(args[:3])}: {proc.stderr.decode(errors='replace')[-500:]}"
            )
        return proc.stdout

    def ensure(self) -> None:
        if (self.git_dir / "HEAD").exists():
            return
        self.git_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", "--bare", str(self.git_dir)], check=True)
        self.run("remote", "add", "origin", self.url)
        self.run("config", "remote.origin.promisor", "true")
        self.run("config", "remote.origin.partialclonefilter", "blob:none")
        self.run("config", "extensions.partialClone", "origin")
        self.run("config", "gc.auto", "0")
        self.run("config", "maintenance.auto", "false")

    def pack_bytes(self) -> int:
        pack = self.git_dir / "objects" / "pack"
        return sum(p.stat().st_size for p in pack.iterdir()) if pack.exists() else 0

    def fetch_commit(self, commit: str) -> None:
        self.run(
            "fetch",
            "-q",
            "--depth",
            "1",
            "--filter=blob:none",
            "--no-tags",
            "origin",
            f"+{commit}:refs/snap/{commit}",
        )

    def fetch_blobs(self, oids: Iterable[str]) -> None:
        wanted = "\n".join(sorted(set(oids)))
        if wanted:
            self.run(
                "-c",
                "fetch.negotiationAlgorithm=noop",
                "fetch",
                "-q",
                "--no-tags",
                "--no-write-fetch-head",
                "--recurse-submodules=no",
                "--filter=blob:none",
                "--stdin",
                "origin",
                stdin=wanted.encode() + b"\n",
            )

    def ls_tree(self, commit: str) -> list[tuple[str, str, str, str]]:
        out = self.run("ls-tree", "-r", "-z", "--full-tree", commit)
        rows = []
        for record in out.split(b"\0"):
            if record:
                head, path = record.split(b"\t", 1)
                mode, kind, oid = head.decode().split(" ")
                rows.append((mode, kind, oid, path.decode(errors="surrogateescape")))
        return rows

    def commit_info(self, commit: str) -> tuple[str, str]:
        tree, date = self.run("show", "-s", "--format=%T %cI", commit).decode().split()
        return tree, date

    def read_blobs(self, oids: list[str]) -> dict[str, bytes]:
        if not oids:
            return {}
        out = self.run("cat-file", "--batch", stdin=("\n".join(oids) + "\n").encode())
        blobs, pos = {}, 0
        while pos < len(out):
            end = out.index(b"\n", pos)
            header = out[pos:end].decode().split()
            pos = end + 1
            if len(header) < 3 or header[1] == "missing":
                continue
            size = int(header[2])
            blobs[header[0]] = out[pos : pos + size]
            pos += size + 1
        return blobs


class BlobStore:
    def __init__(self, corpus: Path):
        self.root = Path(corpus) / ".blobs"

    def path(self, key: str) -> Path:
        return self.root / key[:2] / key

    def has(self, key: str) -> bool:
        return self.path(key).exists()

    def put(self, key: str, data: bytes) -> Path:
        target = self.path(key)
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(f".tmp{threading.get_ident()}")
            tmp.write_bytes(data)
            os.replace(tmp, target)
        return target

    def link(self, key: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest.unlink()
        try:
            os.link(self.path(key), dest)
        except OSError:
            shutil.copyfile(self.path(key), dest)


def _safe_relpath(path: str) -> str | None:
    parts = [p for p in path.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    return "/".join(parts)


def extract_commit(
    git: PartialRepo, store: BlobStore, corpus: Path, repo: str, commit: str
) -> dict:
    t0 = time.monotonic()
    packs0 = git.pack_bytes()
    git.fetch_commit(commit)
    t_tree = time.monotonic()
    rows = git.ls_tree(commit)
    tree, committed = git.commit_info(commit)
    blobs = [(path, oid) for mode, kind, oid, path in rows if kind == "blob" and mode != "120000"]
    paths = [path for mode, kind, _, path in rows if kind == "blob"]
    submodules = [path for _, kind, _, path in rows if kind == "commit"]
    candidates, dropped = select(blobs)
    missing = [c.oid for c in candidates if not store.has(c.oid)]
    git.fetch_blobs(missing)
    t_blobs = time.monotonic()
    contents = git.read_blobs(sorted(set(missing)))

    out = evidence_dir(corpus, repo, commit)
    tmp = out.with_name(out.name + ".partial")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    records, kinds, kind_bytes = [], {}, {}
    skipped = {"binary": 0, "not_contract": 0, "missing": 0, "unsafe_path": 0, "reserved_path": 0}
    for cand in candidates:
        if not store.has(cand.oid):
            data = contents.get(cand.oid)
            if data is None:
                skipped["missing"] += 1
                continue
            store.put(cand.oid, data[:MAX_FILE_BYTES] if b"\0" not in data[:8000] else b"\0")
        stored = store.path(cand.oid)
        head = stored.read_bytes()
        if head == b"\0":
            skipped["binary"] += 1
            continue
        subkind = sniff(cand.subkind, head) if cand.sniff else cand.subkind
        if subkind is None:
            skipped["not_contract"] += 1
            continue
        rel = _safe_relpath(cand.path)
        if rel is None:
            skipped["unsafe_path"] += 1
            continue
        if rel in RESERVED_ROOT_NAMES:
            skipped["reserved_path"] += 1
            continue
        store.link(cand.oid, tmp / rel)
        size = len(head)
        full = len(contents[cand.oid]) if cand.oid in contents else None
        records.append(
            {
                "path": rel,
                "kind": cand.kind,
                "subkind": subkind,
                "oid": cand.oid,
                "bytes": size,
                "truncated": size >= MAX_FILE_BYTES and (full is None or full > size),
            }
        )
        kinds[cand.kind] = kinds.get(cand.kind, 0) + 1
        kind_bytes[cand.kind] = kind_bytes.get(cand.kind, 0) + size

    listing = ("\n".join(paths) + "\n").encode(errors="surrogateescape")
    key = "paths-" + hashlib.sha256(listing).hexdigest()
    store.put(key, listing)
    store.link(key, tmp / PATH_LIST)
    t_end = time.monotonic()
    meta = {
        "repo": repo,
        "commit": commit,
        "tree": tree,
        "committed_date": committed,
        "extractor": EXTRACTOR,
        "extracted_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "n_paths": len(paths),
        "submodules": submodules,
        "files": kinds,
        "bytes": kind_bytes,
        "dropped_by_cap": dropped,
        "skipped": skipped,
        "blobs_fetched": len(set(missing)),
        "network_pack_bytes": git.pack_bytes() - packs0,
        "seconds": {
            "tree": round(t_tree - t0, 3),
            "blobs": round(t_blobs - t_tree, 3),
            "write": round(t_end - t_blobs, 3),
        },
        "evidence": records,
    }
    sidecar = meta_path(corpus, repo, commit)
    if sidecar.exists():
        sidecar.unlink()
    if out.exists():
        shutil.rmtree(out)
    os.replace(tmp, out)
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar_tmp = sidecar.with_suffix(".json.partial")
    sidecar_tmp.write_text(json.dumps(meta, indent=1) + "\n")
    os.replace(sidecar_tmp, sidecar)
    return meta


def extract_repo(
    repo: str,
    commits: list[str],
    corpus: Path,
    git_root: Path,
    keep_git: bool = False,
    url: str | None = None,
) -> list[dict]:
    store = BlobStore(corpus)
    git = PartialRepo(
        Path(git_root) / f"{repo_dirname(repo)}.git", url or f"https://github.com/{repo}.git"
    )
    results = []
    todo = [c for c in commits if not is_extracted(corpus, repo, c)]
    if not todo:
        return [{"repo": repo, "commit": c, "status": "cached"} for c in commits]
    try:
        git.ensure()
        for commit in todo:
            started = time.monotonic()
            try:
                meta = extract_commit(git, store, corpus, repo, commit)
                results.append(
                    {
                        "repo": repo,
                        "commit": commit,
                        "status": "ok",
                        "wall_s": round(time.monotonic() - started, 3),
                        **{
                            k: meta[k]
                            for k in (
                                "n_paths",
                                "files",
                                "bytes",
                                "blobs_fetched",
                                "network_pack_bytes",
                                "seconds",
                            )
                        },
                    }
                )
            except (GitError, subprocess.TimeoutExpired, OSError, ValueError) as err:
                results.append(
                    {
                        "repo": repo,
                        "commit": commit,
                        "status": "error",
                        "error": str(err)[-400:],
                        "wall_s": round(time.monotonic() - started, 3),
                    }
                )
        results.append({"repo": repo, "status": "git_cache", "bytes": git.pack_bytes()})
    finally:
        if not keep_git:
            shutil.rmtree(git.git_dir, ignore_errors=True)
    return results


def extract_many(
    pairs: Iterable[tuple[str, str]],
    corpus: Path,
    git_root: Path,
    log_path: Path,
    workers: int = 8,
    keep_git: bool = False,
    log: Callable[[str], None] = lambda _: None,
) -> list[dict]:
    by_repo: dict[str, list[str]] = {}
    for repo, commit in pairs:
        by_repo.setdefault(repo, [])
        if commit not in by_repo[repo]:
            by_repo[repo].append(commit)
    lock = threading.Lock()
    results: list[dict] = []
    log_path.parent.mkdir(parents=True, exist_ok=True)
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool, open(log_path, "a") as fh:
        futures = {
            pool.submit(extract_repo, repo, commits, corpus, git_root, keep_git): repo
            for repo, commits in by_repo.items()
        }
        for fut in as_completed(futures):
            rows = fut.result()
            with lock:
                for row in rows:
                    fh.write(json.dumps(row) + "\n")
                fh.flush()
                results.extend(rows)
                done += 1
                if done % 25 == 0 or done == len(futures):
                    errors = sum(r["status"] == "error" for r in results)
                    log(f"extract: {done}/{len(futures)} repositories, {errors} errors")
    return results


def materialize(universe_dir: Path, corpus: Path) -> tuple[int, list[str]]:
    """Point ``<universe_dir>/repos`` at the corpus and report rows without evidence.

    One relative symlink per universe instead of one per row: per-row links cost two
    filesystem blocks each, about 1 GB for U1000 across 120 cases. The link exposes the
    whole corpus, so rankers must read only their candidates' evidence directories;
    the harness rejects rankings that name repositories outside the universe.
    """
    universe_dir = Path(universe_dir)
    universe = Universe.load(universe_dir)
    link = universe_dir / "repos"
    if link.is_symlink():
        link.unlink()
    elif link.exists():
        shutil.rmtree(link)
    target = (Path(corpus) / "repos").resolve()
    target.mkdir(parents=True, exist_ok=True)
    link.symlink_to(os.path.relpath(target, universe_dir.resolve()), target_is_directory=True)
    missing = [
        f"{e.repo}@{e.commit}"
        for e in universe.entries
        if not is_extracted(corpus, e.repo, e.commit)
    ]
    return len(universe.entries) - len(missing), missing
