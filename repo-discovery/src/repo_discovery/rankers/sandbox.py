"""Run a command so that it sees no other case.

The CLI's read rules deny ``/home``, ``/workspace``, ``/cursor`` and ``/root`` but not
``/tmp``, where every concurrent case keeps its sandbox, so an agent that greps ``/tmp``
reads other cases' packets. :func:`wrap` runs the command in its own user, mount and PID
namespaces, where each of :data:`HIDDEN` is an empty tmpfs except for the paths the run
needs, bound back at the same place; files in ``over`` are bound over others (a universe
file with its roles masked, say). The command runs as the caller's own uid and sees only
its own processes in ``/proc``. Needs unprivileged user namespaces and util-linux 2.38+.
"""

from __future__ import annotations

import functools
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

HIDDEN = ("/tmp", "/var/tmp", "/dev/shm", "/home", "/workspace", "/cursor", "/root")
STICKY = {"/tmp", "/var/tmp", "/dev/shm"}

# Runs as root of the outer user namespace, which may mount in its own mount namespace.
# /dev/shm holds the kept paths while the hidden roots are covered, and is covered last.
_SETUP = r"""
import json, os, subprocess, sys
spec, command = json.loads(sys.argv[1]), sys.argv[2:]
def mount(*args):
    subprocess.run(["mount", *args], check=True)
def target(path, like):
    if os.path.isdir(like):
        os.makedirs(path, exist_ok=True)
    else:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "a").close()
stage = "/dev/shm"
mount("-t", "tmpfs", "-o", "mode=0700", "tmpfs", stage)
sources = spec["keep"] + [src for src, _ in spec["over"]]
staged = []
for i, path in enumerate(sources):
    staged.append(f"{stage}/{i}")
    target(staged[-1], path)
    mount("--rbind", path, staged[-1])
for root in spec["hide"]:
    if root != stage and os.path.isdir(root):
        mode = "1777" if root in spec["sticky"] else "0755"
        mount("-t", "tmpfs", "-o", f"mode={mode}", "tmpfs", root)
for path, held in zip(spec["keep"], staged):
    target(path, held)
    mount("--rbind", held, path)
for (_, dst), held in zip(spec["over"], staged[len(spec["keep"]):]):
    mount("--bind", held, dst)
for held in staged:
    subprocess.run(["umount", "-l", held], check=True)
subprocess.run(["umount", "-l", stage], check=True)
if stage in spec["hide"]:
    mount("-t", "tmpfs", "-o", "mode=1777", "tmpfs", stage)
os.chdir(spec["cwd"])
inner = ["unshare", "--user", f"--map-user={spec['uid']}", f"--map-group={spec['gid']}", "--"]
sys.exit(subprocess.call([*inner, *command]))
"""


def _under(path: str, roots: Iterable[str]) -> bool:
    return any(path == r or path.startswith(r.rstrip("/") + "/") for r in roots)


def kept(paths: Iterable[Path], hide: Iterable[str] = HIDDEN) -> list[str]:
    """The existing ``paths`` under a hidden root, absolute, with nested ones dropped."""
    hide = list(hide)
    found = sorted(
        {os.path.abspath(p) for p in paths if os.path.exists(p)} - set(hide),
        key=lambda p: (p.count("/"), p),
    )
    out: list[str] = []
    for path in found:
        if _under(path, hide) and not _under(path, out):
            out.append(path)
    return sorted(out)


def wrap(
    command: list[str],
    keep: Iterable[Path] = (),
    over: Iterable[tuple[Path, Path]] = (),
    cwd: Path | None = None,
    hide: Iterable[str] = HIDDEN,
) -> list[str]:
    """``command`` run where ``hide`` holds only ``keep``, with each ``(src, dst)`` of
    ``over`` bound on top of ``dst``. ``cwd`` must be in ``keep``; it defaults to the
    caller's working directory."""
    hide = [h for h in hide if os.path.isdir(h)]
    spec = {
        "keep": kept(keep, hide),
        "over": [[os.path.abspath(s), os.path.abspath(d)] for s, d in over],
        "hide": hide,
        "sticky": sorted(STICKY),
        "cwd": os.path.abspath(cwd or os.getcwd()),
        "uid": os.getuid(),
        "gid": os.getgid(),
    }
    outer = ["unshare", "--user", "--map-root-user", "--mount", "--propagation", "private"]
    outer += ["--pid", "--fork", "--kill-child", "--mount-proc"]
    return [*outer, sys.executable, "-I", "-c", _SETUP, json.dumps(spec), *command]


@functools.cache
def unavailable() -> str | None:
    """Why :func:`wrap` can't run here, or None if it can."""
    if not shutil.which("unshare"):
        return "unshare not found"
    try:
        probe = subprocess.run(
            wrap(["true"], cwd=Path("/")), capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"namespace probe failed: {exc}"
    if probe.returncode:
        return f"namespace probe failed: {probe.stderr.strip()[-500:]}"
    return None
