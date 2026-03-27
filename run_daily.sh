#!/bin/bash
# Morning NBR extractor runner (batch 1 of 2 — 10 norms)
# Cron: 0 8 * * 1-5 /Users/gabrielreginatto/Desktop/Code/NBRs/run_daily.sh

WORK_DIR="/Users/gabrielreginatto/Desktop/Code/NBRs"
cd "$WORK_DIR"

echo "[$(date)] Starting morning NBR extraction batch (10 norms)..."
python3 "$WORK_DIR/nbr_extractor.py" --batch 10
echo "[$(date)] Morning batch done."
