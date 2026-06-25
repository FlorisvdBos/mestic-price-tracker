#!/usr/bin/env python3
"""
Step 2: Scrape Obelink.nl for all Mestic products and record prices in mestic_tracker.db.

Strategy:
  1. Walk all pages of obelink.nl/merken/mestic/ (JSON-LD per page, 24 products each).
  2. Extract price + URL for each Obelink listing.
  3. Match to products in our DB by EAN (from URL slug) first, then by model number.
  4. Write a price_snapshot for every match, and a NULL-price "not_available" snapshot
     for every DB product that has no Obelink listing today.
"""

import json
import re
import sqlite3
import time
from datetime import date

import requests
from bs4 import BeautifulSoup

DB_PATH      = "mestic_tracker.db"
RETAILER     = "obelink"
TODAY        = date.today().isoformat()
BRAND_URL    = "https://www.obelink.nl/merken/mestic/"
REQUEST_DELAY = 1.5

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "nl-NL,nl;q=0.9",
}

# ── helpers ──────────────────────────────────────────────────────────────────

def extract_ean_from_url(url: str) -> str | None:
    """Return 13-digit EAN from the URL slug if present, else None."""
    m = re.search(r'-(\d{13})(?:\.html)?/?$', url)
    return m.group(1) if m else None


def extract_model_from_name(name: str) -> str | None:
    """Pull the Mestic model code from a product name (same pattern as mestic.nl)."""
    m = re.search(
        r'\b([A-Z]{2,6}-\d{2,4}[a-zA-Z0-9]*)\b',
        name, re.IGNORECASE,
    )
    return m.group(1).strip().upper() if m else None


# ── Obelink scraping ─────────────────────────────────────────────────────────

def scrape_obelink_brand_page(session: requests.Session) -> list[dict]:
    """Collect all Mestic products from obelink.nl/merken/mestic/ (all pages)."""
    all_products: list[dict] = []
    seen_urls:    set[str]   = set()
    page = 1

    while True:
        url = BRAND_URL if page == 1 else f"{BRAND_URL}?p={page}"
        try:
            resp = session.get(url, timeout=20)
        except Exception as exc:
            print(f"  [warn] request failed on page {page}: {exc}")
            break

        if resp.status_code != 200:
            print(f"  [warn] HTTP {resp.status_code} on page {page}")
            break

        soup = BeautifulSoup(resp.text, "lxml")
        page_products: list[dict] = []

        # JSON-LD ItemList is the cleanest data source
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
            except json.JSONDecodeError:
                continue

            graph = data.get("@graph", [data])  # handle with/without @graph
            for node in graph:
                if node.get("@type") != "ItemList":
                    continue
                for list_item in node.get("itemListElement", []):
                    item = list_item.get("item", {})
                    prod_url  = item.get("url", "").rstrip("/")
                    prod_name = item.get("name", "").strip()
                    price_raw = item.get("offers", {}).get("price")
                    try:
                        price = float(price_raw) if price_raw is not None else None
                    except (ValueError, TypeError):
                        price = None

                    if not prod_url or prod_url in seen_urls:
                        continue
                    seen_urls.add(prod_url)
                    page_products.append({
                        "name":  prod_name,
                        "price": price,
                        "url":   prod_url,
                        "ean":   extract_ean_from_url(prod_url),
                        "model": extract_model_from_name(prod_name),
                    })

        if not page_products:
            print(f"  Page {page}: no new products — stopping")
            break

        all_products.extend(page_products)
        print(f"  Page {page}: {len(page_products)} products  (total so far: {len(all_products)})")
        page += 1
        time.sleep(REQUEST_DELAY)

    return all_products


# ── matching ──────────────────────────────────────────────────────────────────

def build_db_lookups(conn: sqlite3.Connection) -> tuple[dict, dict, list]:
    """
    Returns:
      ean_to_product   – {ean_str: (product_id, product_name)}
      model_to_product – {MODEL_UPPER: (product_id, product_name)}
      all_products     – [(product_id, product_name, ean, model_number)]
    """
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT id, product_name, ean, model_number FROM products"
    ).fetchall()

    ean_to_product   = {}
    model_to_product = {}
    for pid, name, ean, model in rows:
        if ean:
            ean_to_product[ean] = (pid, name)
        if model:
            model_to_product[model.upper()] = (pid, name)

    return ean_to_product, model_to_product, rows


def match_product(
    obelink_item: dict,
    ean_lookup: dict,
    model_lookup: dict,
) -> tuple[int | None, str]:
    """Return (product_id, match_method) or (None, 'no_match')."""
    # 1. EAN match — most reliable
    if obelink_item["ean"] and obelink_item["ean"] in ean_lookup:
        pid, _ = ean_lookup[obelink_item["ean"]]
        return pid, "ean"

    # 2. Model number match
    if obelink_item["model"] and obelink_item["model"] in model_lookup:
        pid, _ = model_lookup[obelink_item["model"]]
        return pid, "model"

    return None, "no_match"


# ── database writes ───────────────────────────────────────────────────────────

def already_scraped_today(conn: sqlite3.Connection) -> bool:
    cur = conn.cursor()
    row = cur.execute(
        "SELECT COUNT(*) FROM price_snapshots WHERE retailer=? AND scraped_date=?",
        (RETAILER, TODAY),
    ).fetchone()
    return row[0] > 0


def record_snapshot(
    cur:        sqlite3.Cursor,
    product_id: int,
    price:      float | None,
    retailer_url: str | None,
    match_method: str,
) -> None:
    cur.execute("""
        INSERT INTO price_snapshots (product_id, retailer, price_eur, scraped_date, retailer_url, match_method)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (product_id, RETAILER, price, TODAY, retailer_url, match_method))


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    print(f"Obelink scraper  —  {TODAY}")
    print("=" * 60)

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")

    # Ensure extra columns exist (idempotent)
    for stmt in [
        "ALTER TABLE price_snapshots ADD COLUMN retailer_url TEXT",
        "ALTER TABLE price_snapshots ADD COLUMN match_method TEXT",
    ]:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.commit()

    if already_scraped_today(conn):
        print(f"Already have Obelink data for {TODAY} — skipping.")
        conn.close()
        return

    session = requests.Session()
    session.headers.update(HEADERS)

    # Phase 1: collect all Obelink Mestic listings
    print(f"\nScraping {BRAND_URL} …")
    obelink_items = scrape_obelink_brand_page(session)
    print(f"\nTotal Obelink Mestic listings found: {len(obelink_items)}")

    # Phase 2: build lookup tables from our DB
    ean_lookup, model_lookup, all_db_products = build_db_lookups(conn)
    print(f"DB products to match against: {len(all_db_products)}")

    # Phase 3: match and record
    print(f"\nMatching and recording snapshots…")
    cur = conn.cursor()
    matched_product_ids: set[int] = set()
    stats = {"ean": 0, "model": 0, "no_match_obelink": 0}

    for item in obelink_items:
        pid, method = match_product(item, ean_lookup, model_lookup)
        if pid is not None:
            record_snapshot(cur, pid, item["price"], item["url"], method)
            matched_product_ids.add(pid)
            stats[method] += 1
            marker = "EAN" if method == "ean" else "MDL"
            print(f"  [{marker}] {item['name'][:55]:<55}  €{item['price'] or 0:.2f}")
        else:
            stats["no_match_obelink"] += 1
            print(f"  [---] {item['name'][:55]:<55}  (no DB match)")

    conn.commit()

    # Phase 4: mark every unmatched DB product as not_available
    not_available = 0
    for pid, name, ean, model in all_db_products:
        if pid not in matched_product_ids:
            record_snapshot(cur, pid, None, None, "not_available")
            not_available += 1

    conn.commit()

    # Summary
    print(f"\n{'='*60}")
    print(f"Obelink scrape complete — {TODAY}")
    print(f"  Matched by EAN:          {stats['ean']}")
    print(f"  Matched by model number: {stats['model']}")
    print(f"  Obelink items unmatched: {stats['no_match_obelink']}")
    print(f"  DB products not at OBL:  {not_available}")

    # Price comparison sample
    print(f"\nPrice comparison sample (Mestic MSRP vs Obelink):")
    print(f"  {'Product':<45} {'MSRP':>8}  {'Obelink':>8}  {'Diff':>8}")
    print("  " + "-"*80)
    for row in cur.execute("""
        SELECT p.product_name,
               msrp.price_eur  AS msrp_price,
               obl.price_eur   AS obl_price
        FROM products p
        JOIN price_snapshots msrp ON msrp.product_id = p.id
                                  AND msrp.retailer = 'mestic.nl'
        JOIN price_snapshots obl  ON obl.product_id  = p.id
                                  AND obl.retailer   = 'obelink'
                                  AND obl.scraped_date = ?
                                  AND obl.price_eur IS NOT NULL
        WHERE msrp.scraped_date = (
            SELECT MAX(scraped_date) FROM price_snapshots
            WHERE retailer='mestic.nl' AND product_id=p.id
        )
        ORDER BY (msrp.price_eur - obl.price_eur) DESC
        LIMIT 20
    """, (TODAY,)):
        name, msrp, obl = row
        diff = obl - msrp
        sign = "+" if diff > 0 else ""
        print(f"  {name[:45]:<45} €{msrp:>7.2f}  €{obl:>7.2f}  {sign}{diff:>7.2f}")

    conn.close()
    print(f"\nDone.")


if __name__ == "__main__":
    main()
