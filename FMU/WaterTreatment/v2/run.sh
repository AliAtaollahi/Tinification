#!/usr/bin/env bash
set -euo pipefail

echo ">>> Build Tank1 FMU..."
pythonfmu build -f tank1_fmu.py

echo ">>> Build Tank2 FMU..."
pythonfmu build -f tank2_fmu.py

echo ">>> Co-simulate two FMUs..."
python simulate_two_tanks.py --stop-time 300 --dt 1 --out TwoTanks.csv

echo ">>> Plot..."
python plot.py TwoTanks.csv

echo ">>> Done."
