#!/usr/bin/env bash
set -euo pipefail

echo ">>> Build Tank1 FMU..."
pythonfmu build -f tank1_fmu.py

echo ">>> Build Tank2 FMU..."
pythonfmu build -f tank2_fmu.py

echo ">>> Co-simulate two FMUs using Rebeca controller..."
python simulate_two_tanks.py \
    --stop-time 3000 \
    --dt 1 \
    --controller rebeca \
    --rebeca RebecaCore.txt \
    --out TwoTanks.csv

echo ">>> Plot..."
python plot.py TwoTanks.csv

echo ">>> Done."
