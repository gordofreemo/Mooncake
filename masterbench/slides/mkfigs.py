"""Slide figures for the master-bottleneck evidence, emitted as TikZ (Helvetica).

Each figure is written to slides/fig_<name>.tex and built with lualatex; the
data come from the masterbench results copied into slides/data/.
Palette (validated, light surface): lookups #2a78d6, writes #eb6834.
"""

import csv
import json
import math
import re
import statistics as st
import subprocess
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"

PREAMBLE = r"""\documentclass{article}
\usepackage{fontspec}
\setsansfont{Helvetica}
\renewcommand{\familydefault}{\sfdefault}
\usepackage{tikz}
\usetikzlibrary{arrows.meta}
\pagestyle{empty}
\definecolor{lookup}{HTML}{2A78D6}
\definecolor{lookuplight}{HTML}{9DC1EC}
\definecolor{write}{HTML}{EB6834}
\definecolor{writelight}{HTML}{F5B597}
\definecolor{ink}{HTML}{0B0B0B}
\definecolor{inktwo}{HTML}{52514E}
\definecolor{gridc}{HTML}{E4E3DF}
\definecolor{axisc}{HTML}{8F8E89}
\definecolor{refc}{HTML}{3D3C39}
\tikzset{
  tick/.style={font=\footnotesize, text=inktwo, inner sep=1.5pt},
  lab/.style={font=\small, text=ink, inner sep=1.5pt},
  note/.style={font=\footnotesize, text=inktwo, inner sep=1.5pt},
  ser/.style={line width=1.4pt, line cap=round, line join=round},
  thin/.style={line width=0.9pt, line cap=round, line join=round},
  ref/.style={line width=0.8pt, draw=refc, dash pattern=on 3pt off 2pt},
}
\begin{document}
\setbox0=\hbox{%
\begin{tikzpicture}[x=1cm,y=1cm]
"""

POSTAMBLE = r"""\end{tikzpicture}}%
\pdfvariable horigin=0pt
\pdfvariable vorigin=0pt
\pagewidth=\dimexpr\wd0+8pt\relax
\pageheight=\dimexpr\ht0+\dp0+8pt\relax
\shipout\vbox{\kern4pt\hbox{\kern4pt\box0\kern4pt}\kern4pt}
\end{document}
"""


def f3(v):
    return f"{v:.3f}"


class Fig:
    def __init__(self):
        self.cmds = []

    def add(self, s):
        self.cmds.append(s)

    def write(self, name):
        path = HERE / f"fig_{name}.tex"
        path.write_text(PREAMBLE + "\n".join(self.cmds) + "\n" + POSTAMBLE)
        return path


class Ax:
    """A plot area at (x0, y0) of size w x h cm with linear or log10 scales."""

    def __init__(self, fig, x0, y0, w, h, xlim, ylim, xlog=False, ylog=False):
        self.f, self.x0, self.y0, self.w, self.h = fig, x0, y0, w, h
        self.xlim, self.ylim, self.xlog, self.ylog = xlim, ylim, xlog, ylog

    def _map(self, v, lim, log, origin, size):
        a, b = lim
        if log:
            return origin + size * (math.log10(v) - math.log10(a)) / (math.log10(b) - math.log10(a))
        return origin + size * (v - a) / (b - a)

    def X(self, v):
        return self._map(v, self.xlim, self.xlog, self.x0, self.w)

    def Y(self, v):
        return self._map(v, self.ylim, self.ylog, self.y0, self.h)

    def P(self, x, y):
        return f"({f3(self.X(x))},{f3(self.Y(y))})"

    def yticks(self, ticks, labels, grid=True):
        for t, lab in zip(ticks, labels):
            y = self.Y(t)
            if grid:
                self.f.add(rf"\draw[draw=gridc, line width=0.4pt] ({f3(self.x0)},{f3(y)}) -- ({f3(self.x0 + self.w)},{f3(y)});")
            self.f.add(rf"\node[tick, anchor=east] at ({f3(self.x0 - 0.06)},{f3(y)}) {{{lab}}};")

    def xticks(self, ticks, labels, grid=False, show_labels=True):
        for t, lab in zip(ticks, labels):
            x = self.X(t)
            if grid:
                self.f.add(rf"\draw[draw=gridc, line width=0.4pt] ({f3(x)},{f3(self.y0)}) -- ({f3(x)},{f3(self.y0 + self.h)});")
            self.f.add(rf"\draw[draw=axisc, line width=0.4pt] ({f3(x)},{f3(self.y0)}) -- ({f3(x)},{f3(self.y0 - 0.07)});")
            if show_labels:
                self.f.add(rf"\node[tick, anchor=north] at ({f3(x)},{f3(self.y0 - 0.09)}) {{{lab}}};")

    def frame(self):
        self.f.add(rf"\draw[draw=axisc, line width=0.5pt] ({f3(self.x0)},{f3(self.y0 + self.h)}) -- ({f3(self.x0)},{f3(self.y0)}) -- ({f3(self.x0 + self.w)},{f3(self.y0)});")

    def xlabel(self, text, dy=0.62):
        self.f.add(rf"\node[lab, anchor=north] at ({f3(self.x0 + self.w / 2)},{f3(self.y0 - dy)}) {{{text}}};")

    def ylabel(self, text, dx=1.05):
        self.f.add(rf"\node[lab, rotate=90, anchor=south] at ({f3(self.x0 - dx)},{f3(self.y0 + self.h / 2)}) {{{text}}};")

    def line(self, pts, style):
        self.f.add(rf"\draw[{style}] " + " -- ".join(self.P(x, y) for x, y in pts) + ";")

    def marks(self, pts, color, shape="circle", hollow=False, ring=True):
        for x, y in pts:
            p = self.P(x, y)
            if shape == "circle":
                if ring:
                    self.f.add(rf"\fill[white] {p} circle (3.4pt);")
                if hollow:
                    self.f.add(rf"\filldraw[fill=white, draw={color}, line width=1.1pt] {p} circle (2.2pt);")
                else:
                    self.f.add(rf"\fill[{color}] {p} circle (2.5pt);")
            else:
                if ring:
                    self.f.add(rf"\fill[white] {p} +(-3.3pt,-3.3pt) rectangle +(3.3pt,3.3pt);")
                if hollow:
                    self.f.add(rf"\filldraw[fill=white, draw={color}, line width=1.1pt] {p} +(-2.0pt,-2.0pt) rectangle +(2.0pt,2.0pt);")
                else:
                    self.f.add(rf"\fill[{color}] {p} +(-2.4pt,-2.4pt) rectangle +(2.4pt,2.4pt);")

    def hline(self, y, style="ref"):
        self.f.add(rf"\draw[{style}] ({f3(self.x0)},{f3(self.Y(y))}) -- ({f3(self.x0 + self.w)},{f3(self.Y(y))});")

    def vline(self, x, style):
        self.f.add(rf"\draw[{style}] ({f3(self.X(x))},{f3(self.y0)}) -- ({f3(self.X(x))},{f3(self.y0 + self.h)});")

    def text(self, x, y, s, anchor="west", style="note"):
        self.f.add(rf"\node[{style}, anchor={anchor}] at {self.P(x, y)} {{{s}}};")


def legend(fig, x, y, items, gap=0.35):
    """Horizontal legend: items = [(label, draw_fn(fig, x, y))], drawn left to right."""
    cx = x
    for label, sample, width in items:
        sample(fig, cx, y)
        fig.add(rf"\node[note, anchor=west, text=ink] at ({f3(cx + 0.62)},{f3(y)}) {{{label}}};")
        cx += 0.62 + width + gap


def sample_line(color, style="ser", shape="circle", hollow=False, dash=None):
    def draw(fig, x, y):
        d = f", dash pattern={dash}" if dash else ""
        fig.add(rf"\draw[{style}, draw={color}{d}] ({f3(x)},{f3(y)}) -- ({f3(x + 0.5)},{f3(y)});")
        if shape:
            ax = Ax(fig, 0, 0, 1, 1, (0, 1), (0, 1))
            ax.marks([(x + 0.25, y)], color, shape, hollow, ring=False)
    return draw


def si(v):
    for div, suffix in ((1e6, "M"), (1e3, "K")):
        if v >= div:
            q = v / div
            return f"{q:.0f}{suffix}" if q >= 10 else f"{q:.1f}".rstrip("0").rstrip(".") + suffix
    return f"{v:g}"


def build(path):
    subprocess.run(["lualatex", "-interaction=nonstopmode", "-halt-on-error", path.name],
                   cwd=HERE, check=True, stdout=subprocess.DEVNULL)
    pdf = path.with_suffix(".pdf")
    subprocess.run(["pdftoppm", "-r", "300", "-png", "-singlefile", pdf.name, path.stem], cwd=HERE, check=True)
    subprocess.run(["pdftocairo", "-svg", pdf.name, path.stem + ".svg"], cwd=HERE, check=True)


# --- Data ----------------------------------------------------------------------

def read_jsonl(name):
    return [json.loads(l) for l in (DATA / name).read_text().splitlines() if l.strip()]


def e2_series():
    rows = list(csv.DictReader((DATA / "e2_compare.csv").open()))
    out = {}
    for r in rows:
        out.setdefault((r["variant"], r["op"]), []).append((int(r["stores"]), float(r["keys_per_s"])))
    return {k: sorted(v) for k, v in out.items()}


def glog_epoch(line, year=2026):
    m = re.match(r"I(\d{2})(\d{2}) (\d{2}):(\d{2}):(\d{2})\.(\d+)", line)
    mo, d, hh, mm, ss, us = (int(g) for g in m.groups())
    return datetime(year, mo, d, hh, mm, ss, us, tzinfo=timezone.utc).timestamp()


def e3_timeline(tag="evict_r100000000"):
    tl = [r for r in read_jsonl("e3_patched_timeline.jsonl") if r["tag"] == tag]
    main = sorted((r for r in tl if r["role"] == "main"), key=lambda r: r["sec"])
    mixed = sorted((r for r in tl if r["role"] == "mixed"), key=lambda r: r["sec"])
    t0 = main[0]["t"] - main[0]["sec"]
    starts = [glog_epoch(l) - t0 for l in (DATA / "e3_r100M_evict.log").read_text().splitlines()
              if "EVICT-TRIGGER" in l]
    return main, mixed, starts


def worst_second_p99(tag):
    tl = [r for r in read_jsonl("e3_patched_timeline.jsonl") if r["tag"] == tag and r["role"] == "mixed"]
    return max(r["p99_ms"] for r in tl)


def e4_p99():
    out = {}
    for r in read_jsonl("e4_results.jsonl"):
        side, k = re.match(r"contend_(\w+?)_k(\d+)", r["tag"]).groups()
        out.setdefault((side, int(k)), []).append(r)
    return {k: (st.median(x["lat_p99_ms"] for x in v), st.median(x["lat_p50_ms"] for x in v),
                st.median(x["calls_per_s"] / x["offered_calls_per_s"] for x in v)) for k, v in out.items()}


def e5_get_p99():
    out = {}
    for r in read_jsonl("e5_get_128.jsonl"):
        m = re.match(r"openget_f([0-9.]+)", r["tag"])
        if m:
            out.setdefault(float(m.group(1)), []).append(r["lat_p99_ms"])
    return {k: st.median(v) for k, v in out.items()}


# --- Figures -------------------------------------------------------------------

def fig_writes_vs_cluster():
    s = e2_series()
    f = Fig()
    ax = Ax(f, 0, 0, 10.0, 5.2, (24, 11000), (100, 1e7), xlog=True, ylog=True)
    yt = [1e2, 1e3, 1e4, 1e5, 1e6, 1e7]
    ax.yticks(yt, ["100", "1K", "10K", "100K", "1M", "10M"])
    xt = [32, 128, 512, 2048, 8192]
    ax.xticks(xt, ["32", "128", "512", "2,048", "8,192"])
    ax.frame()
    get = s[("upstream", "Get")]
    up = s[("upstream", "Put")]
    pa = s[("patched", "Put")]
    ax.line(get, "ser, draw=lookup")
    ax.line(pa, "ser, draw=write, dash pattern=on 4pt off 2.5pt")
    ax.line(up, "ser, draw=write")
    ax.marks(get, "lookup", "circle")
    ax.marks(pa, "write", "square", hollow=True)
    ax.marks(up, "write", "square")
    # Selective direct labels: the endpoints that carry the story.
    ax.text(36, up[0][1] * 1.3, f"{si(up[0][1])}", "south west")
    ax.text(8192, up[-1][1] * 1.0, f"\\;{up[-1][1]:.0f}", "west")
    ax.text(8192, pa[-1][1], f"\\;{si(round(pa[-1][1], -4))}", "west")
    ax.text(8192, get[-1][1], f"\\;{si(round(get[-1][1], -5))}", "west")
    ax.text(40, 900, "upstream: 512× fewer writes/s", "west", "lab")
    ax.xlabel("Storage clients registered with the master")
    ax.ylabel("Keys per second")
    legend(f, 0.05, 5.62, [
        ("lookups", sample_line("lookup", shape="circle"), 1.05),
        ("writes, upstream", sample_line("write", shape="square"), 2.0),
        ("writes, patched", sample_line("write", shape="square", hollow=True, dash="on 4pt off 2.5pt"), 1.9),
    ])
    return f.write("writes_vs_cluster")


def fig_eviction_timeline():
    main, mixed, starts = e3_timeline()
    f = Fig()
    last = main[-1]["sec"]
    top = Ax(f, 0, 2.95, 12.0, 2.3, (0, last), (0, 800e3))
    bot = Ax(f, 0, 0, 12.0, 2.5, (0, last), (0.5, 1000), ylog=True)
    for ax in (top, bot):
        for x in starts:
            if 0 <= x <= last:
                ax.vline(x, "draw=refc, line width=0.6pt")
    top.yticks([0, 250e3, 500e3, 750e3], ["0", "250K", "500K", "750K"])
    top.xticks([0, 30, 60, 90, 120, 150, 180], [""] * 7, show_labels=False)
    top.frame()
    top.line([(r["sec"], r["keys_no_space"]) for r in main], "thin, draw=writelight")
    top.line([(r["sec"], r["keys_ok"]) for r in main], "ser, draw=write")
    top.ylabel("Writes/s", dx=1.15)
    bot.yticks([1, 10, 100, 1000], ["1", "10", "100", "1,000"])
    bot.hline(10)
    bot.xticks([0, 30, 60, 90, 120, 150, 180], ["0", "30", "60", "90", "120", "150", "180"])
    bot.frame()
    bot.line([(r["sec"], max(r["p50_ms"], 0.5)) for r in mixed], "thin, draw=lookuplight")
    bot.line([(r["sec"], max(r["p99_ms"], 0.5)) for r in mixed], "ser, draw=lookup")
    bot.ylabel("Lookup (ms)", dx=1.15)
    bot.xlabel("Time (s)")
    bot.text(last, 10, "10 ms", "south east")
    # Labels for the event markers, placed in the top panel's headroom.
    for i, x in enumerate(starts, 1):
        if 0 <= x <= last:
            f.add(rf"\node[note, anchor=south] at ({f3(top.X(x))},{f3(top.y0 + top.h)}) {{round {i}}};")
    legend(f, 0.05, 5.95, [
        ("written", sample_line("write", shape=None), 1.0),
        ("rejected (no space)", sample_line("writelight", style="thin", shape=None), 2.55),
        ("lookup p99", sample_line("lookup", shape=None), 1.45),
        ("lookup p50", sample_line("lookuplight", style="thin", shape=None), 1.45),
        ("eviction round starts", lambda fig, x, y: fig.add(
            rf"\draw[draw=refc, line width=0.6pt] ({f3(x + 0.25)},{f3(y - 0.17)}) -- ({f3(x + 0.25)},{f3(y + 0.17)});"), 2.6),
    ], gap=0.3)
    return f.write("eviction_timeline")


def fig_contention():
    d = e4_p99()
    f = Fig()
    ax = Ax(f, 0, 0, 8.5, 5.0, (-0.3, 4.3), (0.5, 50000), ylog=True)
    ax.yticks([1, 10, 100, 1000, 10000], ["1 ms", "10 ms", "100 ms", "1 s", "10 s"])
    ax.xticks([0, 1, 2, 4], ["0", "1", "2", "4"])
    ax.frame()
    ax.hline(10)
    client = [(k, d[("client", k)][0]) for k in (0, 1, 2, 4)]
    master = [(k, d[("master", k)][0]) for k in (0, 1, 2, 4)]
    ax.line(master, "ser, draw=lookup, dash pattern=on 4pt off 2.5pt")
    ax.line(client, "ser, draw=lookup")
    ax.marks(master, "lookup", "square", hollow=True)
    ax.marks(client, "lookup", "circle")
    ax.text(0, client[0][1] * 0.55, f"{client[0][1]:.0f} ms", "north")
    ax.text(1, client[1][1] * 0.62, f"\\;\\;{client[1][1]:.0f} ms", "north west")
    ax.text(1, master[1][1] * 1.45, f"{master[1][1] / 1000:.1f} s\\;\\;", "south east")
    ax.text(4.3, 10, "10 ms", "south east")
    ax.text(3.0, 2500, "offered rate not sustained:", "south")
    ax.text(3.0, 2500, "queue grows over the run", "north")
    ax.xlabel("CPU-bound stress-ng workers per core")
    ax.ylabel("Lookup p99", dx=1.35)
    legend(f, 0.05, 5.42, [
        ("client cores loaded", sample_line("lookup", shape="circle"), 2.3),
        ("master cores loaded", sample_line("lookup", shape="square", hollow=True, dash="on 4pt off 2.5pt"), 2.4),
    ])
    return f.write("contention")


def fig_write_stalls():
    tl = sorted((r for r in read_jsonl("e5_write_timeline.jsonl")
                 if r["role"] == "main" and r["tag"].startswith("openput_f")), key=lambda r: r["sec"])
    f = Fig()
    last = tl[-1]["sec"]
    top = Ax(f, 0, 2.95, 12.0, 2.2, (0, last), (0, 1.25e6))
    bot = Ax(f, 0, 0, 12.0, 2.5, (0, last), (1, 10000), ylog=True)
    top.yticks([0, 400e3, 800e3, 1.2e6], ["0", "400K", "800K", "1.2M"])
    top.xticks([0, 20, 40, 60, 80, 100, 120], [""] * 7, show_labels=False)
    top.frame()
    top.line([(r["sec"], r["keys_ok"]) for r in tl], "ser, draw=write")
    top.ylabel("Writes/s", dx=1.15)
    bot.yticks([1, 10, 100, 1000, 10000], ["1", "10", "100", "1,000", "10,000"])
    bot.xticks([0, 20, 40, 60, 80, 100, 120], ["0", "20", "40", "60", "80", "100", "120"])
    bot.frame()
    bot.line([(r["sec"], max(r["p50_ms"], 1)) for r in tl], "thin, draw=writelight")
    bot.line([(r["sec"], max(r["p99_ms"], 1)) for r in tl], "ser, draw=write")
    bot.ylabel("Write latency (ms)", dx=1.15)
    bot.xlabel("Time (s)")
    # Keys held by the master at each stall: 1M preloaded + 5 s warm-up + writes so far.
    rate = st.median(r["keys_ok"] for r in tl)
    stalls = []
    for r in tl:
        if r["p99_ms"] > 10 * st.median(x["p99_ms"] for x in tl):
            if not stalls or r["sec"] - stalls[-1][-1]["sec"] > 3:
                stalls.append([r])
            else:
                stalls[-1].append(r)
    cum = 0
    keys_at = {}
    for r in tl:
        cum += r["keys_ok"]
        keys_at[r["sec"]] = 1e6 + 5 * rate + cum
    for grp in stalls:
        peak = max(grp, key=lambda r: r["p99_ms"])
        keys = keys_at[grp[0]["sec"]] / 1e6
        bot.text(peak["sec"], peak["p99_ms"] * 1.25, f"{keys:.0f}M keys", "south")
    legend(f, 0.05, 5.6, [
        ("written", sample_line("write", shape=None), 0.95),
        ("p99", sample_line("write", shape=None), 0.45),
        ("p50", sample_line("writelight", style="thin", shape=None), 0.45),
    ])
    return f.write("write_stalls")


def fig_discovery_summary():
    e5 = e5_get_p99()
    e4 = e4_p99()
    groups = [
        ("Random arrivals", [
            ("10\\% of capacity", e5[0.1]), ("50\\%", e5[0.5]), ("70\\%", e5[0.7]), ("95\\%", e5[0.95])]),
        ("Full pool (worst second)", [
            ("1M objects", worst_second_p99("evict_r1000000")),
            ("10M objects", worst_second_p99("evict_r10000000")),
            ("100M objects", worst_second_p99("evict_r100000000"))]),
        ("Shared cores, 12\\% load", [
            ("1 CPU worker per client core", e4[("client", 1)][0]),
            ("1 CPU worker per master core", e4[("master", 1)][0])]),
    ]
    f = Fig()
    row_h, head_h = 0.48, 0.66
    n_rows = sum(len(g[1]) for g in groups)
    height = n_rows * row_h + len(groups) * head_h
    ax = Ax(f, 5.4, 0, 8.4, height, (0.5, 4000), (0, height), xlog=True)
    xt = [1, 10, 100, 1000]
    ax.xticks(xt, ["1 ms", "10 ms", "100 ms", "1 s"], grid=True)
    ax.f.add(rf"\draw[draw=axisc, line width=0.5pt] ({f3(ax.x0)},0) -- ({f3(ax.x0 + ax.w)},0);")
    ax.vline(10, "ref")
    f.add(rf"\node[note, anchor=south] at ({f3(ax.X(10))},{f3(height)}) {{10 ms}};")
    y = height
    for title, rows in groups:
        y -= head_h
        f.add(rf"\node[lab, anchor=west, font=\small\bfseries] at (0,{f3(y + 0.3)}) {{{title}}};")
        for label, v in rows:
            y -= row_h
            yc = y + row_h / 2 + 0.05
            f.add(rf"\node[lab, anchor=west] at (0.25,{f3(yc)}) {{{label}}};")
            f.add(rf"\draw[draw=gridc, line width=0.4pt] ({f3(ax.x0)},{f3(yc)}) -- ({f3(ax.X(v))},{f3(yc)});")
            ax.marks([(v, yc)], "lookup", "circle")
            txt = f"{v:.1f} ms" if v < 100 else (f"{v:.0f} ms" if v < 1000 else f"{v / 1000:.1f} s")
            f.add(rf"\node[note, text=ink, anchor=west, fill=white, inner sep=1pt] at ({f3(ax.X(v) + 0.14)},{f3(yc)}) {{{txt}}};")
    f.add(rf"\node[lab, anchor=north] at ({f3(ax.x0 + ax.w / 2)},-0.62) {{Lookup p99 per 128-key call}};")
    return f.write("discovery_summary")


def main():
    """Write and build every slide figure.

    Saves
    -----
    slides/fig_{writes_vs_cluster,eviction_timeline,contention,write_stalls,
    discovery_summary}.{tex,pdf,png,svg}
    """
    for make in (fig_writes_vs_cluster, fig_eviction_timeline, fig_contention,
                 fig_write_stalls, fig_discovery_summary):
        path = make()
        build(path)
        print("built", path.stem)


if __name__ == "__main__":
    main()
