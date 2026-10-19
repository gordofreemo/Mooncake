# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "matplotlib",
#     "numpy",
#     "pandas",
# ]
# ///
"""Paper figure for masterbench E2: Mooncake master vs. number of storage clients.

Same conventions as plot.py: column-width panels, 7-8 pt text, no in-plot prose,
TrueType-embedded fonts; the caption is written to a .tex file.
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

CAPTION = r"""\caption{Effect of the number of storage clients on a single Mooncake master.
Each storage client mounts one segment and sends a heartbeat every second; the total
pool size is fixed, so only the number of segments changes. Sixteen active clients issue
128-key requests (one outstanding each); 1M keys are preloaded.
(a)~Keys served per second. (b)~Median (solid) and 99th-percentile (dashed) request latency.
(c)~Master resident memory after preloading.
The master runs on 16 cores of an AMD EPYC~9654; points are medians of three 60\,s runs,
bars show the minimum and maximum.}
\label{fig:master-fanin}
"""


def si(value, _pos=None):
    for div, suffix in ((1e9, "G"), (1e6, "M"), (1e3, "K")):
        if value >= div:
            return f"{value / div:g}{suffix}"
    return f"{value:g}"


def setup_x(ax, counts):
    ax.set_xscale("log", base=2)
    ax.xaxis.set_major_locator(FixedLocator(counts))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _p: f"{int(v)}"))
    ax.xaxis.set_minor_formatter(NullFormatter())
    ax.set_xlabel("Storage clients")


def draw_throughput(ax, agg, counts):
    for op, sub in agg.groupby("op"):
        sub = sub.sort_values("stores")
        ax.errorbar(sub["stores"], sub["keys_per_s"],
                    yerr=[sub["keys_per_s"] - sub["keys_min"], sub["keys_max"] - sub["keys_per_s"]],
                    capsize=1.5, elinewidth=0.6, capthick=0.6, **STYLE[op])
    setup_x(ax, counts)
    ax.set_yscale("log")
    ax.yaxis.set_major_locator(LogLocator(base=10))
    ax.yaxis.set_major_formatter(FuncFormatter(si))
    ax.set_ylabel("Throughput (keys/s)")


def draw_latency(ax, agg, counts):
    for op, sub in agg.groupby("op"):
        sub = sub.sort_values("stores")
        ax.plot(sub["stores"], sub["p50_ms"], linestyle="-", **STYLE[op])
        ax.plot(sub["stores"], sub["p99_ms"], linestyle="--", markerfacecolor="white", **STYLE[op])
    setup_x(ax, counts)
    ax.set_yscale("log")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _p: f"{v:g}"))
    ax.yaxis.set_minor_formatter(NullFormatter())
    ax.set_ylabel("Latency (ms)")
    keys = [Line2D([0], [0], color="0.2", linestyle="-", label="p50"),
            Line2D([0], [0], color="0.2", linestyle="--", label="p99")]
    ax.legend(handles=keys, loc="upper left", frameon=False, handlelength=2.2,
              borderaxespad=0.3, labelspacing=0.3)


def draw_memory(ax, mem, counts):
    ax.plot(mem["stores"], mem["rss_gib"], color="0.25", marker="D", markersize=3)
    setup_x(ax, counts)
    ax.set_yscale("log")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _p: f"{v:g}"))
    ax.yaxis.set_minor_formatter(NullFormatter())
    ax.set_ylabel("Master memory (GiB)")


def main():
    """Make the E2 fan-in figure from masterbench results.

    Parameters
    ----------
    sys.argv[1:] : str
        Result directories; every ``results.jsonl`` below them is read. The first
        directory also receives the outputs.

    Saves
    -----
    <first dir>/figures/fig_fanin.{pdf,png}       three panels, figure* width
    <first dir>/figures/fig_fanin_caption.tex     LaTeX caption
    <first dir>/figures/fanin_summary.{csv,txt}   numbers behind the figure
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
    df["stores"] = df["num_segments"]
    requested = df["keys_ok"] + df["keys_not_found"] + df["keys_err"]
    df["valid"] = (df["keys_not_found"] == 0) & (df["keys_err"] == 0) & (requested > 0)
    good = df[df["valid"]]
    agg = (
        good.groupby(["op", "stores"])
        .agg(keys_per_s=("keys_per_s", "median"), keys_min=("keys_per_s", "min"),
             keys_max=("keys_per_s", "max"), p50_ms=("lat_p50_ms", "median"),
             p99_ms=("lat_p99_ms", "median"), master_cpu_pct=("master_cpu_pct", "median"),
             runs=("keys_per_s", "size"))
        .reset_index()
    )
    agg["cpu_us_per_key"] = agg["master_cpu_pct"] / 100 / agg["keys_per_s"] * 1e6
    mem = (good.groupby("stores")["master_rss_after_prefill"].median() / 2**30).rename("rss_gib").reset_index()
    counts = sorted(df["stores"].unique())
    out = roots[0] / "figures"
    out.mkdir(parents=True, exist_ok=True)
    agg.to_csv(out / "fanin_summary.csv", index=False)

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

    # --- Plot ---
    fig, axes = plt.subplots(1, 3, figsize=(7.0, 1.8))
    fig.subplots_adjust(wspace=0.42)
    draw_throughput(axes[0], agg, counts)
    draw_latency(axes[1], agg, counts)
    draw_memory(axes[2], mem, counts)
    for ax, label in zip(axes, ("(a) Throughput", "(b) Latency", "(c) Master memory")):
        ax.text(0.5, -0.36, label, transform=ax.transAxes, ha="center", va="top", fontsize=8)
    handles = [Line2D([0], [0], linestyle="-", label=op, **st) for op, st in STYLE.items()]
    fig.legend(handles=handles, loc="lower center", bbox_to_anchor=(0.5, 0.93), ncol=2,
               frameon=False, handlelength=2.2, columnspacing=1.5, borderaxespad=0)

    # --- Save ---
    for ext in ("pdf", "png"):
        fig.savefig(out / f"fig_fanin.{ext}", dpi=300)
    plt.close(fig)
    (out / "fig_fanin_caption.tex").write_text(CAPTION)

    # --- Summary (numbers for the text) ---
    lines = []
    for op, sub in agg.groupby("op"):
        sub = sub.sort_values("stores")
        first, last = sub.iloc[0], sub.iloc[-1]
        lines.append(
            f"{op}: {first['keys_per_s']:,.0f} keys/s at {int(first['stores'])} stores -> "
            f"{last['keys_per_s']:,.0f} at {int(last['stores'])} "
            f"({first['keys_per_s'] / last['keys_per_s']:.1f}x drop); master CPU per key "
            f"{first['cpu_us_per_key']:.2f} -> {last['cpu_us_per_key']:.2f} us"
        )
    if len(mem) > 1:
        slope = (mem["rss_gib"].iloc[-1] - mem["rss_gib"].iloc[0]) * 2**30 / (mem["stores"].iloc[-1] - mem["stores"].iloc[0])
        lines.append(f"Master memory: {mem['rss_gib'].iloc[0]:.2f} -> {mem['rss_gib'].iloc[-1]:.2f} GiB "
                     f"(~{slope / 2**20:.2f} MiB per storage client)")
    bad = df[~df["valid"]]
    lines.append(f"Runs: {len(df)} total, {len(bad)} invalid")
    (out / "fanin_summary.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
