#!/usr/bin/env python3
"""
Step 1: Scrape all Mestic products from mestic.nl and store in SQLite.
"""
import json
import re
import sqlite3
import time
from datetime import date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.mestic.nl"
DB_PATH = "mestic_tracker.db"
TODAY = date.today().isoformat()
REQUEST_DELAY = 1.2  # seconds between requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "nl-NL,nl;q=0.9",
}

# category URL path → human-readable name
CATEGORIES = {
    "/klimaat/airconditioners":          "Klimaat > Airconditioners",
    "/klimaat/elektrische-kachels":      "Klimaat > Elektrische kachels",
    "/klimaat/gaskachels":               "Klimaat > Gaskachels",
    "/koelboxen/koelboxen":              "Koelboxen & Koelkasten > Koelboxen",
    "/koelboxen/koelkasten":             "Koelboxen & Koelkasten > Koelkasten",
    "/energie-en-elektra/zonnepanelen":  "Energie & Elektra > Zonnepanelen",
    "/energie-en-elektra/laadregelaars": "Energie & Elektra > Laadregelaars",
    "/energie-en-elektra/accu":          "Energie & Elektra > Accu's",
    "/energie-en-elektra/omvormers":     "Energie & Elektra > Omvormers",
    "/energie-en-elektra/power-stations":"Energie & Elektra > Power Stations",
    "/energie-en-elektra/acculaders":    "Energie & Elektra > Acculaders",
    "/huishoudelijk/koffiezetters":      "Huishoudelijk > Koffiezetters",
    "/huishoudelijk/waterkokers":        "Huishoudelijk > Waterkokers",
    "/huishoudelijk/stofzuigers":        "Huishoudelijk > Stofzuigers",
    "/huishoudelijk/wasmachines":        "Huishoudelijk > Wasmachines",
    "/huishoudelijk/kooktoestellen":     "Huishoudelijk > Kooktoestellen",
    "/huishoudelijk/ovens-en-airfryers": "Huishoudelijk > Ovens & Airfryers",
    "/huishoudelijk/magnetrons":         "Huishoudelijk > Magnetrons",
    "/multimedia/cameras":               "Multimedia > Camera's",
    "/multimedia/tvs":                   "Multimedia > TV's",
    "/multimedia/antennes":              "Multimedia > Antennes",
    "/sale":                             "Sale",
}


def setup_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS products (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT    NOT NULL,
            model_number TEXT,
            article_number TEXT,
            ean          TEXT,
            category     TEXT,
            product_url  TEXT    UNIQUE NOT NULL,
            first_seen   TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS price_snapshots (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id   INTEGER NOT NULL,
            retailer     TEXT    NOT NULL DEFAULT 'mestic.nl',
            price_eur    REAL,
            scraped_date TEXT    NOT NULL,
            FOREIGN KEY (product_id) REFERENCES products(id)
        );

        CREATE INDEX IF NOT EXISTS idx_snapshots_product_date
            ON price_snapshots(product_id, scraped_date);
    """)
    conn.commit()
    return conn


def extract_model_number(product_name: str) -> str | None:
    """Pull the model code out of a Mestic product name.

    Handles patterns such as RTA-2200i, MCC-45, SPA-5100, MI-500, MCCM-35,
    MCCHD-33, MMI-150, BA-2600, etc.
    """
    match = re.search(
        r'\b([A-Z]{2,6}-\d{2,4}[a-zA-Z0-9]*)\b',
        product_name,
        re.IGNORECASE,
    )
    return match.group(1).strip() if match else None


def get_product_details(session: requests.Session, url: str) -> tuple[str | None, str | None]:
    """Return (article_number, ean) from a product detail page."""
    try:
        resp = session.get(url, timeout=20)
        if resp.status_code != 200:
            return None, None
        soup = BeautifulSoup(resp.text, "lxml")
        article_number = ean = None
        for row in soup.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            label = cells[0].get_text(strip=True)
            value = cells[1].get_text(strip=True)
            if label == "Artikelnummer":
                article_number = value
            elif label == "EAN-code":
                ean = value
            if article_number and ean:
                break
        return article_number, ean
    except Exception as exc:
        print(f"    [warn] detail fetch failed: {exc}")
        return None, None


def scrape_category(
    session: requests.Session,
    cat_path: str,
    cat_name: str,
) -> list[dict]:
    """Return all products from a category, following pagination."""
    products: list[dict] = []
    limit_start = 0
    page_size = 15

    while True:
        url = BASE_URL + cat_path
        if limit_start:
            url += f"?limit_start={limit_start}"

        try:
            resp = session.get(url, timeout=20)
        except Exception as exc:
            print(f"  [warn] request failed: {exc}")
            break

        if resp.status_code == 404:
            print(f"  [skip] 404 — category not found")
            break
        if resp.status_code != 200:
            print(f"  [warn] HTTP {resp.status_code}")
            break

        soup = BeautifulSoup(resp.text, "lxml")
        items = soup.find_all(class_="product-item")
        if not items:
            break

        page_products: list[dict] = []
        for item in items:
            link = item.find("a", attrs={"data-ec-product": True})
            if not link:
                continue
            try:
                ec = json.loads(link["data-ec-product"])
                pd = ec["ecommerce"]["click"]["products"][0]
                href = link.get("href", "")
                if not href:
                    continue
                price_raw = pd.get("price")
                page_products.append({
                    "name":     pd.get("name", "").strip(),
                    "price":    float(price_raw) if price_raw else None,
                    "url":      href if href.startswith("http") else BASE_URL + href,
                    "category": cat_name,
                })
            except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                continue

        if not page_products:
            break

        products.extend(page_products)
        print(f"  offset {limit_start:>4}: {len(page_products)} products")

        # Stop if this page wasn't full
        if len(page_products) < page_size:
            break

        # Double-check total count label (e.g. "28 producten")
        total_text = soup.find(string=re.compile(r"\d+\s+producten"))
        if total_text:
            total = int(re.search(r"(\d+)", total_text).group(1))
            if limit_start + len(page_products) >= total:
                break

        limit_start += page_size
        time.sleep(REQUEST_DELAY)

    return products


def upsert_product(cur: sqlite3.Cursor, prod: dict, article_number, ean) -> int:
    model_number = extract_model_number(prod["name"])
    cur.execute("""
        INSERT INTO products
            (product_name, model_number, article_number, ean, category, product_url, first_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(product_url) DO UPDATE SET
            product_name   = excluded.product_name,
            model_number   = excluded.model_number,
            article_number = excluded.article_number,
            ean            = excluded.ean,
            category       = excluded.category
    """, (prod["name"], model_number, article_number, ean,
          prod["category"], prod["url"], TODAY))

    row = cur.execute(
        "SELECT id FROM products WHERE product_url = ?", (prod["url"],)
    ).fetchone()
    return row[0]


def main() -> None:
    print(f"Mestic product scraper  —  {TODAY}")
    print("=" * 60)

    conn = setup_db(DB_PATH)
    session = requests.Session()
    session.headers.update(HEADERS)

    # Phase 1 — collect all product URLs from category listing pages
    all_products: list[dict] = []
    seen_urls: set[str] = set()

    for cat_path, cat_name in CATEGORIES.items():
        print(f"\n[category] {cat_name}")
        for prod in scrape_category(session, cat_path, cat_name):
            if prod["url"] not in seen_urls:
                seen_urls.add(prod["url"])
                all_products.append(prod)
        time.sleep(REQUEST_DELAY)

    print(f"\n{'='*60}")
    print(f"Unique products found across all categories: {len(all_products)}")

    # Phase 2 — visit each product page to get article number + EAN
    print(f"\nFetching product detail pages…")
    cur = conn.cursor()
    saved = 0

    for i, prod in enumerate(all_products, 1):
        name_short = prod["name"][:55]
        print(f"  [{i:>3}/{len(all_products)}] {name_short}")

        article_number, ean = get_product_details(session, prod["url"])
        product_id = upsert_product(cur, prod, article_number, ean)

        cur.execute("""
            INSERT INTO price_snapshots (product_id, retailer, price_eur, scraped_date)
            VALUES (?, 'mestic.nl', ?, ?)
        """, (product_id, prod["price"], TODAY))

        conn.commit()
        saved += 1
        time.sleep(REQUEST_DELAY)

    # Summary
    print(f"\n{'='*60}")
    print(f"Saved {saved} products to {DB_PATH}\n")
    print("Products per category:")
    for row in cur.execute(
        "SELECT category, COUNT(*) n FROM products GROUP BY category ORDER BY category"
    ):
        print(f"  {row[1]:>3}  {row[0]}")

    print("\nSample (first 5 rows):")
    print(f"  {'Name':<40} {'Model':<15} {'Artikelnr':<12} {'EAN':<15} {'Price':>8}")
    print("  " + "-" * 100)
    for row in cur.execute("""
        SELECT p.product_name, p.model_number, p.article_number, p.ean, s.price_eur
        FROM products p
        JOIN price_snapshots s ON s.product_id = p.id
        WHERE s.retailer = 'mestic.nl' AND s.scraped_date = ?
        LIMIT 5
    """, (TODAY,)):
        name, model, artnr, ean, price = row
        price_str = f"€{price:.2f}" if price else "—"
        print(f"  {(name or '')[:40]:<40} {(model or '—'):<15} {(artnr or '—'):<12} {(ean or '—'):<15} {price_str:>8}")

    conn.close()
    print(f"\nDone.")


if __name__ == "__main__":
    main()
