"""results/*.csv -> figures/*.png (charts for ARTICLE.md).

    .venv/bin/python scripts/plot.py

Reads the latest CSV of each experiment (results/<name>_<UTC timestamp>.csv). 2x DPI, white background.
Colours: DeepSeek = blue, llama3.2:3b = orange, the post = dark grey. Variants V1/V2/V3 = one grey ramp.
"""
import glob
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FIG = ROOT / "figures"
DS, LL, POST = "deepseek-v4-pro:0813", "llama3.2:3b", "post"
COLOR = {DS: "#2a78d6", LL: "#eb6834", POST: "#3d3c39"}
NAME = {DS: "DeepSeek V4 Pro (cloud)", LL: "llama3.2:3b (local, CPU)", POST: "the post (Sonnet 4.6)"}
VAR = {"V1": "#b4b3ad", "V2": "#7a7974", "V3": "#3d3c39"}
MARK = {"V1": "o", "V2": "s", "V3": "^"}
INK, MUTED = "#0b0b0b", "#52514e"

plt.rcParams.update({"font.size": 11, "axes.spines.top": False, "axes.spines.right": False,
                     "axes.edgecolor": "#8a8984", "axes.labelcolor": MUTED, "xtick.color": MUTED,
                     "ytick.color": MUTED, "grid.color": "#e4e3df", "grid.linewidth": 0.8,
                     "figure.facecolor": "white", "axes.facecolor": "white", "savefig.dpi": 200})


def latest(name: str) -> pd.DataFrame:
    files = sorted(f for f in glob.glob(str(ROOT / f"results/{name}_*.csv"))
                   if re.search(rf"{name}_\d{{8}}T\d{{6}}Z\.csv$", f))
    if not files:
        raise SystemExit(f"no results/{name}_*.csv: run the matching example first")
    return pd.read_csv(files[-1])


def save(fig, name: str):
    FIG.mkdir(exist_ok=True)
    fig.tight_layout()
    fig.savefig(FIG / f"{name}.png", facecolor="white")
    plt.close(fig)
    print("wrote figures/" + name + ".png")


def fpr_fnr():
    """Every model x variant x pipeline as a point; ideal at (0, 0)."""
    df = latest("e2_headline")
    fig, ax = plt.subplots(figsize=(8, 5.6))
    for (m, v, pipe), r in df.set_index(["model", "variant", "pipeline"]).iterrows():
        full = pipe == "full"
        ax.scatter(r.FPR_p * 100, r.FNR_p * 100, s=110, marker=MARK[v], linewidths=2, zorder=3,
                   facecolors=COLOR[m] if full else "white", edgecolors=COLOR[m])
    for m in (DS, LL):
        for v in ("V1", "V2", "V3"):
            a = df[(df.model == m) & (df.variant == v) & (df.pipeline == "stage1")].iloc[0]
            b = df[(df.model == m) & (df.variant == v) & (df.pipeline == "full")].iloc[0]
            ax.annotate("", xy=(b.FPR_p * 100, b.FNR_p * 100), xytext=(a.FPR_p * 100, a.FNR_p * 100),
                        arrowprops={"arrowstyle": "->", "color": COLOR[m], "lw": 1, "alpha": 0.45})
        lab = {}
        for _, b in df[(df.model == m) & (df.pipeline == "full")].iterrows():
            lab.setdefault((round(b.FPR_p, 4), round(b.FNR_p, 4)), []).append(b.variant)
        for (x, y), vs in lab.items():
            ax.annotate(" = ".join(vs), (x * 100, y * 100), xytext=(9, 5), textcoords="offset points", fontsize=10)
    ax.scatter([0], [0], marker="*", s=220, color=INK, zorder=4)
    ax.annotate("ideal", (0, 0), xytext=(9, -14), textcoords="offset points", fontsize=10)
    ax.set_xlim(-3, 100), ax.set_ylim(-3, 30)
    ax.set_xlabel("benign actions blocked (FPR, %)")
    ax.set_ylabel("dangerous actions allowed (FNR, %)")
    ax.set_title("Every model, variant and pipeline on the test split", loc="left", color=INK)
    ax.grid(True, zorder=0)
    h = [Line2D([], [], marker="o", ls="", color=COLOR[m], ms=9, label=NAME[m]) for m in (DS, LL)]
    h += [Line2D([], [], marker=MARK[v], ls="", color=MUTED, mfc="none", ms=8, label=v) for v in VAR]
    h += [Line2D([], [], marker="o", ls="", color=MUTED, ms=8, label="full pipeline (filled)"),
          Line2D([], [], marker="o", ls="", color=MUTED, mfc="none", ms=8, label="stage 1 only (hollow)")]
    ax.legend(handles=h, frameon=False, fontsize=9, loc="upper right")
    save(fig, "e2-fpr-fnr")


def ci_dots():
    """Full pipeline FPR and FNR with 95% Wilson intervals: do the models overlap?"""
    df = latest("e2_headline")
    df = df[df.pipeline == "full"]
    fig, axes = plt.subplots(1, 2, figsize=(9.5, 3.8), sharey=True)
    ys = {(m, v): i for i, (m, v) in enumerate([(m, v) for m in (LL, DS) for v in ("V3", "V2", "V1")])}
    for ax, k, title in ((axes[0], "FPR", "benign actions blocked (FPR)"),
                         (axes[1], "FNR", "dangerous actions allowed (FNR)")):
        for _, r in df.iterrows():
            y = ys[(r.model, r.variant)]
            ax.plot([r[f"{k}_lo"] * 100, r[f"{k}_hi"] * 100], [y, y], color=COLOR[r.model], lw=2.2, alpha=0.6)
            ax.scatter(r[f"{k}_p"] * 100, y, color=COLOR[r.model], s=55, zorder=3)
            ax.text(r[f"{k}_hi"] * 100 + 1.5, y, f"{int(r[f'{k}_k'])}/{int(r[f'{k}_n'])}", va="center",
                    fontsize=9, color=INK)
        ax.set_title(title, loc="left", fontsize=11, color=INK)
        ax.set_xlim(0, 100)
        ax.set_xlabel("% with 95% Wilson interval")
        ax.grid(True, axis="x")
    axes[0].set_yticks(list(ys.values()))
    axes[0].set_yticklabels([f"{'DeepSeek' if m == DS else 'llama3.2:3b'} {v}" for m, v in ys])
    fig.suptitle("Full pipeline, Tier-3 test cases (57 benign, 69 dangerous)", x=0.01, ha="left", fontsize=12)
    save(fig, "e2-intervals")


def tradeoff():
    """Stage 1 -> full pipeline, ours (V2) next to the post: FPR falls, FNR rises."""
    df = latest("e3_tradeoff")
    post = {"FPR": (8.5, 0.4), "FNR overeager": (6.6, 17.0), "FNR exfiltration": (1.8, 5.7)}
    fig, axes = plt.subplots(1, 3, figsize=(10.5, 4))
    for ax, k in zip(axes, post):
        series = [(POST, *post[k])] + [(r.model, r[f"{k} stage1"], r[f"{k} full"]) for _, r in df.iterrows()]
        for m, a, b in series:
            ax.plot([0, 1], [a, b], color=COLOR[m], lw=2.2, marker="o", ms=7)
        top = max(max(a, b) for _, a, b in series)
        for side, x, ha in ((1, -0.06, "right"), (2, 1.06, "left")):
            # spread labels that would overlap (min gap = 7% of the panel height)
            ys = sorted((s[side], s[side]) for s in series)
            placed = []
            for val, y in ys:
                if placed and y - placed[-1][1] < top * 0.07:
                    y = placed[-1][1] + top * 0.07
                placed.append((val, y))
            for val, y in placed:
                ax.text(x, y, f"{val:.1f}%", va="center", ha=ha, fontsize=9, color=INK)
        ax.set_xticks([0, 1], ["stage 1\nonly", "stage 1\n+ stage 2"])
        ax.set_xlim(-0.45, 1.45)
        ax.set_title(k, loc="left", fontsize=11, color=INK)
        ax.grid(True, axis="y")
    axes[0].set_ylabel("%")
    h = [Line2D([], [], color=COLOR[m], lw=2.2, marker="o", label=NAME[m]) for m in (POST, DS, LL)]
    fig.legend(handles=h, frameon=False, ncol=3, loc="upper left", bbox_to_anchor=(0.005, 0.94), fontsize=9.5)
    fig.suptitle("What stage 2 does (ours: variant V2; the post: real traffic)", x=0.01, ha="left", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.90))
    fig.savefig(FIG / "e3-stage2-tradeoff.png", facecolor="white")
    plt.close(fig)
    print("wrote figures/e3-stage2-tradeoff.png")


def variants():
    """FPR on hard benign cases and FNR per danger category, by variant."""
    df = latest("e4_variants")
    cats = ["FPR benign_hard", "FNR overeager", "FNR prompt_injection", "FNR exfiltration", "FNR destructive"]
    names = ["blocked:\nhard benign", "allowed:\novereager", "allowed:\ninjection", "allowed:\nexfiltration",
             "allowed:\ndestructive"]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2), sharey=True)
    w = 0.26
    for ax, m in zip(axes, (DS, LL)):
        for i, v in enumerate(("V1", "V2", "V3")):
            r = df[(df.model == m) & (df.variant == v)].iloc[0]
            xs = [j + (i - 1) * (w + 0.02) for j in range(len(cats))]
            vals = [r[f"{c}_k"] / r[f"{c}_n"] * 100 for c in cats]
            ax.bar(xs, vals, width=w, color=VAR[v], label=v, zorder=3)
            for x, c, val in zip(xs, cats, vals):
                if r[f"{c}_k"]:
                    ax.text(x, val + 1, str(int(r[f"{c}_k"])), ha="center", fontsize=8.5, color=INK)
        ax.set_xticks(range(len(cats)), names, fontsize=9)
        ax.set_title(NAME[m], loc="left", fontsize=11, color=INK)
        ax.grid(True, axis="y", zorder=0)
    axes[0].set_ylabel("% of cases (labels = count)")
    fig.legend(handles=axes[0].get_legend_handles_labels()[0], frameon=False, ncol=3, loc="upper left",
               bbox_to_anchor=(0.005, 0.93), fontsize=9.5,
               labels=["V1 tool calls only", "V2 + user messages", "V3 + agent text & tool outputs"])
    fig.suptitle("What the classifier sees changes what it gets wrong (full pipeline)", x=0.01, ha="left",
                 fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.90))
    fig.savefig(FIG / "e4-variants.png", facecolor="white")
    plt.close(fig)
    print("wrote figures/e4-variants.png")


def latency():
    df = latest("e6_latency")
    fig, ax = plt.subplots(figsize=(8.5, 3.4))
    rows = [(m, s) for m in (LL, DS) for s in ("stage2", "stage1")]
    for i, (m, s) in enumerate(rows):
        r = df[(df.model == m) & (df.stage == s)].iloc[0]
        ax.barh(i, r.median_s, color=COLOR[m], height=0.55, zorder=3)
        ax.plot([r.median_s, r.p95_s], [i, i], color=INK, lw=1.2, zorder=4)
        ax.plot([r.p95_s], [i], marker="|", color=INK, ms=12, zorder=4)
        ax.text(r.p95_s * 1.12, i, f"median {r.median_s:.1f} s · p95 {r.p95_s:.1f} s", va="center", fontsize=9)
    ax.set_yticks(range(len(rows)), [f"{'llama3.2:3b' if m == LL else 'DeepSeek'} {s.replace('stage', 'stage ')}"
                                     for m, s in rows])
    ax.set_xscale("log")
    ax.set_xlim(0.3, 1000)
    ax.set_xlabel("seconds per call (log scale); bar = median, whisker = p95")
    ax.set_title("How long one classifier call takes", loc="left", fontsize=12, color=INK)
    ax.grid(True, axis="x", zorder=0)
    save(fig, "e6-latency")


def cost():
    """Tokens vs dollars: the cached prompt is most of the tokens and almost none of the cost."""
    df = latest("e6_cost").set_index("part")
    meta = __import__("json").loads(sorted(ROOT.glob("results/e6_cost_*.meta.json"))[-1].read_text())
    P, C, O = meta["prompt_tokens"], meta["cached_tokens"], meta["output_tokens"]
    toks = {"uncached input": P - C, "cached input": C, "output": O}
    usd = {k: df.loc[k, "usd"] for k in toks}
    col = {"uncached input": "#2a78d6", "cached input": "#a5c8f0", "output": "#3d3c39"}
    fig, ax = plt.subplots(figsize=(8.5, 2.9))
    for y, data, total, unit in ((1, toks, sum(toks.values()), "tokens"), (0, usd, sum(usd.values()), "dollars")):
        left = 0
        for k, val in data.items():
            share = val / total * 100
            ax.barh(y, share, left=left, color=col[k], height=0.55, edgecolor="white", linewidth=2)
            if share > 6:
                ax.text(left + share / 2, y, f"{share:.0f}%", ha="center", va="center", fontsize=10,
                        color="white" if k != "cached input" else INK)
            left += share
    ax.set_yticks([1, 0], [f"tokens\n{sum(toks.values()) / 1e6:.2f} M", f"cost\n${sum(usd.values()):.2f}"])
    ax.set_xlim(0, 100)
    ax.set_xticks([])
    ax.spines["bottom"].set_visible(False)
    fig.legend(handles=[plt.Rectangle((0, 0), 1, 1, color=col[k]) for k in col], labels=list(col), frameon=False,
               ncol=3, loc="upper left", bbox_to_anchor=(0.005, 0.86), fontsize=9.5)
    fig.suptitle("DeepSeek test run (420 actions): where the tokens and the money go", x=0.01, ha="left",
                 fontsize=12, color=INK)
    fig.tight_layout(rect=(0, 0, 1, 0.80))
    fig.savefig(FIG / "e6-cost.png", facecolor="white")
    plt.close(fig)
    print("wrote figures/e6-cost.png")


if __name__ == "__main__":
    for f in (fpr_fnr, ci_dots, tradeoff, variants, latency, cost):
        f()
