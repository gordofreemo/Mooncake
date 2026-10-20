# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "matplotlib",
#     "numpy",
#     "pandas",
# ]
# ///
"""Paper figures for masterbench E3 (full pool), E4 (CPU contention) and E5
(open-loop load).

Same conventions as plot.py: column-width panels, 7-8 pt text, no in-plot
prose, TrueType-embedded fonts; captions go to .tex files.
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.ticker import FixedLocator, FuncFormatter, NullFormatter  # noqa: E402

GET = dict(color="#4575b4", marker="o")
PUT = dict(color="#d73027", marker="s")
VARIANT = {
    "upstream": dict(color="#d73027", marker="s", label="Upstream master"),
    "patched": dict(color="#1a9850", marker="^", label="Patched master"),
}
EVICT_RE = re.compile(r"^I(\d{4}) (\d{2}:\d{2}:\d{2}\.\d+).*\[EVICT-(TRIGGER|DONE)\]")

CAPTION_TIMELINE = r"""\caption{A single Mooncake master with a full pool (E3, patched master).
Sixteen clients write new 1\,MiB objects in 128-key batches while sixteen clients
look up recently written keys; the pool starts at 85\% of capacity, so eviction
runs whenever usage passes the 90\% watermark. Rows: resident objects.
(a)~Keys written per second; dotted: writes rejected for lack of space.
(b)~Median and 99th-percentile lookup latency per second.
(c)~Master CPU. Vertical lines mark the start of each eviction round; rounds
run back to back. One run per row.}
\label{fig:master-evict-timeline}
"""
CAPTION_SUMMARY = r"""\caption{Full-pool operation of the upstream and patched master (E3).
(a)~Keys written per second over the 180\,s window. (b)~99th-percentile lookup
latency. (c)~Share of written keys rejected for lack of space. One run per point.}
\label{fig:master-evict-summary}
"""
CAPTION_OPEN = r"""\caption{Tail latency of a single Mooncake master under Poisson arrivals (E5).
Offered load is a fraction of the closed-loop capacity measured first on the same
node; latency is timed from the scheduled send, so it includes queueing. Lines:
99th-percentile latency of (a)~lookups and (b)~writes (patched master) with 64, 128
or 256 open-loop clients, 128 keys per call. Points are medians of three 60\,s runs.}
\label{fig:master-open-loop}
"""
CAPTION_CONTEND = r"""\caption{Lookup latency when other work shares the cores (E4).
Sixteen clients offer 5,000 lookups of 128 keys per second (Poisson); stress-ng
runs the given number of CPU-bound workers per core on (a)~the 16 client cores or
(b)~the master's 16 cores. Points are medians of three 30\,s runs.}
\label{fig:master-contend}
"""


def si(value, _pos=None):
    for div, suffix in ((1e9, "G"), (1e6, "M"), (1e3, "K")):
        if value >= div:
            return f"{value / div:g}{suffix}"
    return f"{value:g}"


def read_jsonl(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def variant_of(job_dir):
    env = job_dir / "env.txt"
    text = env.read_text() if env.exists() else ""
    return "patched" if "fast_put=1" in text else "upstream"


def eviction_rounds(log_path, year):
    """[start, end] epoch seconds of each eviction round in a master log."""
    if not log_path.exists():
        return []
    rounds, start = [], None
    for line in log_path.read_text(errors="replace").splitlines():
        m = EVICT_RE.match(line)
        if not m:
            continue
        stamp = datetime.strptime(f"{year}{m.group(1)} {m.group(2)}", "%Y%m%d %H:%M:%S.%f")
        t = stamp.replace(tzinfo=timezone.utc).timestamp()
        if m.group(3) == "TRIGGER":
            start = t
        elif start is not None:
            rounds.append((start, t))
            start = None
    return rounds


def resident_of(tag):
    m = re.search(r"_r(\d+)$", tag)
    return int(m.group(1)) if m else None


def style_axes():
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


def panel_label(ax, text, y=-0.42):
    ax.text(0.5, y, text, transform=ax.transAxes, ha="center", va="top", fontsize=8)


def save(fig, out, name, caption):
    for ext in ("pdf", "png"):
        fig.savefig(out / f"{name}.{ext}", dpi=300)
    plt.close(fig)
    (out / f"{name}_caption.tex").write_text(caption)


# --- E3 -----------------------------------------------------------------------

def load_e3(roots):
    timelines, results, rounds = [], [], {}
    for root in roots:
        for job in sorted(p.parent for p in root.rglob("timeline.jsonl")):
            variant = variant_of(job)
            tl = read_jsonl(job / "timeline.jsonl")
            for row in tl:
                row["variant"] = variant
            tl = [r for r in tl if r["tag"].startswith("evict_")]
            timelines += tl
            for row in read_jsonl(job / "results.jsonl"):
                if row["tag"].startswith("evict_"):
                    row["variant"] = variant
                    results.append(row)
            if tl:
                year = datetime.fromtimestamp(tl[0]["t"], timezone.utc).year
                for tag in {r["tag"] for r in tl}:
                    rounds[(variant, tag)] = eviction_rounds(job / f"master_{tag}.log", year)
    return pd.DataFrame(timelines), pd.DataFrame(results), rounds


def plot_e3_timeline(tl, rounds, out):
    sub = tl[tl["variant"] == "patched"]
    if sub.empty:
        sub = tl
    variant = sub["variant"].iloc[0]
    tags = sorted(sub["tag"].unique(), key=lambda t: resident_of(t) or 0)
    fig, axes = plt.subplots(len(tags), 3, figsize=(7.0, 1.35 * len(tags) + 0.35), squeeze=False)
    fig.subplots_adjust(wspace=0.45, hspace=0.35)
    for row, tag in enumerate(tags):
        main = sub[(sub["tag"] == tag) & (sub["role"] == "main")].sort_values("sec")
        mixed = sub[(sub["tag"] == tag) & (sub["role"] == "mixed")].sort_values("sec")
        t0 = main["t"].min() - main["sec"].min()
        ax_w, ax_l, ax_c = axes[row]
        last = main["sec"].max()
        for ax in (ax_w, ax_l, ax_c):
            for start, _end in rounds.get((variant, tag), []):
                if 0 <= start - t0 <= last:
                    ax.axvline(start - t0, color="0.55", lw=0.5, ls=(0, (1, 1.5)), zorder=0)
            ax.set_xlim(0, last)
        ax_w.plot(main["sec"], main["keys_ok"], color=PUT["color"], lw=0.9)
        ax_w.plot(main["sec"], main["keys_no_space"], color=PUT["color"], lw=0.8, ls=":")
        ax_w.set_ylim(bottom=0)
        ax_w.yaxis.set_major_formatter(FuncFormatter(si))
        ax_w.set_ylabel("Keys/s")
        if not mixed.empty:
            ax_l.plot(mixed["sec"], mixed["p50_ms"], color=GET["color"], lw=0.9)
            ax_l.plot(mixed["sec"], mixed["p99_ms"], color=GET["color"], lw=0.9, ls="--")
        ax_l.set_yscale("log")
        ax_l.yaxis.set_major_formatter(FuncFormatter(lambda v, _p: f"{v:g}"))
        ax_l.yaxis.set_minor_formatter(NullFormatter())
        ax_l.set_ylabel("Lookup (ms)")
        ax_c.plot(main["sec"], main["master_cpu_pct"] / 100, color="0.25", lw=0.9)
        ax_c.set_ylim(0, 17)
        ax_c.set_ylabel("Master cores")
        res = resident_of(tag)
        ax_w.text(0.02, 0.92, f"{si(res)} objects" if res else tag, transform=ax_w.transAxes,
                  fontsize=7, va="top", ha="left",
                  bbox=dict(facecolor="white", edgecolor="none", pad=0.6, alpha=0.8))
        for ax in (ax_w, ax_l, ax_c):
            if row == len(tags) - 1:
                ax.set_xlabel("Time (s)")
            else:
                ax.tick_params(labelbottom=False)
    for ax, label in zip(axes[-1], ("(a) Writes", "(b) Lookup latency", "(c) Master CPU")):
        panel_label(ax, label, y=-0.55)
    keys = [Line2D([0], [0], color=PUT["color"], label="written"),
            Line2D([0], [0], color=PUT["color"], ls=":", label="rejected"),
            Line2D([0], [0], color=GET["color"], label="lookup p50"),
            Line2D([0], [0], color=GET["color"], ls="--", label="lookup p99"),
            Line2D([0], [0], color="0.55", lw=0.5, ls=(0, (1, 1.5)), label="eviction round starts")]
    fig.legend(handles=keys, loc="lower center", bbox_to_anchor=(0.5, 0.995), ncol=5,
               frameon=False, handlelength=2.0, columnspacing=1.2, borderaxespad=0)
    save(fig, out, "fig_evict_timeline", CAPTION_TIMELINE)


def plot_e3_summary(res, out):
    main = res[res["role"] == "main"].copy()
    mixed = res[res["role"] == "mixed"].copy()
    for df in (main, mixed):
        df["resident"] = df["tag"].map(resident_of)
    main["requested"] = main["keys_ok"] + main["keys_err"]
    main["rejected_pct"] = 100 * main["keys_no_space"] / main["requested"].clip(lower=1)
    fig, axes = plt.subplots(1, 3, figsize=(7.0, 1.8))
    fig.subplots_adjust(wspace=0.45)
    for variant, st in VARIANT.items():
        m = main[main["variant"] == variant].sort_values("resident")
        g = mixed[mixed["variant"] == variant].sort_values("resident")
        if m.empty:
            continue
        kw = dict(color=st["color"], marker=st["marker"])
        axes[0].plot(m["resident"], m["keys_per_s"], **kw)
        if not g.empty:
            axes[1].plot(g["resident"], g["lat_p99_ms"], **kw)
        axes[2].plot(m["resident"], m["rejected_pct"], **kw)
    counts = sorted(main["resident"].dropna().unique())
    for ax in axes:
        ax.set_xscale("log")
        ax.xaxis.set_major_locator(FixedLocator(counts))
        ax.xaxis.set_major_formatter(FuncFormatter(si))
        ax.xaxis.set_minor_formatter(NullFormatter())
        ax.set_xlabel("Resident objects")
    axes[0].set_ylim(bottom=0)
    axes[0].yaxis.set_major_formatter(FuncFormatter(si))
    axes[0].set_ylabel("Writes (keys/s)")
    axes[1].set_ylim(bottom=0)
    axes[1].set_ylabel("Lookup p99 (ms)")
    axes[2].set_ylim(0, 100)
    axes[2].set_ylabel("Rejected writes (%)")
    for ax, label in zip(axes, ("(a) Write throughput", "(b) Lookup tail", "(c) Rejected writes")):
        panel_label(ax, label)
    handles = [Line2D([0], [0], color=st["color"], marker=st["marker"], label=st["label"])
               for v, st in VARIANT.items() if (main["variant"] == v).any()]
    fig.legend(handles=handles, loc="lower center", bbox_to_anchor=(0.5, 0.93), ncol=2,
               frameon=False, handlelength=2.2, columnspacing=1.5, borderaxespad=0)
    save(fig, out, "fig_evict_summary", CAPTION_SUMMARY)


# --- E5 -----------------------------------------------------------------------

def load_results(roots, prefix):
    rows = []
    for root in roots:
        for path in sorted(root.rglob("results.jsonl")):
            rows += [r for r in read_jsonl(path) if r["tag"].startswith(prefix)]
    return pd.DataFrame(rows)


def fraction_of(tag):
    m = re.search(r"_f([0-9.]+)", tag)
    return float(m.group(1)) if m else None


def latency_lines(ax, df, x, st):
    agg = df.groupby(x).agg(p50=("lat_p50_ms", "median"), p99=("lat_p99_ms", "median"),
                            p999=("lat_p999_ms", "median")).reset_index().sort_values(x)
    ax.plot(agg[x], agg["p50"], ls="-", **st)
    ax.plot(agg[x], agg["p99"], ls="--", markerfacecolor="white", **st)
    ax.plot(agg[x], agg["p999"], ls=":", markerfacecolor="white", **st)
    ax.set_yscale("log")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _p: f"{v:g}"))
    ax.yaxis.set_minor_formatter(NullFormatter())
    return agg


def percentile_legend(ax):
    keys = [Line2D([0], [0], color="0.2", ls="-", label="p50"),
            Line2D([0], [0], color="0.2", ls="--", label="p99"),
            Line2D([0], [0], color="0.2", ls=":", label="p99.9")]
    ax.legend(handles=keys, loc="upper left", frameon=False, handlelength=2.2,
              borderaxespad=0.3, labelspacing=0.3)


def plot_e5(roots, out, lines):
    df = pd.concat([load_results(roots, "openget_f"), load_results(roots, "openput_f")],
                   ignore_index=True)
    if df.empty:
        return
    df["load"] = df["tag"].map(fraction_of)
    df["op"] = np.where(df["tag"].str.startswith("openget"), "Get", "Put")
    shades = {64: 0.45, 128: 0.7, 256: 1.0}
    markers = {64: "^", 128: "s", 256: "o"}
    fig, axes = plt.subplots(1, 2, figsize=(7.0, 1.85))
    fig.subplots_adjust(wspace=0.3)
    counts = sorted(df["num_clients"].unique())
    for ax, (op, st) in zip(axes, (("Get", GET), ("Put", PUT))):
        for n in counts:
            sub = df[(df["op"] == op) & (df["num_clients"] == n)]
            if sub.empty:
                continue
            agg = sub.groupby("load").agg(
                p50=("lat_p50_ms", "median"), p99=("lat_p99_ms", "median"),
                p999=("lat_p999_ms", "median"),
                ratio=("calls_per_s", "median"), offered=("offered_calls_per_s", "median"),
                cpu=("master_cpu_pct", "median")).reset_index().sort_values("load")
            color = matplotlib.colors.to_rgba(st["color"], shades.get(n, 1.0))
            ax.plot(agg["load"], agg["p99"], color=color, marker=markers.get(n, "o"),
                    label=f"{n} clients")
            lines.append(f"E5 {op} {n} clients: " + ", ".join(
                f"load {r.load:g}: p50 {r.p50:.2f} p99 {r.p99:.2f} p99.9 {r.p999:.2f} ms, "
                f"achieved {r.ratio / r.offered:.2f}, master {r.cpu:.0f}%"
                for r in agg.itertuples()))
        ax.set_yscale("log")
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _p: f"{v:g}"))
        ax.yaxis.set_minor_formatter(NullFormatter())
        ax.set_xlim(0, 1)
        ax.set_xlabel("Offered load (fraction of capacity)")
        ax.set_ylabel("p99 latency (ms)")
        if ax.get_legend_handles_labels()[0]:
            ax.legend(loc="upper left", frameon=False, handlelength=2.0, borderaxespad=0.3,
                      labelspacing=0.3)
    for ax, label in zip(axes, ("(a) Lookups", "(b) Writes")):
        panel_label(ax, label, y=-0.36)
    save(fig, out, "fig_open_loop", CAPTION_OPEN)


# --- E4 -----------------------------------------------------------------------

def plot_e4(roots, out, lines):
    df = load_results(roots, "contend_")
    if df.empty:
        return
    parts = df["tag"].str.extract(r"contend_(\w+?)_k(\d+)")
    df["side"], df["level"] = parts[0], parts[1].astype(int)
    fig, axes = plt.subplots(1, 2, figsize=(7.0, 1.85), sharey=True)
    fig.subplots_adjust(wspace=0.12)
    levels = sorted(df["level"].unique())
    for ax, side in zip(axes, ("client", "master")):
        sub = df[df["side"] == side]
        if sub.empty:
            continue
        agg = latency_lines(ax, sub, "level", GET)
        ax.xaxis.set_major_locator(FixedLocator(levels))
        ax.set_xlabel("stress-ng workers per core")
        lines.append(f"E4 {side} cores: " + ", ".join(
            f"k={r.level}: p50 {r.p50:.2f} p99 {r.p99:.2f} p99.9 {r.p999:.2f} ms"
            for r in agg.itertuples()))
    axes[0].set_ylabel("Lookup latency (ms)")
    percentile_legend(axes[0])
    for ax, label in zip(axes, ("(a) Client cores loaded", "(b) Master cores loaded")):
        panel_label(ax, label, y=-0.36)
    save(fig, out, "fig_contend", CAPTION_CONTEND)


def main():
    """Make the E3, E4 and E5 figures from masterbench results.

    Parameters
    ----------
    sys.argv[1:] : str
        Result directories, searched recursively; the first also receives the
        outputs under figures/.

    Saves
    -----
    figures/fig_evict_timeline.{pdf,png}, fig_evict_summary.{pdf,png}   (E3)
    figures/fig_contend.{pdf,png}                                       (E4)
    figures/fig_open_loop.{pdf,png}                                     (E5)
    figures/*_caption.tex, figures/e345_summary.txt
    """
    # --- Data ---
    roots = [Path(p) for p in sys.argv[1:]] or [Path(".")]
    out = roots[0] / "figures"
    out.mkdir(parents=True, exist_ok=True)
    style_axes()
    lines = []

    # --- Plot ---
    tl, res, rounds = load_e3(roots)
    if not tl.empty:
        plot_e3_timeline(tl, rounds, out)
        plot_e3_summary(res, out)
        for r in res[res["role"] == "main"].itertuples():
            g = res[(res["role"] == "mixed") & (res["tag"] == r.tag) & (res["variant"] == r.variant)]
            rr = rounds.get((r.variant, r.tag), [])
            n_rounds = len(rr)
            m = tl[(tl["tag"] == r.tag) & (tl["variant"] == r.variant) & (tl["role"] == "main")]
            g_tl = tl[(tl["tag"] == r.tag) & (tl["variant"] == r.variant) & (tl["role"] == "mixed")]
            med = m["keys_ok"].median()
            stall_s = int((m["keys_ok"] < 0.1 * med).sum())
            round_s = np.mean([e - b for b, e in rr]) if rr else 0.0
            lines.append(
                f"E3 {r.variant} {r.tag}: writes {r.keys_per_s:,.0f} keys/s, rejected "
                f"{r.keys_no_space:,} of {r.keys_ok + r.keys_err:,}, write p99 {r.lat_p99_ms:.1f} ms; "
                + (f"lookup p50 {g.iloc[0].lat_p50_ms:.2f} p99 {g.iloc[0].lat_p99_ms:.2f} "
                   f"max {g.iloc[0].lat_max_ms:.1f} ms, not found {g.iloc[0].keys_not_found:,}; "
                   if not g.empty else "")
                + f"eviction rounds {n_rounds} (mean {round_s:.1f} s); seconds with writes <10% of median "
                f"{stall_s}/{len(m)}; worst 1-s lookup p99 {g_tl['p99_ms'].max():.1f} ms; "
                f"lookups ok {g.iloc[0].keys_ok if not g.empty else 0:,}; master CPU {r.master_cpu_pct:.0f}%")
    plot_e4(roots, out, lines)
    plot_e5(roots, out, lines)

    # --- Save ---
    (out / "e345_summary.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
