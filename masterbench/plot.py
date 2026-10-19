# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "matplotlib",
#     "seaborn",
#     "numpy",
#     "pandas",
# ]
# ///
"""Plots for the masterbench experiment (Mooncake master capacity, E1)."""

import json
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from matplotlib.ticker import FuncFormatter, LogLocator, NullFormatter


def main():
    """Make Plot A (capacity) and Plot B (latency) from masterbench results.

    Parameters
    ----------
    sys.argv[1:] : str
        One or more result directories; every ``results.jsonl`` below them is read.
        The first directory also receives the outputs.

    Saves
    -----
    <first dir>/figures/plot_a_capacity.{pdf,png}
    <first dir>/figures/plot_b_latency.{pdf,png}
    <first dir>/figures/summary.csv and summary.txt
    """
    # --- Data ---
    roots = [Path(p) for p in sys.argv[1:]] or [Path(".")]
    rows = []
    for root in roots:
        for path in sorted(root.rglob("results.jsonl")):
            for line in path.read_text().splitlines():
                if line.strip():
                    rows.append(json.loads(line))
    if not rows:
        sys.exit("no results.jsonl found under " + ", ".join(map(str, roots)))
    df = pd.DataFrame(rows).dropna(subset=["keys_per_s", "lat_p50_ms"])
    df["op"] = df["operation"].map({"BatchGet": "Lookups", "BatchPut": "Writes"})
    keys_requested = df["keys_ok"] + df["keys_not_found"] + df["keys_err"]
    df["valid"] = (df["keys_not_found"] == 0) & (df["keys_err"] == 0) & (keys_requested > 0)

    good = df[df["valid"]]
    agg = (
        good.groupby(["op", "num_clients"])
        .agg(
            keys_per_s=("keys_per_s", "median"),
            keys_min=("keys_per_s", "min"),
            keys_max=("keys_per_s", "max"),
            p50_ms=("lat_p50_ms", "median"),
            p99_ms=("lat_p99_ms", "median"),
            master_cpu_pct=("master_cpu_pct", "median"),
            client_cpu_pct=("client_cpu_pct", "median"),
            runs=("keys_per_s", "size"),
        )
        .reset_index()
    )
    agg["spread_pct"] = 100 * (agg["keys_max"] - agg["keys_min"]) / agg["keys_per_s"]

    out = roots[0] / "figures"
    out.mkdir(parents=True, exist_ok=True)
    agg.to_csv(out / "summary.csv", index=False)

    # --- Style Setup ---
    sns.set_theme(font_scale=1.0, style="whitegrid", font="DejaVu Sans")
    styles = {
        "Lookups": dict(color="#4575b4", marker="o", linestyle="-"),
        "Writes": dict(color="#d73027", marker="s", linestyle="--"),
    }
    demand = [
        (4.5e3, "Kimi-scale demand, 256-token blocks (~4.5k keys/s)"),
        (1.45e5, "Kimi-scale demand, 64-token pages x 8 GPUs (~145k keys/s)"),
    ]

    def si(value, _pos=None):
        for div, suffix in ((1e9, "G"), (1e6, "M"), (1e3, "k")):
            if value >= div:
                return f"{value / div:.3g}{suffix}"
        return f"{value:.3g}"

    caption = (
        "One master pinned to 16 cores (BlueField-3 core count; Xeon cores, so an upper bound). "
        "128 keys per request, 10M keys preloaded,\nclients and master on the same node "
        "(network round trip excluded). Each client sends one request at a time. "
        "Median of 3 runs; bars show min-max."
    )

    # --- Plot A: capacity ---
    fig, ax = plt.subplots(figsize=(10, 6), dpi=150)
    for op, sub in agg.groupby("op"):
        sub = sub.sort_values("num_clients")
        st = styles[op]
        ax.errorbar(
            sub["num_clients"], sub["keys_per_s"],
            yerr=[sub["keys_per_s"] - sub["keys_min"], sub["keys_max"] - sub["keys_per_s"]],
            color=st["color"], marker=st["marker"], linestyle=st["linestyle"],
            linewidth=2, markersize=6, capsize=3, elinewidth=1, label=op, zorder=3,
        )
        peak = sub.loc[sub["keys_per_s"].idxmax()]
        cores = peak["master_cpu_pct"] / 100 if peak["master_cpu_pct"] > 0 else np.nan
        per_core = f", {si(peak['keys_per_s'] / cores)} per busy core" if cores == cores else ""
        headroom = peak["keys_per_s"] / demand[-1][0]
        last = sub.iloc[-1]
        ax.annotate(
            f"{op}: peak {si(peak['keys_per_s'])} keys/s{per_core}, "
            f"{headroom:.1f}x the page-level demand",
            xy=(last["num_clients"], last["keys_per_s"]),
            xytext=(0, 10 if op == "Lookups" else -16), textcoords="offset points",
            ha="right", va="bottom" if op == "Lookups" else "top",
            fontsize=10, color="dimgrey", weight="semibold",
        )
    for level, text in demand:
        ax.axhline(level, color="grey", linestyle=":", linewidth=1.2, zorder=1)
        ax.text(1, level * 1.12, text, fontsize=9, color="dimgrey", ha="left", va="bottom")

    ax.set_xscale("log", base=2)
    ax.set_xticks(sorted(agg["num_clients"].unique()))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _p: f"{v:g}"))
    ax.xaxis.set_minor_formatter(NullFormatter())
    ax.set_yscale("log")
    ax.yaxis.set_major_locator(LogLocator(base=10))
    ax.yaxis.set_major_formatter(FuncFormatter(si))
    ymax = max(agg["keys_max"].max(), demand[-1][0]) * 3
    ax.set_ylim(min(agg["keys_min"].min(), demand[0][0]) / 3, ymax)
    ax.set_xlabel("Concurrent clients", fontsize=12, labelpad=8, color="dimgrey")
    ax.set_ylabel("Keys served per second", fontsize=12, labelpad=8, color="dimgrey")
    ax.set_title("How much can one Mooncake master handle?", fontsize=14, loc="left",
                 pad=7, color="dimgrey")
    ax.legend(loc="lower right", fontsize=10, frameon=True, facecolor="white",
              framealpha=0.8, edgecolor="lightgrey", labelcolor="dimgrey")
    ax.tick_params(axis="both", which="both", length=0, labelcolor="dimgrey")
    ax.grid(False)
    ax.grid(alpha=0.5, linewidth=0.8, axis="y", which="major")
    sns.despine(left=True, bottom=True)
    fig.text(0.01, -0.06, caption, fontsize=8.5, color="dimgrey", ha="left", va="top")

    # --- Save ---
    plt.savefig(out / "plot_a_capacity.pdf", dpi=150, bbox_inches="tight")
    plt.savefig(out / "plot_a_capacity.png", dpi=150, bbox_inches="tight")
    plt.close(fig)

    # --- Plot B: latency vs load ---
    fig, ax = plt.subplots(figsize=(10, 6), dpi=150)
    ax.axhspan(0.02, 0.1, color="lightgrey", alpha=0.35, zorder=0, linewidth=0)
    ax.text(agg["keys_per_s"].min(), 0.1 * 1.08,
            "typical datacenter network round trip (0.02-0.1 ms, not measured here)",
            fontsize=9, color="dimgrey", ha="left", va="bottom")
    for op, sub in agg.groupby("op"):
        sub = sub.sort_values("num_clients")
        st = styles[op]
        ax.plot(sub["keys_per_s"], sub["p50_ms"], color=st["color"], marker=st["marker"],
                linestyle="-", linewidth=2, markersize=6, label=f"{op}, median", zorder=3)
        ax.plot(sub["keys_per_s"], sub["p99_ms"], color=st["color"], marker=st["marker"],
                linestyle=":", linewidth=1.5, markersize=6, markerfacecolor="white",
                label=f"{op}, 99th percentile", zorder=3)
        for _, row in sub.iloc[[0, -1]].iterrows():
            ax.annotate(f"{int(row['num_clients'])} client{'s' if row['num_clients'] > 1 else ''}",
                        xy=(row["keys_per_s"], row["p99_ms"]), xytext=(6, 6),
                        textcoords="offset points", fontsize=8.5, color="dimgrey")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.xaxis.set_major_locator(LogLocator(base=10, subs=(1.0, 2.0, 5.0)))
    ax.xaxis.set_major_formatter(FuncFormatter(si))
    ax.xaxis.set_minor_formatter(NullFormatter())
    ax.yaxis.set_minor_formatter(NullFormatter())
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _p: f"{v:g}"))
    ax.set_xlabel("Keys served per second", fontsize=12, labelpad=8, color="dimgrey")
    ax.set_ylabel("Request latency (ms, 128 keys per request)", fontsize=12, labelpad=8,
                  color="dimgrey")
    ax.set_title("When does the master get slow?", fontsize=14, loc="left", pad=7,
                 color="dimgrey")
    ax.legend(loc="upper left", fontsize=9.5, frameon=True, facecolor="white",
              framealpha=0.8, edgecolor="lightgrey", labelcolor="dimgrey")
    ax.tick_params(axis="both", which="both", length=0, labelcolor="dimgrey")
    ax.grid(False)
    ax.grid(alpha=0.5, linewidth=0.8, axis="y", which="major")
    sns.despine(left=True, bottom=True)
    fig.text(0.01, -0.06, caption.replace("Median of 3 runs; bars show min-max.",
                                          "Each point is one client count (median of 3 runs)."),
             fontsize=8.5, color="dimgrey", ha="left", va="top")

    # --- Save ---
    plt.savefig(out / "plot_b_latency.pdf", dpi=150, bbox_inches="tight")
    plt.savefig(out / "plot_b_latency.png", dpi=150, bbox_inches="tight")
    plt.close(fig)

    # --- Summary text ---
    lines = []
    for op, sub in agg.groupby("op"):
        peak = sub.loc[sub["keys_per_s"].idxmax()]
        cores = peak["master_cpu_pct"] / 100
        lines.append(
            f"{op}: peak {peak['keys_per_s']:,.0f} keys/s at {int(peak['num_clients'])} clients; "
            f"master CPU {peak['master_cpu_pct']:.0f}% ({peak['keys_per_s'] / cores:,.0f} keys/s per busy core); "
            f"client CPU {peak['client_cpu_pct']:.0f}%; p50 {peak['p50_ms']:.3f} ms, p99 {peak['p99_ms']:.3f} ms; "
            f"max repeat spread {sub['spread_pct'].max():.1f}%"
        )
    prefill = df[df["master_rss_after_prefill"] > 0]
    if not prefill.empty:
        row = prefill.iloc[0]
        lines.append(
            f"Master memory after preloading {int(row['prefill_objects']):,} keys: "
            f"{row['master_rss_after_prefill'] / 2**30:.2f} GiB "
            f"({row['master_rss_after_prefill'] / row['prefill_objects']:.0f} bytes per key, process total)"
        )
    bad = df[~df["valid"]]
    lines.append(f"Runs: {len(df)} total, {len(bad)} invalid (lookups not found or errors)")
    for _, row in bad.iterrows():
        lines.append(f"  invalid: {row['op']} clients={row['num_clients']} repeat={row['repeat']} "
                     f"not_found={row['keys_not_found']} err={row['keys_err']}")
    wide = agg[agg["spread_pct"] > 10]
    for _, row in wide.iterrows():
        lines.append(f"  spread >10%: {row['op']} clients={row['num_clients']} ({row['spread_pct']:.1f}%)")
    (out / "summary.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
