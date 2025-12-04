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
        print("  python plot.py Toggle      # uses Toggle.csv")
        print("  python plot.py result.csv  # uses result.csv")
        sys.exit(1)

    model_arg = sys.argv[1]

    # If user passes just "Toggle", use Toggle.csv
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
    fig, ax = plt.subplots(figsize=(5, 5))   # square figure

    ax.step(time, y, where="post")

    ax.set_xlabel("time [s]")
    ax.set_ylabel(y_name)
    ax.set_title(f"{model_name} output ({y_name})")

    # Integer ticks on both axes
    x_min, x_max = time.min(), time.max()
    y_min, y_max = y.min(), y.max()

    x_ticks = np.arange(np.floor(x_min), np.ceil(x_max) + 1, 1)
    y_ticks = np.arange(np.floor(y_min), np.ceil(y_max) + 1, 1)

    ax.set_xticks(x_ticks)
    ax.set_yticks(y_ticks)

    # Equal scaling: 1 unit in x == 1 unit in y
    ax.set_aspect("equal", adjustable="box")

    ax.grid(True, linestyle="--", linewidth=0.5)
    fig.tight_layout()

    # Save as <model_name>_output.png
    png_file = f"{model_name}_output.png"
    fig.savefig(png_file, dpi=150)
    print(f"Saved plot to {png_file}")


if __name__ == "__main__":
    main()
