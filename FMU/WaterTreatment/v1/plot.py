# plot.py
import sys
import os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from fmpy import read_csv


def main():
    if len(sys.argv) < 2:
        print("Usage: python plot.py MODEL_NAME_OR_CSV")
        print("Examples:")
        print("  python plot.py WaterTank      # uses WaterTank.csv")
        print("  python plot.py result.csv")
        sys.exit(1)

    arg = sys.argv[1]
    if arg.endswith(".csv"):
        csv_file = arg
        model_name = os.path.splitext(os.path.basename(arg))[0]
    else:
        model_name = arg
        csv_file = f"{model_name}.csv"

    if not os.path.exists(csv_file):
        print(f"CSV file '{csv_file}' not found.")
        sys.exit(1)

    r = read_csv(csv_file)
    cols = r.dtype.names
    print("Columns:", cols)

    time = r["time"]
    level = r["level"] if "level" in cols else None
    v1_cmd = r["v1_cmd"] if "v1_cmd" in cols else None
    v2_cmd = r["v2_cmd"] if "v2_cmd" in cols else None

    if level is None:
        raise RuntimeError("No 'level' column found in CSV.")

    # square plot
    fig = plt.figure(figsize=(10, 10))
    gs = fig.add_gridspec(2, 1, height_ratios=[3, 1], hspace=0.15)

    ax1 = fig.add_subplot(gs[0, 0])
    ax2 = fig.add_subplot(gs[1, 0], sharex=ax1)

    # ---- top: level ----
    ax1.plot(time, level, linewidth=1.6, label="level [m]")
    ax1.set_title("Water tank output")
    ax1.set_ylabel("level [m]")

    # y-axis 0..700 (as you asked)
    ax1.set_ylim(0, 700)
    ax1.set_yticks(np.arange(0, 701, 50))

    ax1.grid(True, linestyle="--", linewidth=0.5, alpha=0.5)
    ax1.legend(loc="best", fontsize=9)

    # ---- bottom: valve commands ----
    if v1_cmd is not None and v2_cmd is not None:
        ax2.step(time, v1_cmd, where="post", linewidth=1.2, alpha=0.8, label="v1_cmd")
        ax2.step(time, v2_cmd, where="post", linewidth=1.2, alpha=0.8, label="v2_cmd")
        ax2.set_ylim(-0.1, 1.1)
        ax2.set_yticks([0, 1])
        ax2.set_ylabel("valve")
        ax2.grid(True, linestyle="--", linewidth=0.5, alpha=0.5)
        ax2.legend(loc="best", fontsize=9)

    ax2.set_xlabel("time [s]")

    # x ticks (~12 ticks)
    x_min, x_max = float(np.min(time)), float(np.max(time))
    step_x = max(1, int((x_max - x_min) // 12) or 1)
    ax2.set_xticks(np.arange(np.floor(x_min), np.ceil(x_max) + 1, step_x))

    fig.tight_layout()
    out = f"{model_name}_output.png"
    fig.savefig(out, dpi=150)
    print(f"Saved plot to {out}")


if __name__ == "__main__":
    main()
