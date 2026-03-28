#!/usr/bin/env python3
"""
ABNT Coleção NBR Extractor
Approach: Playwright browser automation → screenshot each rendered PDF page → OCR locally.
This mirrors exactly what a human user does on screen (no download bypass).

Usage:
  python nbr_extractor.py --enumerate-only   # Just populate DB with norm list
  python nbr_extractor.py --batch 20         # Process 20 norms (default)
  python nbr_extractor.py --batch 1 --verbose
  python nbr_extractor.py --dry-run          # Show what would be processed, no downloads
"""

import argparse
import json
import re
import sqlite3
import sys
import time
import random
from datetime import datetime
from pathlib import Path

import pytesseract
from PIL import Image
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# ── Config ──────────────────────────────────────────────────────────────────
BASE_URL = "https://www.abntcolecao.com.br"
CREDENTIALS = {"empresa": "copasa", "usuario": "jose", "senha": "jose"}
OUTPUT_DIR = Path("output")
DB_PATH = Path("norms.db")
LOG_DIR = Path("logs")
DELAY_MIN = 5   # seconds between norms
DELAY_MAX = 12

CATEGORY_KEYWORDS = {
    "hidrômetros": [
        "hidrômetro", "hidrometro",
        "medidor de água", "medidor de agua",
        "medidores de água", "medidores de agua",
        "medição de vazão",
        "ultrassonico", "ultrassônico",
        "medidor eletromagnético",
        "medidor de débito",
        "hidrometria",
        "metrologia",
        "materiais de referência",
    ],
    "tubulações": [
        "polietileno", "pead", "pe 100", "tubo", "tubulação", "tubulacao",
        "alta densidade", "conduto", "duto", "tubagem", "canalização",
        "pvc", "adução", "distribuição de água", "esgoto",
    ],
    "conexões": [
        "conexão", "conexao", "eletrofusão", "eletrofusao",
        "termofusão", "termofusao", "fitting", "luva", "cotovelo", "tê", "adaptador",
        "união", "flange", "bocal",
    ],
    "químicos": [
        "poliacrilamida", "floculante", "coagulante",
        "tratamento de água", "tratamento de agua",
        "coagulação", "floculação",
        "sulfato de alumínio", "cloro", "hipoclorito",
    ],
    "vedação": [
        "lacre",
    ],
    "akeso": [
        "curativo", "seringas",
    ],
}


# ── Database ─────────────────────────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS norms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            norm_key TEXT UNIQUE NOT NULL,
            viewer_key TEXT,
            code TEXT,
            title TEXT,
            date TEXT,
            norm_status TEXT,
            pages INTEGER,
            summary TEXT,
            category TEXT,
            output_dir TEXT,
            extraction_status TEXT DEFAULT 'pending',
            error_msg TEXT,
            processed_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    return conn


def _kw_match(kw: str, title_lower: str) -> bool:
    """Match keyword as a whole word, allowing for Portuguese plural -s suffix."""
    return bool(re.search(r'(?<![a-záàãâéêíóôõúüçñ])' + re.escape(kw) + r's?(?![a-záàãâéêíóôõúüçñ])', title_lower))


def classify_category(title: str) -> str:
    title_lower = title.lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(_kw_match(kw, title_lower) for kw in keywords):
            return category
    return "outros"


def is_relevant(title: str) -> bool:
    """Return True only if the norm title matches at least one keyword as a whole word."""
    title_lower = title.lower()
    return any(
        _kw_match(kw, title_lower)
        for keywords in CATEGORY_KEYWORDS.values()
        for kw in keywords
    )


def pre_filter_pending(conn, verbose=False) -> int:
    """Mark pending norms with irrelevant titles as ignored before downloading."""
    pending = conn.execute(
        "SELECT id, code, title FROM norms WHERE extraction_status='pending' AND title IS NOT NULL AND title != ''"
    ).fetchall()
    ignored = 0
    for row in pending:
        if not is_relevant(row["title"]):
            conn.execute("UPDATE norms SET extraction_status='ignored' WHERE id=?", (row["id"],))
            ignored += 1
            if verbose:
                print(f"  [skip] {row['code']} — {row['title'][:60]}")
    conn.commit()
    if ignored:
        print(f"[*] Pre-filter: marked {ignored} irrelevant norms as ignored")
    return ignored


# ── Login ────────────────────────────────────────────────────────────────────
def login(page, verbose=False):
    if verbose:
        print("[*] Logging in...")
    page.goto(BASE_URL + "/")
    page.wait_for_load_state("networkidle", timeout=15000)

    # Fill login form using role-based locators (the page uses table layout, no IDs)
    page.get_by_role("textbox", name="empresa").fill(CREDENTIALS["empresa"])
    page.get_by_role("textbox", name="usuário").fill(CREDENTIALS["usuario"])
    page.get_by_role("textbox", name="senha").fill(CREDENTIALS["senha"])
    page.evaluate("__doPostBack('cmdLogin', '')")
    page.wait_for_url("**/colecao.aspx", timeout=20000)

    if verbose:
        print("[✓] Logged in")


def ensure_logged_in(page, verbose=False):
    """Re-login if session expired (redirected to login page)."""
    if "colecao.aspx" not in page.url and "colecaogrid" not in page.url and "normavw" not in page.url and "pdfview" not in page.url:
        if verbose:
            print("[!] Session expired, re-logging in...")
        login(page, verbose)


# ── Norm enumeration ──────────────────────────────────────────────────────────
def enumerate_norms(page, conn, verbose=False):
    """Navigate to the norm grid and extract all norm entries into DB."""
    print("[*] Enumerating all norms in collection...")

    page.evaluate("__doPostBack('ctl00$cphPagina$cmdNormaAll', '')")
    page.wait_for_url("**/colecaogrid.aspx", timeout=20000)
    page.wait_for_load_state("networkidle")

    grid_id = "ctl00$cphPagina$gvNorma"
    page_num = 1

    while True:
        if verbose:
            print(f"  → Parsing grid page {page_num}...")

        # Extract all norm links from the current grid page (deduplicate by Q key)
        seen_keys = set()
        for link in page.query_selector_all('a[href*="normavw.aspx"]'):
            href = link.get_attribute("href") or ""
            m = re.search(r'Q=([^&"\']+)', href)
            if not m:
                continue
            norm_key = m.group(1)
            if norm_key in seen_keys:
                continue
            seen_keys.add(norm_key)

            code = link.text_content().strip()
            conn.execute(
                "INSERT OR IGNORE INTO norms (norm_key, code) VALUES (?, ?)",
                (norm_key, code if re.match(r"^ABNT", code) else ""),
            )

        conn.commit()

        if not seen_keys:
            break  # No norms found on this page, we're done

        # Check if there's a next page by looking for a link for page_num+1 or "..."
        next_page = page_num + 1
        next_link = page.query_selector(
            f'a[href*="Page${next_page}"], a[href*="Page$Next"]'
        )
        if not next_link:
            # Also check for "..." which points to Page$(next_page) beyond visible range
            dots = page.query_selector_all('td a[href*="__doPostBack"]')
            for a in dots:
                href = a.get_attribute("href") or ""
                if f"Page${next_page}" in href or (a.text_content().strip() == "..." ):
                    next_link = a
                    break

        if not next_link:
            break  # No more pages

        # Navigate to next page via postback (more reliable than clicking)
        page.evaluate(f"__doPostBack('{grid_id}', 'Page${next_page}')")
        page.wait_for_load_state("networkidle")
        time.sleep(random.uniform(1, 2))
        page_num = next_page

    total_in_db = conn.execute("SELECT COUNT(*) FROM norms").fetchone()[0]
    print(f"[✓] Enumeration complete: {total_in_db} norms in DB")
    return total_in_db


# ── Metadata extraction ───────────────────────────────────────────────────────
def parse_metadata(page) -> dict:
    """Parse the normavw.aspx detail page using JavaScript to walk the DOM."""
    result = page.evaluate("""() => {
        const meta = {};
        // Walk all table rows looking for label/value pairs
        const rows = document.querySelectorAll('tr');
        for (const row of rows) {
            const cells = row.querySelectorAll('td');
            if (cells.length < 2) continue;
            const labelText = cells[0].innerText.trim().replace(/:$/, '').trim();
            // Find the last non-empty cell as the value
            let valueText = '';
            for (let i = cells.length - 1; i >= 1; i--) {
                const t = cells[i].innerText.trim();
                if (t && t !== labelText) { valueText = t; break; }
            }
            if (!labelText || !valueText) continue;
            if (/^C[oó]digo$/i.test(labelText) && valueText.startsWith('ABNT'))
                meta.code = valueText;
            else if (/^Status$/i.test(labelText))
                meta.norm_status = valueText;
            else if (/^Data de Publica/i.test(labelText))
                meta.date = valueText;
            else if (/^T[íi]tulo Idioma Principal/i.test(labelText))
                meta.title = valueText;
            else if (/^N[º°] de P[aá]ginas/i.test(labelText))
                meta.pages = valueText;
            else if (/^Resumo/i.test(labelText))
                meta.summary = valueText;
            else if (/^Comit[eê]/i.test(labelText))
                meta.committee = valueText;
            else if (/^Organismo/i.test(labelText))
                meta.organism = valueText;
        }
        return meta;
    }""")
    return result or {}


# ── PDF page screenshotter ────────────────────────────────────────────────────
def screenshot_pdf_pages(page, norm_dir: Path, verbose=False) -> list[str]:
    """Screenshot each rendered canvas page in the pdf.js viewer."""
    # Wait for pdf.js to load
    try:
        page.wait_for_function("typeof PDFViewerApplication !== 'undefined' && PDFViewerApplication.pagesCount > 0", timeout=30000)
    except PlaywrightTimeout:
        raise RuntimeError("PDF viewer did not load (timeout)")

    total_pages = page.evaluate("PDFViewerApplication.pagesCount")
    if verbose:
        print(f"    PDF has {total_pages} pages")

    # Set zoom to 150% for better OCR quality
    try:
        page.evaluate("PDFViewerApplication.pdfViewer.currentScale = 1.5")
        time.sleep(1)
    except Exception:
        pass

    page_images = []

    for page_num in range(1, total_pages + 1):
        # Navigate pdf.js to this page
        page.evaluate(f"PDFViewerApplication.page = {page_num}")
        time.sleep(1.5)  # wait for canvas render

        # Try to screenshot the canvas for this specific page
        canvas_sel = f"#pageContainer{page_num} canvas, .page[data-page-number='{page_num}'] canvas"
        canvas = page.query_selector(canvas_sel)

        if not canvas:
            # Fallback: scroll to page and screenshot viewport
            page.evaluate(f"document.getElementById('pageContainer{page_num}')?.scrollIntoView()")
            time.sleep(0.5)
            canvas = page.query_selector(f"canvas")

        img_path = norm_dir / f"page_{page_num:03d}.png"

        if canvas:
            canvas.screenshot(path=str(img_path))
        else:
            # Last resort: full page screenshot cropped
            page.screenshot(path=str(img_path))

        page_images.append(str(img_path))

        if verbose and page_num % 10 == 0:
            print(f"    → {page_num}/{total_pages} pages captured")

    return page_images


# ── OCR ───────────────────────────────────────────────────────────────────────
def ocr_pages(image_paths: list[str], verbose=False) -> str:
    """Run tesseract OCR on each page image and combine into full text."""
    all_text = []
    for i, img_path in enumerate(image_paths, 1):
        img = Image.open(img_path)
        text = pytesseract.image_to_string(img, lang="por")
        all_text.append(text)
        if verbose and i % 10 == 0:
            print(f"    → OCR: {i}/{len(image_paths)} pages done")
    return "\n\n--- PAGE BREAK ---\n\n".join(all_text)


# ── Process one norm ─────────────────────────────────────────────────────────
def process_norm(page, row, dry_run=False, verbose=False):
    norm_key = row["norm_key"]
    norm_id = row["id"]

    if verbose:
        print(f"\n[*] Processing norm {norm_id}: key={norm_key[:20]}...")

    # Step 1: Get metadata from detail page
    detail_url = f"{BASE_URL}/normavw.aspx?Q={norm_key}"
    page.goto(detail_url)
    page.wait_for_load_state("networkidle", timeout=15000)
    ensure_logged_in(page, verbose)

    metadata = parse_metadata(page)
    # Prefer the code captured during enumeration (grid link text) as it's more accurate
    code = row["code"] or metadata.get("code") or f"NORM_{norm_id}"
    title = metadata.get("title", "")
    category = classify_category(title)

    if verbose:
        print(f"    Code: {code}")
        print(f"    Title: {title[:60]}...")
        print(f"    Category: {category}")

    # Pre-filter: skip norms not relevant to InovaChina's product lines
    if title and not is_relevant(title):
        if verbose:
            print(f"    [skip] Not relevant — ignoring")
        return {"code": code, "title": title, "category": "ignored", "_ignored": True}

    if dry_run:
        return {"code": code, "title": title, "category": category}

    # Extract viewer key from the Visualizar onclick
    onclick = page.get_attribute('img[alt="Visualizar esta norma"]', "onclick") or ""
    m = re.search(r"ViewNorma\('([^']+)'\)", onclick)
    if not m:
        raise RuntimeError("Could not find viewer key (Visualizar button not found)")
    viewer_key = m.group(1)

    # Create output directory for this norm (replace filesystem-unsafe chars with dash)
    safe_code = re.sub(r'[/\\:*?"<>|]', "-", code).strip()
    norm_dir = OUTPUT_DIR / category / safe_code
    norm_dir.mkdir(parents=True, exist_ok=True)

    time.sleep(random.uniform(2, 4))

    # Step 2: Open PDF viewer
    viewer_url = f"{BASE_URL}/pdfview/viewer.aspx?Q={viewer_key}&locale=pt-BR&Req="
    if verbose:
        print(f"    Opening viewer: {viewer_url[:60]}...")
    page.goto(viewer_url)
    page.wait_for_load_state("networkidle", timeout=20000)

    # Step 3: Screenshot each page
    image_paths = screenshot_pdf_pages(page, norm_dir, verbose)

    # Step 4: OCR all pages
    if verbose:
        print(f"    Running OCR on {len(image_paths)} pages...")
    full_text = ocr_pages(image_paths, verbose)

    # Step 5: Save outputs
    metadata.update({
        "viewer_key": viewer_key,
        "category": category,
        "total_pages_captured": len(image_paths),
        "extracted_at": datetime.now().isoformat(),
    })
    (norm_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (norm_dir / "text.txt").write_text(full_text, encoding="utf-8")

    if verbose:
        print(f"    [✓] Saved to {norm_dir}")

    return {
        "code": code,
        "title": title,
        "category": category,
        "viewer_key": viewer_key,
        "pages": len(image_paths),
        "output_dir": str(norm_dir),
        "norm_status": metadata.get("norm_status", ""),
        "date": metadata.get("date", ""),
        "summary": metadata.get("summary", ""),
    }


# ── Main batch runner ─────────────────────────────────────────────────────────
def run(batch_size=20, enumerate_only=False, dry_run=False, verbose=False):
    OUTPUT_DIR.mkdir(exist_ok=True)
    LOG_DIR.mkdir(exist_ok=True)

    conn = init_db()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()

        # Login
        login(page, verbose=verbose)

        # Always enumerate if DB is empty
        total = conn.execute("SELECT COUNT(*) FROM norms").fetchone()[0]
        if total == 0 or enumerate_only:
            enumerate_norms(page, conn, verbose=verbose)
            if enumerate_only:
                browser.close()
                conn.close()
                return

        # Pre-filter: ignore norms whose titles don't match any relevant keyword
        pre_filter_pending(conn, verbose=verbose)

        # Get pending norms (include previous errors for retry)
        pending = conn.execute(
            "SELECT * FROM norms WHERE extraction_status IN ('pending', 'error') ORDER BY extraction_status DESC, id LIMIT ?",
            (batch_size,)
        ).fetchall()

        if not pending:
            print("[✓] No pending norms — all done!")
            browser.close()
            conn.close()
            return

        print(f"[*] Processing {len(pending)} norms (batch_size={batch_size})")
        done = 0
        errors = 0

        for i, norm in enumerate(pending, 1):
            code_display = norm["code"] or f"ID:{norm['id']}"
            print(f"[{i}/{len(pending)}] {code_display}")

            try:
                result = process_norm(page, norm, dry_run=dry_run, verbose=verbose)

                if not dry_run and result.get("_ignored"):
                    conn.execute(
                        "UPDATE norms SET extraction_status='ignored', title=?, code=? WHERE id=?",
                        (result.get("title"), result.get("code"), norm["id"])
                    )
                    conn.commit()
                    print(f"    ~ ignored (not relevant)")
                    continue

                if not dry_run:
                    conn.execute("""
                        UPDATE norms SET
                            viewer_key=?, code=?, title=?, date=?, norm_status=?,
                            pages=?, summary=?, category=?, output_dir=?,
                            extraction_status='done', processed_at=?
                        WHERE id=?
                    """, (
                        result.get("viewer_key"), result.get("code"), result.get("title"),
                        result.get("date"), result.get("norm_status"), result.get("pages"),
                        result.get("summary"), result.get("category"), result.get("output_dir"),
                        datetime.now().isoformat(), norm["id"]
                    ))
                    conn.commit()

                print(f"    ✓ {result['code']} [{result['category']}]")
                done += 1

            except Exception as e:
                err_msg = str(e)
                print(f"    ✗ Error: {err_msg[:80]}")
                if not dry_run:
                    conn.execute(
                        "UPDATE norms SET extraction_status='error', error_msg=? WHERE id=?",
                        (err_msg, norm["id"])
                    )
                    conn.commit()
                errors += 1

            # Polite delay between norms (skip delay after last one)
            # Ignored norms only hit the detail page — use a short delay
            # Downloaded norms hit the PDF viewer — use the full delay
            if i < len(pending):
                was_ignored = not dry_run and isinstance(result, dict) and result.get("_ignored")
                delay = random.uniform(1, 3) if was_ignored else random.uniform(DELAY_MIN, DELAY_MAX)
                if verbose:
                    print(f"    Waiting {delay:.1f}s before next norm...")
                time.sleep(delay)

        browser.close()

    conn.close()

    # Summary
    remaining = conn.execute if False else sqlite3.connect(DB_PATH).execute(
        "SELECT COUNT(*) FROM norms WHERE extraction_status='pending'"
    ).fetchone()[0]

    print(f"\n{'='*50}")
    print(f"Batch complete: {done} done, {errors} errors")
    print(f"Remaining: {remaining} norms still pending")
    if remaining > 0:
        days_left = (remaining + batch_size - 1) // batch_size
        print(f"Estimated: {days_left} more days at {batch_size}/day")


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ABNT NBR Extractor")
    parser.add_argument("--batch", type=int, default=20, help="Number of norms to process (default: 20)")
    parser.add_argument("--enumerate-only", action="store_true", help="Only populate DB, no extraction")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be processed, no downloads")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    args = parser.parse_args()

    run(
        batch_size=args.batch,
        enumerate_only=args.enumerate_only,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )
