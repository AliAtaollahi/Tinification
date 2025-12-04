# plot_two_tanks.py
import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from fmpy import read_csv


def _auto_ylim(y: np.ndarray, *, floor: float = 0.0, pad_frac: float = 0.08) -> tuple[float, float]:
    """Return (ymin, ymax) with padding so curves don't clip."""
    y = np.asarray(y, dtype=float)
    y = y[np.isfinite(y)]
    if y.size == 0:
        return floor, floor + 1.0
    ymin = min(float(np.min(y)), floor)
    ymax = float(np.max(y))
    if ymax <= ymin + 1e-12:
        return ymin, ymin + 1.0
    pad = (ymax - ymin) * pad_frac
    return ymin, ymax + pad


def main():
    if len(sys.argv) < 2:
        print("Usage: python plot_two_tanks.py TwoTanks.csv [output.png]")
        raise SystemExit(1)

    csv_file = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) >= 3 else "TwoTanks_output.png"

    r = read_csv(csv_file)
    cols = r.dtype.names
    print("Columns:", cols)

    t = r["time"].astype(float)
    L1 = r["tank1_level"].astype(float)
    L2 = r["tank2_level"].astype(float)
    v1 = r["v1_cmd"].astype(float)
    v2 = r["v2_cmd"].astype(float)
    v3 = r["v3_cmd"].astype(float)

    fig, ax = plt.subplots(3, 1, sharex=True, figsize=(10, 9))

    # Tank1
    ax[0].plot(t, L1, label="tank1 level [m]")
    ax[0].set_ylabel("tank1 [m]")
    y0, y1 = _auto_ylim(L1, floor=0.0, pad_frac=0.10)
    ax[0].set_ylim(y0, y1)
    ax[0].grid(True, linestyle="--", linewidth=0.5, alpha=0.5)
    ax[0].legend(loc="best", fontsize=8)

    # Tank2 (AUTO SCALE so you see higher)
    ax[1].plot(t, L2, label="tank2 level [m]")
    ax[1].set_ylabel("tank2 [m]")
    y0, y1 = _auto_ylim(L2, floor=0.0, pad_frac=0.10)
    ax[1].set_ylim(y0, y1)
    ax[1].grid(True, linestyle="--", linewidth=0.5, alpha=0.5)
    ax[1].legend(loc="best", fontsize=8)

    # Valves
    ax[2].step(t, v1, where="post", label="v1")
    ax[2].step(t, v2, where="post", label="v2")
    ax[2].step(t, v3, where="post", label="v3")
    ax[2].set_ylabel("valve")
    ax[2].set_ylim(-0.1, 1.1)
    ax[2].set_xlabel("time [s]")
    ax[2].grid(True, linestyle="--", linewidth=0.5, alpha=0.5)
    ax[2].legend(loc="best", fontsize=8)

    # nicer x ticks (~12)
    tmin, tmax = float(np.nanmin(t)), float(np.nanmax(t))
    ticks = np.linspace(tmin, tmax, num=13)
    ax[2].set_xticks(ticks)

    fig.tight_layout()
    fig.savefig(out, dpi=150)
    print(f"Saved plot to {out}")


if __name__ == "__main__":
    main()
