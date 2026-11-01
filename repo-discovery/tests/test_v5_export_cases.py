from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from v5_export_cases import still_errored


def test_still_errored_only_for_provider_errors_without_a_ranking() -> None:
    assert still_errored({"status": "error", "follow_up_status": None, "parse": "none"})
    assert still_errored({"status": "turn_budget", "follow_up_status": "error", "parse": "none"})
    assert not still_errored({"status": "error", "follow_up_status": None, "parse": "ok"})
    assert not still_errored({"status": "timeout", "follow_up_status": "timeout", "parse": "none"})
    assert not still_errored({"status": "ok", "follow_up_status": None, "parse": "ok"})
