# Put shared artifacts in shared storage, with checksums, from the start

**Experiment:** repo-discovery · **Added:** 2026-10-29 · **Source:** coordinator's notes; store `internal/corpus/README.md`; the Files sections of store `docs/v4-embeddings.md` and `docs/fetch-level-index-shape.md`; store `docs/v5-memory-checks.md` (Reproducing); [PR #4](https://github.com/ForeverTheIntern/experiments/pull/4) (Hosting)

**What happened.** The evidence corpus went into the Project store as split tarballs, with `SHA256SUMS` and a README of unpack commands, and every later worker used it. Other run artifacts stayed on the worker VMs: predictions, traces, restricted corpora, evidence packets, and the anonymized corpus with its salt and mapping. Later workers rebuilt them, and one rebuild of the anonymized corpus used a different salt.

The store has two limits. It rejects files over 100 MiB: `cp` reports `Invalid argument` and leaves a 0-byte file, so tarballs go in 95 MiB parts (`split -b 95M -d -a 2`). And unpacking a million small files onto the store mount is slow, so unpack to local disk.

**What it cost.** Rebuilds of artifacts that already existed. The rebuilt anonymized corpus has IDs that don't match the ones in the memory-check traces.

**Next time.** Name each shared artifact's store path in the plan. Write it as parts plus `SHA256SUMS` and a short README, check that it reads back, and unpack to local disk. Private material (salts, mappings, third-party content) goes under the store's `internal/`, never in the repo.
