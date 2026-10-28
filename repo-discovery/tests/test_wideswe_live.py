"""Checks against the real WideSWE release; opt in with REPO_DISCOVERY_NETWORK_TESTS=1."""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

import pytest

from repo_discovery import split, wideswe

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        os.environ.get("REPO_DISCOVERY_NETWORK_TESTS") != "1",
        reason="set REPO_DISCOVERY_NETWORK_TESTS=1 to fetch WideSWE",
    ),
]

MANIFEST = Path(__file__).parents[1] / "splits" / "wideswe-release120.json"


def test_release120() -> None:
    cases = split.assign_splits(wideswe.load(wideswe.fetch()))
    assert len(cases) == 120
    assert len({c.ecosystem for c in cases}) == 41
    assert Counter(len(c.gold) for c in cases) == {2: 107, 3: 13}
    assert split.manifest(cases) == json.loads(MANIFEST.read_text())
    assert Counter(c.scope_visibility for c in cases) == {"none": 53, "partial": 45, "complete": 22}
