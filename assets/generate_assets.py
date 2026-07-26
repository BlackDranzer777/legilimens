"""
Generates the images embedded in the top-level README.md.

These images ARE committed (unlike doc/assets/*, which are gitignored) so they
render on GitHub. Re-run this whenever the architecture or branding changes.

Run:
    .venv\\Scripts\\python.exe assets\\generate_assets.py      # Windows
    .venv/bin/python assets/generate_assets.py                # macOS/Linux

Output (committed):
    assets/banner.png         hero banner for the top of the README
    assets/architecture.png   system architecture (ports + data flow)
    assets/frontend.png       frontend architecture (data in, controls out)

Deps: matplotlib (already in the doc/.venv — see doc/requirements.txt).
"""

from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

# ---------- brand palette (matches the app's neo-brutalist theme) ----------
ACCENT = "#C8F400"   # acid green
DARK = "#1a1a1a"
INK = "#0d0d0d"      # banner background
GREY = "#c2c2c2"
MUTED = "#8a8a8a"
WHITE = "#ffffff"

OUT = Path(__file__).parent


# ---------------------------------------------------------------- banner ----
def banner():
    fig, ax = plt.subplots(figsize=(12, 3.4))
    fig.patch.set_facecolor(INK)
    ax.set_facecolor(INK)
    ax.set_xlim(0, 12)
    ax.set_ylim(0, 3.4)
    ax.axis("off")

    # spaced wordmark
    ax.text(6, 2.15, "L E G I L I M E N S", ha="center", va="center",
            fontsize=42, fontweight="bold", color=WHITE, family="monospace")

    # accent rule under the wordmark
    ax.add_patch(Rectangle((3.0, 1.62), 6.0, 0.055, color=ACCENT, zorder=3))

    # tagline
    ax.text(6, 1.22, "Reading what others cannot see.", ha="center", va="center",
            fontsize=15, color=ACCENT, style="italic")

    # one-line description
    ax.text(6, 0.66,
            "A man-in-the-middle inspector for WebTransport — the traffic Burp and Wireshark can't see.",
            ha="center", va="center", fontsize=11.5, color=GREY)

    # protocol chips
    ax.text(6, 0.22, "QUIC    ·    HTTP/3    ·    UDP    ·    datagrams + streams",
            ha="center", va="center", fontsize=9, color=MUTED, family="monospace")

    fig.savefig(OUT / "banner.png", dpi=150, facecolor=INK, bbox_inches="tight")
    plt.close(fig)


# ------------------------------------------------------- shared box helper ----
def _box(ax, x, y, w, h, label, sub=None, hot=False, fs=11.5):
    ax.add_patch(Rectangle((x, y), w, h, linewidth=2.6 if hot else 1.6,
                           edgecolor=DARK, facecolor=ACCENT if hot else WHITE,
                           zorder=2))
    ly = y + h / 2 + (0.16 if sub else 0)
    ax.text(x + w / 2, ly, label, ha="center", va="center", zorder=3,
            fontsize=fs, color=DARK, fontweight="bold")
    if sub:
        ax.text(x + w / 2, y + h / 2 - 0.24, sub, ha="center", va="center",
                zorder=3, fontsize=8.6, color="#555555", family="monospace")


def _arrow(ax, p1, p2, label="", off=(0, 0.28), color=DARK, lw=2.8,
           style="-", fs=9.8, italic=False):
    ax.annotate("", xy=p2, xytext=p1,
                arrowprops=dict(arrowstyle="-|>", lw=lw, color=color,
                                shrinkA=4, shrinkB=4, linestyle=style))
    if label:
        mx, my = (p1[0] + p2[0]) / 2 + off[0], (p1[1] + p2[1]) / 2 + off[1]
        ax.text(mx, my, label, ha="center", va="center", fontsize=fs, color=color,
                fontweight="normal" if italic else "bold",
                style="italic" if italic else "normal", family="monospace")


# ------------------------------------------------------ architecture ----
def architecture():
    fig, ax = plt.subplots(figsize=(12, 6.4))
    ax.set_xlim(0, 12)
    ax.set_ylim(0, 6.4)
    ax.axis("off")

    # top row: client -> proxy -> server
    _box(ax, 0.4, 4.2, 2.8, 1.3, "App / Game Client", sub="browser · WebTransport")
    _box(ax, 4.7, 4.2, 2.9, 1.3, "LEGILIMENS PROXY", sub=":4433  ·  QUIC MITM", hot=True)
    _box(ax, 8.9, 4.2, 2.7, 1.3, "Target Server", sub=":4434")

    _arrow(ax, (3.2, 4.85), (4.7, 4.85), "connects")
    _arrow(ax, (7.6, 4.85), (8.9, 4.85), "forwards")

    # bottom row: control API (left) <- inspector UI (under proxy)
    _box(ax, 4.7, 0.7, 2.9, 1.4, "Inspector UI", sub=":5173  ·  React")
    _box(ax, 0.4, 0.75, 2.8, 1.2, "Control API", sub=":4436  ·  FastAPI")

    # proxy -> UI : live events over the WebSocket (label kept to the right, clear space)
    _arrow(ax, (6.15, 4.2), (6.15, 2.1), "live events\nws://:4435", off=(1.25, 0))
    # UI -> control API : REST
    _arrow(ax, (4.7, 1.45), (3.2, 1.45), "REST control")
    # control API -> proxy : dashed, it reconfigures the live proxy
    _arrow(ax, (1.85, 1.95), (4.7, 4.2), "", color=MUTED, lw=1.5, style=(0, (4, 3)))
    ax.text(2.35, 2.45, "reconfigures\ncapture · tamper · target", ha="left", va="center",
            fontsize=8.4, color=MUTED, style="italic", family="monospace")

    ax.text(6, 6.05, "Architecture — one process, four services",
            ha="center", va="center", fontsize=14, fontweight="bold", color=DARK)

    fig.savefig(OUT / "architecture.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


# --------------------------------------------------------- frontend ----
def frontend():
    fig, ax = plt.subplots(figsize=(12, 6.0))
    ax.set_xlim(0, 12)
    ax.set_ylim(0, 6)
    ax.axis("off")

    # DATA IN (read path)
    _box(ax, 0.3, 3.9, 2.4, 1.2, "WebSocket", sub=":4435")
    _box(ax, 3.9, 3.9, 3.4, 1.2, "Zustand Store", sub="single source of truth", hot=True)
    _box(ax, 8.5, 3.5, 3.3, 1.9, "View Components\nTrafficLog · LatencyGraph\nStatusBar · StreamInspector", fs=9.8)
    _arrow(ax, (2.7, 4.5), (3.9, 4.5), "events")
    _arrow(ax, (7.3, 4.5), (8.5, 4.5), "re-render")

    # CONTROLS OUT (write path)
    _box(ax, 0.3, 0.8, 2.4, 1.2, "Control API", sub=":4436")
    _box(ax, 4.1, 0.7, 4.4, 1.4, "Control Components\nHeader · TargetConfig\nTamperConfig · AttackSimulator", fs=9.8)
    _arrow(ax, (4.1, 1.4), (2.7, 1.4), "REST POST")

    # the loop: backend reacts, new events flow back in
    _arrow(ax, (1.5, 2.0), (1.5, 3.9), "", color=MUTED, lw=1.5, style=(0, (4, 3)))
    ax.text(1.85, 2.95, "backend reacts →\nnew events\nflow back in", ha="left",
            va="center", fontsize=8.6, color=MUTED, style="italic")

    ax.text(6, 5.8, "Frontend — data in, controls out",
            ha="center", va="center", fontsize=14, fontweight="bold", color=DARK)

    fig.savefig(OUT / "frontend.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


if __name__ == "__main__":
    print("[assets] banner...")
    banner()
    print("[assets] architecture...")
    architecture()
    print("[assets] frontend...")
    frontend()
    print(f"[assets] done -> {OUT}")
