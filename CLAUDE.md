# CLAUDE.md — NBR Extractor Project Context

## What this project does

Automated extractor for Brazilian technical standards (NBRs) from the ABNT Coleção portal
(`abntcolecao.com.br`). Credentials: empresa=`copasa`, usuario=`jose`, senha=`jose`.
Subscription expires 16/12/2026, ~562 norms total.

Approach: Playwright headless Chrome → screenshot each rendered PDF canvas page → OCR with
Tesseract (Portuguese). Mirrors exactly what a human user does on screen — no DRM bypass.

## Company context — InovaChina

Gabriel works at InovaChina, which imports and distributes the following product lines in Brazil.
The NBRs extracted are the technical standards their products must comply with:

| Business Line | Product | Relevant NBR categories |
|---|---|---|
| **Mazu** | Water meters (ultrasonic & mechanical) | hidrômetros, metrologia |
| **Tubom** | HDPE pipes & fittings (electrofusion/thermofusion) | tubulações, conexões |
| **Naiad** | Chemicals — polyacrylamide, coagulants, water treatment | químicos |
| **Kratos** | Seals / lacres | vedação |
| **Akeso** | Medical devices | medical |

## Keyword whitelist (pre-download filter)

Only norms matching these keywords get downloaded. Anything else is marked `ignored` in the DB.

```python
"hidrômetros": ["hidrômetro", "medidor de água", "medição de vazão", "ultrassônico",
                 "medidor eletromagnético", "água potável", "metrologia", "materiais de referência"]
"tubulações":  ["polietileno", "pead", "pe 100", "tubo", "tubulação", "pvc", "conduto",
                "adução", "distribuição de água", "esgoto"]
"conexões":    ["conexão", "eletrofusão", "termofusão", "luva", "cotovelo", "flange"]
"químicos":    ["poliacrilamida", "floculante", "coagulante", "tratamento de água",
                "sulfato de alumínio", "cloro", "hipoclorito"]
"vedação":     ["lacre"]
"akeso":       ["curativo", "seringas"]
```

If you need to add/remove keywords, edit `CATEGORY_KEYWORDS` in `nbr_extractor.py`. The
`is_relevant()` function checks all categories — if title matches none, norm is skipped.

## Google Drive — Normas folder

Extracted norms are uploaded to Google Drive under `gabriel@inovachina.com`:

| Drive Folder | Drive ID | Maps to |
|---|---|---|
| Normas (root) | `12bMZqHRuHWHKLFPcecLdoW_fFPV1DCwW` | Shared with all @inovachina.com |
| Mazu | `1pPAflJEb6gdrqdTtFyZ-iwLrE7iF52r5` | hidrômetros |
| Tubom | `181oOBC00PpsyugccGqdRI9Ni2VENbaEK` | tubulações + conexões |
| Naiad | `15cVhQktjQ46HpQgABehemD24lpu5g-i3` | químicos |
| Kratos | `1J4SAaEKEAw56kG_yjDz_ELUZ7aoic63W` | vedação |
| Akeso | `1cTGZHZ8VEMahCRaXpGlCrFub7nzWryYO` | medical |

The folder is shared domain-wide (writer access) with `inovachina.com`.

## GWS CLI auth

Google Workspace CLI (`gws`) is installed via npm. OAuth client stored in GCP Secret Manager:

```bash
# Restore client_secret.json on a new machine:
gcloud secrets versions access latest --secret=gws-oauth-client-secret --project=nbrs-491617 > ~/.config/gws/client_secret.json

# Then authenticate:
gws auth login --services drive
```

GCP project: `nbrs-491617`
Owner: `gabrielreginatto@gmail.com`
`gabriel@inovachina.com` has `serviceUsageConsumer` + `secretAccessor` roles.

## Cron schedule

Two batches of 10 norms/day, Mon–Fri (stays under the server's ~10 req/session throttle):

```
0  8 * * 1-5  run_daily.sh     → morning batch
0 17 * * 1-5  run_afternoon.sh → afternoon batch
```

Check crontab: `crontab -l`

## Database — norms.db (SQLite, gitignored)

```bash
# Progress summary:
sqlite3 norms.db "SELECT extraction_status, COUNT(*) FROM norms GROUP BY extraction_status"

# List done norms by category:
sqlite3 norms.db "SELECT category, code, title FROM norms WHERE extraction_status='done' ORDER BY category"
```

Statuses: `pending` → `done` | `error` (retried next batch) | `ignored` (filtered out, never retried)

## Server behavior / anti-scraping

- No Cloudflare, no WAF
- reCAPTCHA v3 (invisible, score-based) — Playwright passes it fine
- Soft connection reset after ~10 consecutive rapid requests — stay at 10/batch
- Session: ASP.NET `HttpOnly; SameSite=Lax` cookie; `ensure_logged_in()` handles re-auth
- No `robots.txt` published

## Output structure

```
output/
├── hidrômetros/ABNT NBR 15538-2023/
│   ├── page_001.png … page_N.png
│   ├── text.txt          ← full OCR'd text (Tesseract por)
│   └── metadata.json     ← code, title, date, status, summary, category
├── tubulações/
├── conexões/
├── químicos/
├── vedação/
└── outros/
```

## Key files

| File | Purpose |
|---|---|
| `nbr_extractor.py` | Main script — login, enumerate, filter, screenshot, OCR |
| `run_daily.sh` | Morning cron (8am, --batch 10) |
| `run_afternoon.sh` | Afternoon cron (5pm, --batch 10) |
| `norms.db` | SQLite progress DB (gitignored) |
| `output/` | Extracted norms (gitignored) |
| `logs/` | Cron output logs (gitignored) |
