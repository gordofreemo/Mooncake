"""Slide figures in conventional systems-paper style (TikZ, Helvetica).

Full axis box, black tick labels, outward ticks, light dotted grid, legend
inside the plot, tab10 colours, no in-plot annotations. All four figures plot
p99 lookup latency of the patched master.
"""

import math

import mkfigs as base
from mkfigs import Fig, f3, build, read_jsonl, e3_timeline, e4_p99, e5_get_p99, worst_second_p99

PREAMBLE = base.PREAMBLE.replace(r"\definecolor{lookup}{HTML}{2A78D6}", r"\definecolor{cA}{HTML}{1F77B4}" "\n" r"\definecolor{cB}{HTML}{FF7F0E}" "\n" r"\definecolor{lookup}{HTML}{2A78D6}")
PREAMBLE = PREAMBLE.replace(r"  tick/.style={font=\footnotesize, text=inktwo, inner sep=1.5pt},",
                            r"  tick/.style={font=\footnotesize, text=black, inner sep=1.5pt},")
base.PREAMBLE = PREAMBLE


class Ax(base.Ax):
    def frame(self):
        self.f.add(rf"\draw[black, line width=0.6pt] ({f3(self.x0)},{f3(self.y0)}) rectangle ({f3(self.x0 + self.w)},{f3(self.y0 + self.h)});")

    def yticks(self, ticks, labels, grid=True):
        for t, lab in zip(ticks, labels):
            y = self.Y(t)
            if grid:
                self.f.add(rf"\draw[black!25, line width=0.4pt, dash pattern=on 0.6pt off 1.4pt] ({f3(self.x0)},{f3(y)}) -- ({f3(self.x0 + self.w)},{f3(y)});")
            self.f.add(rf"\draw[black, line width=0.6pt] ({f3(self.x0)},{f3(y)}) -- ({f3(self.x0 - 0.07)},{f3(y)});")
            self.f.add(rf"\node[tick, anchor=east] at ({f3(self.x0 - 0.09)},{f3(y)}) {{{lab}}};")

    def yminor(self):
        lo, hi = self.ylim
        d = 10 ** math.floor(math.log10(lo))
        while d < hi:
            for m in range(2, 10):
                v = m * d
                if lo < v < hi:
                    y = self.Y(v)
                    self.f.add(rf"\draw[black, line width=0.4pt] ({f3(self.x0)},{f3(y)}) -- ({f3(self.x0 - 0.04)},{f3(y)});")
            d *= 10

    def xminor(self):
        lo, hi = self.xlim
        d = 10 ** math.floor(math.log10(lo))
        while d < hi:
            for m in range(2, 10):
                v = m * d
                if lo < v < hi:
                    x = self.X(v)
                    self.f.add(rf"\draw[black, line width=0.4pt] ({f3(x)},{f3(self.y0)}) -- ({f3(x)},{f3(self.y0 - 0.04)});")
            d *= 10

    def xticks(self, ticks, labels, grid=False, show_labels=True):
        for t, lab in zip(ticks, labels):
            x = self.X(t)
            if grid:
                self.f.add(rf"\draw[black!25, line width=0.4pt, dash pattern=on 0.6pt off 1.4pt] ({f3(x)},{f3(self.y0)}) -- ({f3(x)},{f3(self.y0 + self.h)});")
            self.f.add(rf"\draw[black, line width=0.6pt] ({f3(x)},{f3(self.y0)}) -- ({f3(x)},{f3(self.y0 - 0.07)});")
            if show_labels:
                self.f.add(rf"\node[tick, anchor=north] at ({f3(x)},{f3(self.y0 - 0.09)}) {{{lab}}};")

    def marks(self, pts, color, shape="circle", hollow=False, ring=False):
        for x, y in pts:
            p = self.P(x, y)
            if shape == "circle":
                self.f.add(rf"\filldraw[fill={'white' if hollow else color}, draw={color}, line width=0.8pt] {p} circle (1.9pt);")
            else:
                self.f.add(rf"\filldraw[fill={'white' if hollow else color}, draw={color}, line width=0.8pt] {p} +(-1.8pt,-1.8pt) rectangle +(1.8pt,1.8pt);")


SER = "line width=1.1pt, line join=round"


def inner_legend(fig, x, y, items, anchor="north west"):
    """Legend box anchored at (x, y) by its top-left or bottom-right corner."""
    row = 0.42
    width = max(len(label) for label, _ in items) * 0.165 + 1.0
    if anchor == "south east":
        x, y = x - width, y + row * len(items) + 0.12
    fig.add(rf"\draw[fill=white, draw=black!40, line width=0.4pt] ({f3(x)},{f3(y)}) rectangle ({f3(x + width)},{f3(y - row * len(items) - 0.12)});")
    for i, (label, (color, shape, dash)) in enumerate(items):
        yy = y - 0.27 - i * row
        d = f", dash pattern={dash}" if dash else ""
        style = "black!55, line width=0.6pt" if color == "ref" else f"{SER}, draw={color}"
        fig.add(rf"\draw[{style}{d}] ({f3(x + 0.15)},{f3(yy)}) -- ({f3(x + 0.75)},{f3(yy)});")
        if shape:
            ax = Ax(fig, 0, 0, 1, 1, (0, 1), (0, 1))
            ax.marks([(x + 0.45, yy)], color, shape)
        fig.add(rf"\node[tick, anchor=west] at ({f3(x + 0.82)},{f3(yy)}) {{{label}}};")


LOG_MS = {1: "1", 10: "10", 100: "100", 1000: "1,000", 10000: "10,000"}


def top_legend(fig, x, y, items):
    """One-row legend above a plot, starting at (x, y)."""
    cx = x
    for label, (color, shape, dash) in items:
        d = f", dash pattern={dash}" if dash else ""
        style = "black!55, line width=0.6pt" if color == "ref" else f"{SER}, draw={color}"
        fig.add(rf"\draw[{style}{d}] ({f3(cx)},{f3(y)}) -- ({f3(cx + 0.6)},{f3(y)});")
        if shape:
            Ax(fig, 0, 0, 1, 1, (0, 1), (0, 1)).marks([(cx + 0.3, y)], color, shape)
        fig.add(rf"\node[tick, anchor=west] at ({f3(cx + 0.67)},{f3(y)}) {{{label}}};")
        cx += 0.67 + len(label) * 0.165 + 0.5


def fig_summary():
    e5, e4 = e5_get_p99(), e4_p99()
    groups = [
        ("(a) Load", [
            ("10\\% of peak throughput", e5[0.1]), ("50\\% of peak", e5[0.5]),
            ("70\\% of peak", e5[0.7]), ("95\\% of peak", e5[0.95])]),
        ("(b) Full pool (worst second)", [
            ("1M objects", worst_second_p99("evict_r1000000")),
            ("10M objects", worst_second_p99("evict_r10000000")),
            ("100M objects", worst_second_p99("evict_r100000000"))]),
        ("(c) Shared cores, at 12\\% of peak", [
            ("1 CPU-bound thread per client core", e4[("client", 1)][0]),
            ("1 CPU-bound thread per master core", e4[("master", 1)][0])]),
    ]
    f = Fig()
    row_h, head_h = 0.44, 0.52
    height = sum(len(g[1]) for g in groups) * row_h + len(groups) * head_h + 0.1
    ax = Ax(f, 6.4, 0, 8.0, height, (0.5, 5000), (0, height), xlog=True)
    ax.xticks([1, 10, 100, 1000], [LOG_MS[t] for t in (1, 10, 100, 1000)], grid=True)
    ax.xminor()
    y = height - 0.05
    for gi, (title, rows) in enumerate(groups):
        if gi:
            f.add(rf"\draw[black!40, line width=0.4pt] (0,{f3(y)}) -- ({f3(ax.x0 + ax.w)},{f3(y)});")
        y -= head_h
        f.add(rf"\node[tick, anchor=west, font=\footnotesize\bfseries] at (0,{f3(y + 0.2)}) {{{title}}};")
        for label, v in rows:
            y -= row_h
            yc = y + row_h / 2 + 0.06
            f.add(rf"\node[tick, anchor=west] at (0.3,{f3(yc)}) {{{label}}};")
            ax.marks([(v, yc)], "cA", "circle")
            txt = f"{v:.1f}" if v < 100 else f"{v:,.0f}"
            f.add(rf"\node[tick, anchor=west] at ({f3(ax.X(v) + 0.1)},{f3(yc)}) {{{txt}}};")
    ax.frame()
    f.add(rf"\node[lab, anchor=north] at ({f3(ax.x0 + ax.w / 2)},-0.55) {{p99 lookup latency (ms)}};")
    return f.write("paper_summary")


def fig_load():
    e5 = e5_get_p99()
    pts = sorted((100 * k, v) for k, v in e5.items())
    f = Fig()
    ax = Ax(f, 0, 0, 7.0, 4.4, (0, 100), (1, 100), ylog=True)
    ax.yticks([1, 10, 100], [LOG_MS[t] for t in (1, 10, 100)])
    ax.yminor()
    ax.xticks([0, 20, 40, 60, 80, 100], ["0", "20", "40", "60", "80", "100"])
    ax.line(pts, f"{SER}, draw=cA")
    ax.marks(pts, "cA", "circle")
    ax.frame()
    ax.xlabel("Offered load (\\% of peak throughput)", dy=0.55)
    ax.ylabel("p99 lookup latency (ms)", dx=0.75)
    return f.write("paper_load")


def fig_full_pool():
    main, mixed, starts = e3_timeline()
    last = main[-1]["sec"]
    f = Fig()
    ax = Ax(f, 0, 0, 10.0, 4.4, (0, last), (1, 1000), ylog=True)
    ax.yticks([1, 10, 100, 1000], [LOG_MS[t] for t in (1, 10, 100, 1000)])
    ax.yminor()
    ax.xticks([0, 30, 60, 90, 120, 150, 180], ["0", "30", "60", "90", "120", "150", "180"])
    for x in starts:
        if 0 <= x <= last:
            ax.vline(x, "black!55, line width=0.6pt, dash pattern=on 2.5pt off 1.5pt")
    ax.line([(r["sec"], max(r["p99_ms"], 1)) for r in mixed], f"{SER}, draw=cA")
    ax.frame()
    ax.xlabel("Time (s)", dy=0.55)
    ax.ylabel("p99 lookup latency (ms)", dx=0.95)
    top_legend(f, 0.1, 4.75, [("p99 lookup latency, per second", ("cA", None, None)),
                              ("start of an eviction round", ("ref", None, "on 2.5pt off 1.5pt"))])
    return f.write("paper_full_pool")


def fig_shared_cores():
    d = e4_p99()
    client = [(k, d[("client", k)][0]) for k in (0, 1, 2, 4)]
    master = [(k, d[("master", k)][0]) for k in (0, 1, 2, 4)]
    f = Fig()
    ax = Ax(f, 0, 0, 7.0, 4.4, (-0.25, 4.25), (1, 50000), ylog=True)
    ax.yticks([1, 10, 100, 1000, 10000], ["1", "10", "100", "1,000", "10,000"])
    ax.yminor()
    ax.xticks([0, 1, 2, 3, 4], ["0", "1", "2", "3", "4"])
    ax.line(client, f"{SER}, draw=cA")
    ax.line(master, f"{SER}, draw=cB, dash pattern=on 3pt off 1.5pt")
    ax.marks(client, "cA", "circle")
    ax.marks(master, "cB", "square")
    ax.frame()
    ax.xlabel("CPU-bound threads per core", dy=0.55)
    ax.ylabel("p99 lookup latency (ms)", dx=1.25)
    inner_legend(f, 6.85, 0.15, [("on the client cores", ("cA", "circle", None)),
                                 ("on the master cores", ("cB", "square", "on 3pt off 1.5pt"))],
                 anchor="south east")
    return f.write("paper_shared_cores")


def main():
    """Write and build the paper-style slide figures.

    Saves
    -----
    slides/fig_paper_{summary,load,full_pool,shared_cores}.{tex,pdf,png,svg}
    """
    for make in (fig_summary, fig_load, fig_full_pool, fig_shared_cores):
        path = make()
        build(path)
        print("built", path.stem)


if __name__ == "__main__":
    main()
