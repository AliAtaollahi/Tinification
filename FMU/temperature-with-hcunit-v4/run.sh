#!/usr/bin/env bash
set -euo pipefail

FMU_SCRIPT="temperature_fmu.py"
FMU_FILE="Temperature.fmu"
CSV_FILE="Temperature.csv"
STOP_TIME=500

echo ">>> Building FMU..."
pythonfmu build -f "$FMU_SCRIPT"

echo ">>> Simulating FMU..."
fmpy simulate "$FMU_FILE" --stop-time "$STOP_TIME" --output-file "$CSV_FILE"

echo ">>> Plotting results..."
python plot.py "$CSV_FILE"

echo ">>> Done."
