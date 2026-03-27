#!/bin/bash
# Daily NBR extractor runner
# Run manually or via cron: 0 8 * * 1-5 /Users/gabrielreginatto/Desktop/Code/NBRs/run_daily.sh

WORK_DIR="/Users/gabrielreginatto/Desktop/Code/NBRs"
cd "$WORK_DIR"

echo "[$(date)] Starting daily NBR extraction batch..."
python3 "$WORK_DIR/nbr_extractor.py" --batch 50
echo "[$(date)] Done."
