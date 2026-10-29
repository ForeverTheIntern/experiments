"""Print the tables of the store's ``docs/packet-construction.md`` from the doc-health outputs.

Reads the JSON that ``profile_packets.py summarize``, ``stratify.py`` and ``compare.py``
write and prints Markdown tables in the report's units. It reads nothing else, so every
number in the report comes from those three files.

    python3 scripts/doc_health/tables.py --profile ~/rd-dh/profile/summary.json \\
        --stratify ~/rd-dh/stratify.json --drops ~/rd-dh/drops.json
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Sequence
from pathlib import Path

GROUPS = ("gold", "wideswe", "same_owner", "frame")
TIERS = ("low", "mid", "high")


def pct(v: float | None) -> str:
    return "—" if v is None else f"{v:.0%}" if v >= 0.1 or v == 0 else f"{v:.1%}"


def num(v: float | None) -> str:
    return "—" if v is None else f"{v:,.0f}" if v >= 100 or v == int(v) else f"{v:g}"


def kb(v: float | None) -> str:
    return "—" if v is None else f"{v / 1000:,.0f} KB" if v >= 10_000 else f"{v / 1000:.1f} KB"


def med(stats: dict | None, key: str = "median") -> float | None:
    return None if not stats else stats.get(key)


def table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines)


def profile_tables(p: dict) -> list[tuple[str, str]]:
    u = p["unique"]
    out = []
    out.append(
        (
            "README length, distinct dev-pool snapshots at U10000",
            table(
                (
                    "Group",
                    "Snapshots",
                    "README",
                    "Tokens p25 / median / p75 / p90",
                    "Bytes, median",
                    "bge tokens, median",
                    "Over 512 bge tokens",
                ),
                [
                    (
                        g,
                        f"{u[g]['n']:,}",
                        pct(u[g]["readme"]),
                        " / ".join(
                            num(med(u[g]["readme_tokens"], k))
                            for k in ("p25", "median", "p75", "p90")
                        ),
                        num(med(u[g]["readme_bytes"])),
                        num(med(u[g]["readme_bge"])),
                        pct(u[g]["readme_over_512_bge"]),
                    )
                    for g in GROUPS
                ],
            ),
        )
    )
    out.append(
        (
            "README structure",
            table(
                (
                    "Group",
                    "Headings, median",
                    "No heading",
                    "Any code block",
                    "Any badge",
                    "Thin",
                    "Badge- or link-only",
                    "Non-English (non-Latin / Latin)",
                    ".rst",
                    "Several candidates",
                    "Language variant picked",
                ),
                [
                    (
                        g,
                        num(med(u[g]["headings"])),
                        pct(u[g]["no_headings"]),
                        pct(u[g]["any_code_block"]),
                        pct(u[g]["any_badge"]),
                        pct(u[g]["thin"]),
                        pct(u[g]["badge_link_only"]),
                        f"{pct(u[g]['non_english'])} ({pct(u[g]['non_english_non_latin'])} / "
                        f"{pct(u[g]['non_english_latin'])})",
                        pct(u[g]["readme_rst"]),
                        pct(u[g]["readme_several_candidates"]),
                        pct(u[g]["readme_variant"]),
                    )
                    for g in GROUPS
                ],
            ),
        )
    )
    out.append(
        (
            "Docs, manifests, contracts and size",
            table(
                (
                    "Group",
                    "Docs",
                    "Doc files, median",
                    "Doc bytes, median",
                    "Manifests, median",
                    "No manifest",
                    "Contracts",
                    "Listed paths, median",
                    "Packet bytes, median",
                ),
                [
                    (
                        g,
                        pct(u[g]["docs"]),
                        num(med(u[g]["docs_files"])),
                        kb(med(u[g]["docs_bytes"])),
                        num(med(u[g]["manifests"])),
                        pct(u[g]["no_manifest"]),
                        pct(u[g]["contracts"]),
                        num(med(u[g]["paths"])),
                        kb(med(u[g]["packet_bytes"])),
                    )
                    for g in GROUPS
                ],
            ),
        )
    )
    out.append(
        (
            "Contract types (share of snapshots with at least one)",
            table(
                ("Group", *sorted({k for g in GROUPS for k in u[g]["contract_kinds"]})),
                [
                    (
                        g,
                        *(
                            pct(u[g]["contract_kinds"].get(k, 0.0))
                            for k in sorted({k for g in GROUPS for k in u[g]["contract_kinds"]})
                        ),
                    )
                    for g in GROUPS
                ],
            ),
        )
    )
    sizes = p["by_size"]
    out.append(
        (
            "Decoys by universe size (each case's universe, pooled over cases)",
            table(
                (
                    "Size",
                    "Decoy rows",
                    "Context / same-owner / frame",
                    "README tokens, median",
                    "Over 512 bge",
                    "Docs",
                    "Doc bytes, median",
                    "Manifests, median",
                    "No manifest",
                    "Contracts",
                    "Non-English",
                    "Badge- or link-only",
                ),
                [
                    (
                        f"U{z}",
                        f"{d['all_decoys']['n']:,}",
                        " / ".join(pct(d["mix"][g]) for g in ("wideswe", "same_owner", "frame")),
                        num(med(d["all_decoys"]["readme_tokens"])),
                        pct(d["all_decoys"]["readme_over_512_bge"]),
                        pct(d["all_decoys"]["docs"]),
                        kb(med(d["all_decoys"]["docs_bytes"])),
                        num(med(d["all_decoys"]["manifests"])),
                        pct(d["all_decoys"]["no_manifest"]),
                        pct(d["all_decoys"]["contracts"]),
                        pct(d["all_decoys"]["non_english"]),
                        pct(d["all_decoys"]["badge_link_only"]),
                    )
                    for z, d in sizes.items()
                ],
            ),
        )
    )
    out.append(
        (
            "Per case: is the target median README shorter than the decoys'?",
            table(
                ("Size", "Cases", "Target median below decoy median", "Below context median"),
                [
                    (
                        f"U{z}",
                        str(d["cases"]),
                        pct(d["target_median_below_decoy_median"]),
                        pct(d["target_median_below_context_median"]),
                    )
                    for z, d in p["paired_readme_tokens"].items()
                ],
            ),
        )
    )
    split_cols = (
        "Snapshots",
        "README",
        "README tokens, median",
        "Thin",
        "Non-English",
        "Docs",
        "Doc bytes, median",
        "Manifests, median",
        "Contracts",
    )

    def split_row(name: str, d: dict) -> tuple[str, ...]:
        return (
            name,
            f"{d['n']:,}",
            pct(d["readme"]),
            num(d["readme_tokens_median"]),
            pct(d["thin"]),
            pct(d["non_english"]),
            pct(d["docs"]),
            kb(d["docs_bytes_median"]),
            num(d["manifests_median"]),
            pct(d["contracts"]),
        )

    out.append(
        (
            "By ecosystem (dev cases' targets, context and same-owner decoys)",
            table(
                ("Ecosystem, group", *split_cols),
                [
                    split_row(f"{e}, {g}", d)
                    for e, gs in p["by_ecosystem"].items()
                    for g in ("gold", "wideswe", "same_owner")
                    if (d := gs.get(g))
                ],
            ),
        )
    )
    out.append(
        (
            "Frame decoys by primary language",
            table(
                ("Language", *split_cols),
                [split_row(k, d) for k, d in p["frame_by_language"].items()],
            ),
        )
    )
    out.append(
        (
            "Targets by primary language",
            table(
                ("Language", *split_cols),
                [split_row(k, d) for k, d in p["gold_by_language"].items()],
            ),
        )
    )
    return out


def family_table(fams: dict, keep: Callable[[str], bool]) -> str:
    rows = []
    for name, f in fams.items():
        if not keep(name):
            continue
        counts = f.get("misses") or {}
        extra = (
            " / ".join(str(counts.get(t, 0)) for t in TIERS)
            if counts
            else "gains "
            + " / ".join(str(f["gains"][t]) for t in TIERS)
            + "; losses "
            + " / ".join(str(f["losses"][t]) for t in TIERS)
        )
        rows.append(
            (name, str(f["cells"]), f"{f['gap_high_minus_low']:+.3f}", f"{f['p']:.3f}", extra)
        )
    return table(
        (
            "Family | names",
            "Cells",
            "CR@10, high minus low",
            "Permutation p",
            "Misses low / mid / high (case-cells)",
        ),
        rows,
    )


def stratify_tables(s: dict, cell_sizes: Sequence[int]) -> list[tuple[str, str]]:
    out = []
    ranges = s["tier_score_ranges"]
    out.append(
        (
            "Tiers",
            table(
                ("Tier", "Cases", "Score range"),
                [
                    (t, str(s["tier_sizes"][t]), f"{ranges[t][0]:.4f} to {ranges[t][1]:.4f}")
                    for t in TIERS
                ],
            ),
        )
    )
    comp = s["tier_composition"]
    out.append(
        (
            "Tier composition",
            table(
                ("Tier", "Ecosystems", "Category"),
                [
                    (
                        t,
                        ", ".join(
                            f"{k} {v}"
                            for k, v in sorted(comp["ecosystem"][t].items(), key=lambda kv: -kv[1])
                        ),
                        ", ".join(f"{k} {v}" for k, v in comp["category"][t].items()),
                    )
                    for t in TIERS
                ],
            ),
        )
    )
    cases = s["cases"]
    comps = list(next(iter(cases.values()))["components"])
    by = {t: [c for c in cases.values() if c["tier"] == t] for t in TIERS}
    out.append(
        (
            "Mean component score by tier (weakest target)",
            table(
                ("Component", *TIERS),
                [
                    (
                        k,
                        *(
                            f"{sum(c['components'][k] for c in by[t]) / len(by[t]):.2f}"
                            for t in TIERS
                        ),
                    )
                    for k in comps
                ],
            ),
        )
    )
    flags = list(next(iter(cases.values()))["flags"])
    out.append(
        (
            "Cases where any target has the flag",
            table(
                ("Flag", "Cases", *TIERS),
                [
                    (
                        f,
                        str(sum(c["flags"][f] for c in cases.values())),
                        *(str(sum(c["flags"][f] for c in by[t])) for t in TIERS),
                    )
                    for f in flags
                ],
            ),
        )
    )
    fams = s["families"]
    out.append(
        ("Agents: high minus low tier", family_table(fams, lambda n: not n.startswith("static")))
    )
    out.append(
        (
            "Static rankers: high minus low tier",
            family_table(fams, lambda n: n.startswith("static") and "gain over" not in n),
        )
    )
    out.append(
        (
            "Static rankers: gain over names only (L0), high minus low tier",
            family_table(fams, lambda n: "gain over" in n),
        )
    )
    rows = []
    for c in s["cells"].values():
        if c["size"] in cell_sizes and "gain over" not in c["family"]:
            rows.append(
                (
                    c["family"],
                    c["names"],
                    c["level"],
                    f"U{c['size']}",
                    f"{c['all']:.3f}",
                    *(f"{c['tiers'][t]:.3f}" for t in TIERS),
                )
            )
    rows.sort(key=lambda r: (r[0], r[1], r[2], int(r[3][1:])))
    out.append(
        (
            "CR@10 by tier, per cell",
            table(("Family", "Names", "Level", "Size", "All 61", *TIERS), rows),
        )
    )
    wanted = (
        "no README",
        "no docs",
        "docs under 10 KB",
        "one manifest or none",
        "README under 100 tokens",
        "README over 1,000 tokens",
    )
    rows = []
    for name, f in fams.items():
        if (
            "gain over" in name
            or "names only" in name
            or (name.endswith("| hidden") and "agent" not in name)
        ):
            continue
        fl = f.get("flags", {})
        rows.append(
            (
                name,
                *(
                    f"{fl[w]['with']:.2f} vs {fl[w]['without']:.2f}"
                    if "with" in fl.get(w, {})
                    else "—"
                    for w in wanted
                ),
            )
        )
    counts = {w: next(iter(fams.values()))["flags"][w]["cases"] for w in wanted}
    out.append(
        (
            "CR@10 with vs without each flag (mean over a family's cells)",
            table(("Family | names", *(f"{w} ({counts[w]} cases)" for w in wanted)), rows),
        )
    )
    conf = s["confound"]
    feats = list(next(iter(conf.values()))["auc"])
    out.append(
        (
            "Target vs decoy AUC",
            table(
                (
                    "Feature",
                    *(f"U{z} all decoys" for z in conf),
                    "U5000 same-owner",
                    "Context (any size)",
                ),
                [
                    (
                        f,
                        *(f"{conf[z]['auc'][f]['all decoys']:.2f}" for z in conf),
                        f"{conf['5000']['auc'][f]['same_owner']:.2f}",
                        f"{conf['250']['auc'][f]['wideswe']:.2f}",
                    )
                    for f in feats
                ],
            ),
        )
    )
    shortcuts = list(next(iter(conf.values()))["shortcut_cr10"])
    out.append(
        (
            "Shortcut rankers, CR@10",
            table(
                ("Ordering", *(f"U{z}" for z in conf)),
                [(k, *(f"{conf[z]['shortcut_cr10'][k]:.3f}" for z in conf)) for k in shortcuts]
                + [("random (expected)", *(f"{conf[z]['random_cr10']:.2g}" for z in conf))],
            ),
        )
    )
    rows = []
    for name, d in s["displacers"].items():
        t = d["readme_tokens_median"]
        o = d["readme_over_1000"]
        rows.append(
            (
                name,
                str(d["cases"]),
                str(d["top10_decoys"]),
                num(t["top10 decoys"]),
                num(t["targets"]),
                num(t["all decoys"]),
                pct(o["top10 decoys"]),
                pct(o["targets"]),
                pct(o["all decoys"]),
            )
        )
    out.append(
        (
            "Decoys ranked above a missed target: README analyzed tokens",
            table(
                (
                    "Cell",
                    "Cases with a miss",
                    "Top-10 decoys",
                    "Median, top-10 decoys",
                    "Median, targets",
                    "Median, all decoys",
                    "Over 1,000, top-10 decoys",
                    "Over 1,000, targets",
                    "Over 1,000, all decoys",
                ),
                rows,
            ),
        )
    )
    rows = []
    for name, row in s["matched"].items():
        docs, size = (row[k] for k in row if k != "all decoys")
        rows.append(
            (
                name,
                f"{row['all decoys']:.3f}",
                f"{docs['decoys_kept']:.2f}",
                f"{docs['cr10']:.3f}",
                f"{docs['random_same_count']:.3f}",
                f"{docs['cr10'] - docs['random_same_count']:+.3f}",
                f"{size['cr10']:.3f}",
                f"{size['random_same_count']:.3f}",
                str(size["short"]),
            )
        )
    out.append(
        (
            "Matched decoy pools, CR@10",
            table(
                (
                    "Cell",
                    "All decoys",
                    "Documented share kept",
                    "Documented decoys only",
                    "Random pool, same count",
                    "Documented minus random",
                    "Decoys at least target-sized",
                    "Random, same count",
                    "Cases short of 10",
                ),
                rows,
            ),
        )
    )
    return out


def drops_table(rows: Sequence[dict]) -> str:
    return table(
        (
            "Variant",
            "Level",
            "Ranker",
            "Size",
            "Base CR@10",
            "Degraded",
            "Change",
            "Lost",
            "Gained",
            "Rankings differ",
        ),
        [
            (
                r["variant"],
                r["level"],
                r["ranker"],
                f"U{r['size']}",
                f"{r['base']:.3f}",
                f"{r['degraded']:.3f}",
                f"{-r['drop']:+.3f}",
                str(len(r["lost"])),
                str(len(r["gained"])),
                str(r["rankings_differ"]),
            )
            for r in rows
        ],
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--stratify", type=Path)
    parser.add_argument("--drops", type=Path, nargs="*", default=[])
    parser.add_argument("--cell-sizes", type=int, nargs="+", default=[250, 1000, 5000, 10000])
    args = parser.parse_args(argv)
    sections = []
    if args.profile:
        sections += profile_tables(json.loads(args.profile.read_text()))
    if args.stratify:
        sections += stratify_tables(json.loads(args.stratify.read_text()), args.cell_sizes)
    drops = [r for path in args.drops for r in json.loads(path.read_text())]
    if drops:
        order = {"no-docs": 0, "no-readme": 1, "readme-cap": 2}
        levels = {"L3": 0, "L3+docs": 1, "L4": 2}
        drops.sort(
            key=lambda r: (
                order.get(r["variant"], 9),
                levels.get(r["level"], 9),
                r["ranker"],
                r["size"],
            )
        )
        sections.append(("Ranker degradation, real names", drops_table(drops)))
    for title, body in sections:
        print(f"### {title}\n\n{body}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
