# Stage downloads before the run; don't depend on the network at run time

**Experiment:** repo-discovery · **Added:** 2026-10-29 · **Source:** coordinator's notes; store `docs/v4-embeddings.md` (Setup); store `internal/bge/`; [PR #4](https://github.com/ForeverTheIntern/experiments/pull/4) (Costs); store `docs/v5-cursor-agent.md` (Time)

**What happened.** The network failed in several ways:
- **TLS error.** A Hugging Face download failed with a TLS error on one VM.
- **CDN unreachable.** The V4 VM couldn't reach Hugging Face's LFS CDN at all. Its weights came from a GitHub mirror, with the SHA-256 checked against Hugging Face's LFS pointer.
- **Proxy stall.** The egress proxy stalled once during corpus extraction, leaving git fetches hanging until their timeouts.
- **API outage.** The GitHub API was unreachable during the V5 CLI startup stall.

Staging the model weights and vector cache in the store fixed the downloads. `internal/bge/` holds split files, `SHA256SUMS` and a `DONE` marker.

**What it cost.** Per-VM workarounds, and 17 minutes of a 56-minute extraction.

**Next time.** Before the run, stage model weights, data and caches in shared storage, with checksums and a `DONE` marker written last. Pin revisions, and check hashes before loading. For build jobs that must fetch, set a low-speed limit so a stall fails fast (PR #4 aborts below 1 KB/s for 60 s), and make the job resumable.
