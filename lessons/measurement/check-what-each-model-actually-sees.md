# Check what each model actually sees

**Experiment:** repo-discovery · **Added:** 2026-10-29 · **Source:** coordinator's notes; store `docs/v4-embeddings.md` (Cards overflow bge-small's window); store `docs/fetch-level-index-shape.md` (Dense cards need only the README); store `docs/v0-keyword-pilot.md` (Changes to PR #2; Caveats)

**What happened.** V4's cards had a 256-word budget, but bge-small reads only 512 tokens, and paths, identifiers and package names split into many word pieces. So 56% of cards, and 66% of name-led ones, were cut off. The tail went first: the API, dependency and layout sections, which carry what the cards take from contracts, manifests and the file tree. In V0, `bm25f`'s `identifiers` field was empty for about 90% of repositories on real packets, and the tokenizer split "URLs" into "ur" and "ls".

**What it cost.** For most cards, V4's dense-versus-BM25 comparison wasn't same-text. In the fetch-level grid, L3 and L4 add little to dense cards because the extra text never reaches the encoder: L2 ties L4. And `bm25f` effectively ranks on five fields, not six.

**Next time.** Before running, sample real inputs and measure them against each model's limits: token counts against the window, fill rate per field, and tokenizer output on a few tricky strings. Report the truncation rate next to the results.
