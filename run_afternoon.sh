#!/bin/bash
# Afternoon NBR extractor runner (batch 2 of 2 — 10 norms)
# Cron: 0 17 * * 1-5 /Users/gabrielreginatto/Desktop/Code/NBRs/run_afternoon.sh

WORK_DIR="/Users/gabrielreginatto/Desktop/Code/NBRs"
cd "$WORK_DIR"

echo "[$(date)] Starting afternoon NBR extraction batch (10 norms)..."
python3 "$WORK_DIR/nbr_extractor.py" --batch 10
echo "[$(date)] Afternoon batch done."
