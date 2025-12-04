#!/usr/bin/env bash
set -euo pipefail

FMU_SCRIPT="water_tank_fmu.py"
FMU_FILE="WaterTank.fmu"
CSV_FILE="WaterTank.csv"
STOP_TIME=300
STEP_SIZE=0.5

echo ">>> Building FMU..."
pythonfmu build -f "$FMU_SCRIPT"

echo ">>> Simulating FMU..."
fmpy simulate "$FMU_FILE" --stop-time "$STOP_TIME" --step-size "$STEP_SIZE" --output-file "$CSV_FILE"

echo ">>> Plotting..."
python plot.py "$CSV_FILE"

echo ">>> Done."
