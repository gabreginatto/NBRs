# NBR Extractor — ABNT Coleção

Automated extractor for Brazilian technical standards (NBRs) from the [ABNT Coleção](https://www.abntcolecao.com.br) portal.

## How it works

1. Logs into the portal using Playwright (headless Chrome)
2. Enumerates all norms in the subscribed collection
3. For each norm: opens the PDF viewer → screenshots each rendered page → OCRs locally with Tesseract
4. Saves page images, extracted text, and metadata per norm
5. Tracks progress in SQLite — safe to stop and resume at any time

This approach mirrors what a human user does on screen (no download bypass or DRM circumvention).

## Output structure

```
output/
├── hidrômetros/
│   └── ABNT NBR 15538-2023/
│       ├── page_001.png
│       ├── page_002.png
│       ├── text.txt        ← full OCR'd text
│       └── metadata.json   ← code, title, date, status, summary
├── tubulações/
├── conexões/
├── químicos/
├── vedação/
└── outros/
```

Norms are auto-classified by keywords in their title. Categories:

| Folder | Keywords matched |
|--------|-----------------|
| `hidrômetros/` | hidrômetro, medidor de água, água potável, ultrassônico… |
| `tubulações/` | polietileno, PEAD, tubo, tubulação… |
| `conexões/` | conexão, eletrofusão, termofusão, luva, cotovelo… |
| `químicos/` | poliacrilamida, floculante, coagulante, tratamento de água… |
| `vedação/` | vedação, gaxeta, junta, anel de borracha, selo… |
| `outros/` | everything else |

## Setup

```bash
pip install playwright pytesseract pillow
playwright install chromium
brew install tesseract tesseract-lang   # includes Portuguese (por)
```

## Usage

```bash
# First run: populate the database with all norms in the collection
python3 nbr_extractor.py --enumerate-only

# Process 10 norms (safe daily batch)
python3 nbr_extractor.py --batch 10

# Verbose output (shows page-by-page progress)
python3 nbr_extractor.py --batch 10 --verbose

# Preview what would be processed without downloading
python3 nbr_extractor.py --dry-run
```

## Daily scheduling (cron)

Two batches of 10/day — morning (8am) and afternoon (5pm), Mon–Fri.
Run these commands once in your terminal to install both cron jobs:

```bash
(crontab -l 2>/dev/null; echo "0 8 * * 1-5 /Users/gabrielreginatto/Desktop/Code/NBRs/run_daily.sh >> /Users/gabrielreginatto/Desktop/Code/NBRs/logs/extractor.log 2>&1") | crontab -
(crontab -l 2>/dev/null; echo "0 17 * * 1-5 /Users/gabrielreginatto/Desktop/Code/NBRs/run_afternoon.sh >> /Users/gabrielreginatto/Desktop/Code/NBRs/logs/extractor.log 2>&1") | crontab -
```

Verify with `crontab -l`. At 562 norms and 20/day (2×10), the full collection completes in ~29 weekdays (~6 weeks).

## Progress tracking

Progress is stored in `norms.db` (SQLite). Check status anytime:

```bash
sqlite3 norms.db "SELECT extraction_status, COUNT(*) FROM norms GROUP BY extraction_status"
```

## Notes

- `norms.db`, `output/`, and `logs/` are excluded from version control (local data only)
- The watermark visible in some pages (CNPJ number) is the licensed copy's watermark — normal
- If a norm fails, it's marked `error` in the DB and retried on the next run
