# Dry-run a driver against a scratch root, because its redirects still write

**Experiment:** fetch-level selection export · **Added:** 2026-10-29 · **Source:** [PR #19](https://github.com/ForeverTheIntern/experiments/pull/19)

**What happened.** To check that the committed case-cost drivers passed `case_cost.py` the same arguments as the VM originals, both were run with a stub `nice` on `PATH` that logged its arguments and ran nothing. The shell still ran each `> $o/NAME.md 2> $o/NAME.err` redirect, against the real `~/rd-fu/out`, and truncated the 16 tables and logs of eight runs to empty. The JSON beside them survived because `case_cost.py` writes it itself.

**What it cost.** A repair: replaying `case_cost.py`'s printing from each JSON, after checking the replay byte for byte on two untouched runs. Without the JSON, all eight runs would have had to be rerun.

**Next time.** Give every driver a root (`R=${ROOT:-$HOME}`) and dry-run it against a scratch root of symlinks to its inputs, never the real output directory. Let the script write its own outputs (like `--json`) instead of relying on shell redirects for anything you keep.
