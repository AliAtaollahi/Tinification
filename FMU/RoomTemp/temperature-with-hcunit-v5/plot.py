import sys
import os
import numpy as np
import matplotlib

# Use non-GUI backend (no Qt / xcb needed)
matplotlib.use("Agg")

import matplotlib.pyplot as plt
from fmpy import read_csv


def main():
    if len(sys.argv) < 2:
        print("Usage: python plot.py MODEL_NAME_OR_CSV")
        print("Examples:")
        print("  python plot.py Temperature   # uses Temperature.csv")
        print("  python plot.py result.csv    # uses result.csv")
        sys.exit(1)

    model_arg = sys.argv[1]

    # If user passes just "Temperature", use Temperature.csv
    if model_arg.endswith(".csv"):
        csv_file = model_arg
        model_name = os.path.splitext(model_arg)[0]
    else:
        model_name = model_arg
        csv_file = f"{model_name}.csv"

    if not os.path.exists(csv_file):
        print(f"CSV file '{csv_file}' not found.")
        sys.exit(1)

    # Read the simulation result
    result = read_csv(csv_file)
    print("Columns:", result.dtype.names)

    # Pick the first column that is not 'time'
    columns = [c for c in result.dtype.names if c != "time"]
    if not columns:
        print("No variable columns found (only 'time').")
        sys.exit(1)

    y_name = columns[0]
    time = result["time"]
    y = result[y_name]

    # ----- plotting -----
    # square figure: x * x
    fig, ax = plt.subplots(figsize=(5, 5))

    ax.plot(time, y)

    ax.set_xlabel("time [s]")
    ax.set_ylabel(y_name)
    ax.set_title(f"{model_name} output ({y_name})")

    # X ticks: integers, but not every single one (aim ~12 ticks)
    x_min, x_max = time.min(), time.max()
    step_x = max(1, int((x_max - x_min) // 12) or 1)
    x_ticks = np.arange(np.floor(x_min), np.ceil(x_max) + 1, step_x)
    ax.set_xticks(x_ticks)

    # ----- Y axis fixed to [10, 30] -----
    ax.set_ylim(11, 26)

    # Y ticks: integers between 10 and 30
    y_ticks = np.arange(11, 26, 1)
    ax.set_yticks(y_ticks)

    # Grid
    ax.grid(True, linestyle="--", linewidth=0.5, alpha=0.5)

    # Slight layout padding so labels don’t get clipped
    fig.tight_layout()

    # Save as <model_name>_output.png
    png_file = f"{model_name}_output.png"
    fig.savefig(png_file, dpi=150)
    print(f"Saved plot to {png_file}")


if __name__ == "__main__":
    main()
