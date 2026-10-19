# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "matplotlib",
#     "numpy",
#     "pandas",
# ]
# ///
"""Paper figures for the masterbench experiment (Mooncake master capacity, E1).

Styled for a USENIX/ACM systems paper: column-width panels, 7-8 pt text,
no in-plot prose (the LaTeX caption carries it), TrueType-embedded fonts.
"""

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.ticker import FixedLocator, FuncFormatter, LogLocator, NullFormatter  # noqa: E402

STYLE = {
    "Get": dict(color="#4575b4", marker="o"),
    "Put": dict(color="#d73027", marker="s"),
}
DEMAND = 1.45e5  # estimated keys/s, 64-token pages x 8 GPUs at Kimi scale

CAPTION = r"""\caption{Throughput and latency of a single Mooncake master.
(a)~Keys served per second as the number of concurrent clients grows.
(b)~Median (solid) and 99th-percentile (dashed) request latency against throughput;
each point is one client count.
The master runs on 16 cores of an AMD EPYC~9654 (the core count of a BlueField-3 DPU);
clients run on the same node, each with one outstanding request of 128 keys.
10M keys are preloaded. Get is \texttt{BatchGetReplicaList}; Put is
\texttt{BatchPutStart} followed by \texttt{BatchPutEnd}.
Points are medians of three 60\,s runs; bars show the minimum and maximum.
The dashed line marks the estimated metadata demand at Kimi scale
(64-token pages over 8 GPUs, 145K keys/s).}
\label{fig:master-capacity}
"""


def si(value, _pos=None):
    for div, suffix in ((1e9, "G"), (1e6, "M"), (1e3, "K")):
        if value >= div:
            return f"{value / div:g}{suffix}"
    return f"{value:g}"


def draw_throughput(ax, agg):
    for op, sub in agg.groupby("op"):
        sub = sub.sort_values("num_clients")
        ax.errorbar(
            sub["num_clients"], sub["keys_per_s"],
            yerr=[sub["keys_per_s"] - sub["keys_min"], sub["keys_max"] - sub["keys_per_s"]],
            capsize=1.5, elinewidth=0.6, capthick=0.6, label=op, **STYLE[op],
        )
    ax.axhline(DEMAND, color="0.35", linestyle="--", linewidth=0.7, zorder=1)
    ax.text(1.08, DEMAND * 0.8, "est. demand", fontsize=6.5, color="0.3", va="top")
    ax.set_xscale("log", base=2)
    ax.xaxis.set_major_locator(FixedLocator(sorted(agg["num_clients"].unique())))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _p: f"{v:g}"))
    ax.xaxis.set_minor_formatter(NullFormatter())
    ax.set_yscale("log")
    ax.yaxis.set_major_locator(LogLocator(base=10))
    ax.yaxis.set_major_formatter(FuncFormatter(si))
    ax.set_ylim(1e4, 1e7)
    ax.set_xlabel("Clients")
    ax.set_ylabel("Throughput (keys/s)")


def draw_latency(ax, agg):
    for op, sub in agg.groupby("op"):
        sub = sub.sort_values("num_clients")
        st = STYLE[op]
        ax.plot(sub["keys_per_s"], sub["p50_ms"], linestyle="-", **st)
        ax.plot(sub["keys_per_s"], sub["p99_ms"], linestyle="--", markerfacecolor="white", **st)
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.xaxis.set_major_locator(LogLocator(base=10))
    ax.xaxis.set_major_formatter(FuncFormatter(si))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _p: f"{v:g}"))
    ax.xaxis.set_minor_formatter(NullFormatter())
    ax.yaxis.set_minor_formatter(NullFormatter())
    ax.set_xlim(1e4, 1e7)
    ax.set_xlabel("Throughput (keys/s)")
    ax.set_ylabel("Latency (ms)")
    keys = [Line2D([0], [0], color="0.2", linestyle="-", label="p50"),
            Line2D([0], [0], color="0.2", linestyle="--", label="p99")]
    ax.legend(handles=keys, loc="upper left", frameon=False, handlelength=2.2,
              borderaxespad=0.3, labelspacing=0.3)


def op_legend(fig, y):
    handles = [Line2D([0], [0], linestyle="-", label=op, **st) for op, st in STYLE.items()]
    fig.legend(handles=handles, loc="lower center", bbox_to_anchor=(0.5, y), ncol=len(handles),
               frameon=False, handlelength=2.2, columnspacing=1.5, borderaxespad=0)


def main():
    """Make the paper figures from masterbench results.

    Parameters
    ----------
    sys.argv[1:] : str
        Result directories; every ``results.jsonl`` below them is read. The first
        directory also receives the outputs.

    Saves
    -----
    <first dir>/figures/fig_master.{pdf,png}            two panels, figure* width
    <first dir>/figures/fig_master_throughput.{pdf,png} panel (a), column width
    <first dir>/figures/fig_master_latency.{pdf,png}    panel (b), column width
    <first dir>/figures/fig_master_caption.tex          LaTeX caption
    <first dir>/figures/summary.{csv,txt}               numbers behind the figures
    """
    # --- Data ---
    roots = [Path(p) for p in sys.argv[1:]] or [Path(".")]
    rows = []
    for root in roots:
        for path in sorted(root.rglob("results.jsonl")):
            rows += [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if not rows:
        sys.exit("no results.jsonl found under " + ", ".join(map(str, roots)))
    df = pd.DataFrame(rows).dropna(subset=["keys_per_s", "lat_p50_ms"])
    df["op"] = df["operation"].map({"BatchGet": "Get", "BatchPut": "Put"})
    requested = df["keys_ok"] + df["keys_not_found"] + df["keys_err"]
    df["valid"] = (df["keys_not_found"] == 0) & (df["keys_err"] == 0) & (requested > 0)
    agg = (
        df[df["valid"]]
        .groupby(["op", "num_clients"])
        .agg(keys_per_s=("keys_per_s", "median"), keys_min=("keys_per_s", "min"),
             keys_max=("keys_per_s", "max"), p50_ms=("lat_p50_ms", "median"),
             p99_ms=("lat_p99_ms", "median"), master_cpu_pct=("master_cpu_pct", "median"),
             client_cpu_pct=("client_cpu_pct", "median"), runs=("keys_per_s", "size"))
        .reset_index()
    )
    agg["spread_pct"] = 100 * (agg["keys_max"] - agg["keys_min"]) / agg["keys_per_s"]
    out = roots[0] / "figures"
    out.mkdir(parents=True, exist_ok=True)
    agg.to_csv(out / "summary.csv", index=False)

    # --- Style Setup ---
    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 8, "axes.labelsize": 8,
        "xtick.labelsize": 7, "ytick.labelsize": 7, "legend.fontsize": 7,
        "axes.linewidth": 0.6, "lines.linewidth": 1.1, "lines.markersize": 3.5,
        "lines.markeredgewidth": 0.8,
        "xtick.major.width": 0.6, "ytick.major.width": 0.6,
        "xtick.minor.width": 0.4, "ytick.minor.width": 0.4,
        "xtick.major.size": 2.5, "ytick.major.size": 2.5,
        "xtick.minor.size": 1.5, "ytick.minor.size": 1.5,
        "axes.grid": True, "axes.grid.which": "major", "grid.linestyle": ":",
        "grid.linewidth": 0.5, "grid.color": "0.7", "axes.axisbelow": True,
        "pdf.fonttype": 42, "ps.fonttype": 42,
        "savefig.bbox": "tight", "savefig.pad_inches": 0.02,
    })

    # --- Plot: two panels (figure*) ---
    fig, (ax_a, ax_b) = plt.subplots(1, 2, figsize=(7.0, 1.85))
    fig.subplots_adjust(wspace=0.32)
    draw_throughput(ax_a, agg)
    draw_latency(ax_b, agg)
    for ax, label in ((ax_a, "(a) Throughput"), (ax_b, "(b) Latency vs. throughput")):
        ax.text(0.5, -0.33, label, transform=ax.transAxes, ha="center", va="top", fontsize=8)
    op_legend(fig, 0.93)

    # --- Save ---
    for ext in ("pdf", "png"):
        fig.savefig(out / f"fig_master.{ext}", dpi=300)
    plt.close(fig)

    # --- Plot: single-column panels (for \subfloat) ---
    for name, draw in (("throughput", draw_throughput), ("latency", draw_latency)):
        fig, ax = plt.subplots(figsize=(3.33, 1.75))
        draw(ax, agg)
        op_legend(fig, 0.9)
        for ext in ("pdf", "png"):
            fig.savefig(out / f"fig_master_{name}.{ext}", dpi=300)
        plt.close(fig)
    (out / "fig_master_caption.tex").write_text(CAPTION)

    # --- Summary (numbers for the text, not drawn on the figure) ---
    lines = []
    for op, sub in agg.groupby("op"):
        peak = sub.loc[sub["keys_per_s"].idxmax()]
        cores = peak["master_cpu_pct"] / 100
        lines.append(
            f"{op}: peak {peak['keys_per_s']:,.0f} keys/s at {int(peak['num_clients'])} clients; "
            f"master CPU {peak['master_cpu_pct']:.0f}% ({peak['keys_per_s'] / cores:,.0f} keys/s per busy core); "
            f"client CPU {peak['client_cpu_pct']:.0f}%; p50 {peak['p50_ms']:.3f} ms, p99 {peak['p99_ms']:.3f} ms; "
            f"{peak['keys_per_s'] / DEMAND:.1f}x est. demand; max repeat spread {sub['spread_pct'].max():.1f}%"
        )
    prefill = df[df["master_rss_after_prefill"] > 0]
    if not prefill.empty:
        row = prefill.iloc[0]
        idle = []
        for root in roots:
            for path in root.rglob("master_rss_idle.txt"):
                idle += [int(float(t.split()[1])) for t in path.read_text().splitlines() if t.strip()]
        base = min(idle) if idle else 0
        lines.append(
            f"Master RSS: {row['master_rss_after_prefill'] / 2**30:.2f} GiB with "
            f"{int(row['prefill_objects']):,} keys, {base / 2**20:.0f} MiB idle => "
            f"{(row['master_rss_after_prefill'] - base) / row['prefill_objects']:.0f} B/key"
        )
    bad = df[~df["valid"]]
    lines.append(f"Runs: {len(df)} total, {len(bad)} invalid (not found or errors)")
    for _, row in agg[agg["spread_pct"] > 10].iterrows():
        lines.append(f"  spread >10%: {row['op']} clients={row['num_clients']} ({row['spread_pct']:.1f}%)")
    (out / "summary.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
