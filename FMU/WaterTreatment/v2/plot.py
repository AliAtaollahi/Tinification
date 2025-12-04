# plot_two_tanks.py
import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from fmpy import read_csv


def main():
    if len(sys.argv) < 2:
        print("Usage: python plot_two_tanks.py TwoTanks.csv")
        raise SystemExit(1)

    csv_file = sys.argv[1]
    r = read_csv(csv_file)
    cols = r.dtype.names
    print("Columns:", cols)

    t = r["time"]
    L1 = r["tank1_level"]
    L2 = r["tank2_level"]
    v1 = r["v1_cmd"]
    v2 = r["v2_cmd"]
    v3 = r["v3_cmd"]

    fig, ax = plt.subplots(3, 1, sharex=True, figsize=(9, 9))

    # Tank1 (show up to 700 as you asked)
    ax[0].plot(t, L1, label="tank1 level [m]")
    ax[0].set_ylabel("tank1 [m]")
    ax[0].set_ylim(0, 700)
    ax[0].grid(True, linestyle="--", linewidth=0.5, alpha=0.5)
    ax[0].legend(loc="best", fontsize=8)

    # Tank2
    ax[1].plot(t, L2, label="tank2 level [m]")
    ax[1].set_ylabel("tank2 [m]")
    ax[1].set_ylim(0, 2200)
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

    # x ticks ~12
    x_min, x_max = float(np.min(t)), float(np.max(t))
    step = max(1, int((x_max - x_min) // 12) or 1)
    ax[2].set_xticks(np.arange(np.floor(x_min), np.ceil(x_max) + 1, step))

    fig.tight_layout()
    out = "TwoTanks_output.png"
    fig.savefig(out, dpi=150)
    print(f"Saved plot to {out}")


if __name__ == "__main__":
    main()
