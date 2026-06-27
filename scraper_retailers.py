#!/usr/bin/env python3
"""
Generic Mestic price scraper — driven by retailers.toml.

Adding a new retailer requires only a [retailers.<id>] block in retailers.toml;
no code changes needed as long as the site fits one of the supported methods:
  • json_ld_brand_page  (Obelink style)
  • html_brand_page     (Fritz Berger style)
  • rest_api            (Vrijbuiter style)

Usage:
    python3 scraper_retailers.py                   # all enabled retailers
    python3 scraper_retailers.py fritz_berger      # single retailer by id
    python3 scraper_retailers.py --list            # show configured retailers
"""

import json
import os
import re
import sqlite3
import sys
import time
import tomllib
import warnings
from datetime import date
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

DB_PATH          = "mestic_tracker.db"
CONFIG_FILE      = "retailers.toml"
TODAY            = date.today().isoformat()
SCRAPERAPI_KEY   = os.environ.get("SCRAPERAPI_KEY", "")
SCRAPERAPI_PROXY = "http://proxy-server.scraperapi.com:8001"

BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "nl-NL,nl;q=0.9,de-DE;q=0.8,de;q=0.7,en;q=0.6",
    # Accept-Encoding intentionally omitted: requests adds gzip/deflate automatically
    # and can decompress them. Advertising brotli (br) causes servers to respond
    # with brotli-encoded content that requests cannot decompress without the
    # optional brotli package, resulting in garbled binary output.
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


# ── config loading ────────────────────────────────────────────────────────────

def load_config(path: str = CONFIG_FILE) -> dict:
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    defaults = raw.get("defaults", {})
    retailers = {}
    for rid, cfg in raw.get("retailers", {}).items():
        merged = {**defaults, **cfg, "id": rid}
        retailers[rid] = merged
    return retailers


# ── helpers ───────────────────────────────────────────────────────────────────

def extract_ean_from_url(url: str) -> str | None:
    """Return 13-digit EAN when it appears as the last numeric segment of the URL."""
    m = re.search(r"-(\d{13})(?:\.html)?/?$", url)
    return m.group(1) if m else None


def extract_article_from_url(url: str) -> str | None:
    """Return Mestic 7-digit article number (15xxxxx) if embedded in a retailer URL."""
    m = re.search(r"[\-/_](15\d{5})(?:[/_\-.]|$)", url)
    return m.group(1) if m else None


def extract_model_from_name(name: str) -> str | None:
    """Extract the first Mestic model code from a product name."""
    m = re.search(r"\b([A-Z]{2,6}-\d{2,4}[a-zA-Z0-9]*)\b", name, re.IGNORECASE)
    return m.group(1).upper() if m else None


def extract_models_from_name(name: str) -> list[str]:
    """Extract all model codes, expanding combined pages like 'RTA-1700i/2200i' → ['RTA-1700I', 'RTA-2200I']."""
    primary = re.findall(r"\b([A-Z]{2,6}-\d{2,4}[a-zA-Z0-9]*)\b", name, re.IGNORECASE)
    if not primary:
        return []
    results: list[str] = []
    for match in primary:
        code = match.upper()
        if code not in results:
            results.append(code)
        # Look for variant suffixes: "RTA-1700i/2200i" → also emit "RTA-2200I"
        prefix_m = re.match(r"([A-Z]{2,6}-)", code)
        if prefix_m:
            pref = prefix_m.group(1)
            pos = name.upper().find(code) + len(code)
            for var in re.findall(r"/(\d{2,4}[A-Z0-9]*)", name[pos:pos + 30], re.IGNORECASE):
                combined = (pref + var).upper()
                if combined not in results:
                    results.append(combined)
    return results


def parse_price(text: str, locale: str = "nl") -> float | None:
    """
    Parse a European price string to float.
      nl locale:  '€ 1.299,95'  →  1299.95
      de locale:  '1.299,- €'   →  1299.0
                  '14,99 €'     →  14.99
    """
    text = re.sub(r"(?i)ab|uvp|€|\s", "", text)  # strip labels and whitespace
    text = text.replace(",-", "").strip()          # strip German ",-" decimals

    if locale == "de":
        # German: '.' = thousands separator, ',' = decimal
        text = re.sub(r"\.(?=\d{3})", "", text)   # remove thousands dots
        text = text.replace(",", ".")
    else:
        # Dutch/default: '.' = thousands, ',' = decimal  (same logic)
        text = re.sub(r"\.(?=\d{3})", "", text)
        text = text.replace(",", ".")

    text = re.sub(r"[^\d.]", "", text)
    try:
        return float(text) if text else None
    except ValueError:
        return None


def get_nested(obj: Any, dot_path: str) -> Any:
    """Navigate a nested dict/list using a dot-separated path string."""
    for key in dot_path.split("."):
        if isinstance(obj, dict):
            obj = obj.get(key)
        elif isinstance(obj, list) and key.isdigit():
            obj = obj[int(key)]
        else:
            return None
        if obj is None:
            return None
    return obj


def make_session(language: str = "nl") -> requests.Session:
    s = requests.Session()
    s.headers.update(BASE_HEADERS)
    if language == "de":
        s.headers["Accept-Language"] = "de-DE,de;q=0.9,en;q=0.8"
    return s


# ── scraping methods ──────────────────────────────────────────────────────────

def _ctx_kwargs(cfg: dict) -> dict:
    """Return extra kwargs for Playwright browser.new_context() when a proxy is set."""
    proxy = cfg.get("_proxy")
    return {"proxy": proxy, "ignore_https_errors": True} if proxy else {}

def scrape_json_ld_brand_page(cfg: dict, session: requests.Session) -> list[dict]:
    """
    Obelink-style: brand/category page that embeds a JSON-LD ItemList.
    Paginates using cfg['pagination_param'] (e.g. ?p=2).
    """
    brand_url   = cfg["brand_page_url"]
    pag_param   = cfg.get("pagination_param", "p")
    delay       = cfg.get("request_delay", 1.5)
    results     = []
    seen_urls: set[str] = set()
    page = 1

    while True:
        url = brand_url if page == 1 else f"{brand_url}?{pag_param}={page}"
        try:
            resp = session.get(url, timeout=20)
            resp.raise_for_status()
        except requests.RequestException as exc:
            print(f"    [warn] page {page}: {exc}")
            break

        soup = BeautifulSoup(resp.text, "lxml")
        page_items: list[dict] = []

        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
            except json.JSONDecodeError:
                continue
            for node in data.get("@graph", [data]):
                if node.get("@type") != "ItemList":
                    continue
                for li in node.get("itemListElement", []):
                    item     = li.get("item", {})
                    prod_url = item.get("url", "").rstrip("/")
                    if not prod_url or prod_url in seen_urls:
                        continue
                    seen_urls.add(prod_url)
                    price_raw = item.get("offers", {}).get("price")
                    try:
                        price = float(price_raw) if price_raw is not None else None
                    except (TypeError, ValueError):
                        price = None
                    page_items.append({
                        "name":  item.get("name", "").strip(),
                        "price": price,
                        "url":   prod_url,
                        "sku":   item.get("sku") or item.get("productID") or "",
                    })

        if not page_items:
            break
        results.extend(page_items)
        print(f"    page {page}: {len(page_items)} products")
        page += 1
        time.sleep(delay)

    return results


def scrape_html_brand_page(cfg: dict, session: requests.Session) -> list[dict]:
    """
    Fritz Berger-style: HTML brand/category page with CSS-selectable product cards.
    Fetches only a single page (pagination not yet implemented for HTML method).
    """
    brand_url      = cfg["brand_page_url"]
    card_sel       = cfg.get("card_selector",   "div.product-item")
    name_sel       = cfg.get("name_selector",   ".product-name")
    price_sels     = cfg.get("price_selectors", [".price"])
    url_sel        = cfg.get("url_selector",    "a.product-link")
    price_locale   = cfg.get("price_locale",    "nl")

    try:
        resp = session.get(brand_url, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"    [error] {exc}")
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    cards = soup.select(card_sel)
    print(f"    found {len(cards)} product cards")

    results = []
    for card in cards:
        name_el = card.select_one(name_sel)
        name    = name_el.get_text(strip=True) if name_el else ""
        if not name:
            continue

        price = None
        for sel in price_sels:
            price_el = card.select_one(sel)
            if price_el:
                raw = price_el.get_text(separator="", strip=True)
                price = parse_price(raw, locale=price_locale)
                if price is not None:
                    break

        url_el  = card.select_one(url_sel)
        prod_url = url_el.get("href", "") if url_el else ""
        if prod_url and not prod_url.startswith("http"):
            base = re.match(r"(https?://[^/]+)", brand_url)
            prod_url = (base.group(1) if base else "") + prod_url

        results.append({"name": name, "price": price, "url": prod_url})

    return results


def scrape_playwright_html_brand_page(cfg: dict, session: requests.Session) -> list[dict]:
    """
    Like html_brand_page but loads pages with a real Chromium browser (Playwright)
    to bypass WAF/bot-detection (e.g. Akamai) that rejects plain HTTP clients.
    Pagination is driven by appending &<pagination_param>=N to the URL.

    Optional: price_attr = "value"  — reads the HTML `value` attribute of the price
    element instead of its text content (useful for hidden inputs with numeric values).
    """
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    brand_url    = cfg["brand_page_url"]
    card_sel     = cfg.get("card_selector",   "li.gallery-listing-v2__item")
    name_sel     = cfg.get("name_selector",   ".gallery-listing-v2__title a")
    price_sels   = cfg.get("price_selectors", ["p.gallery-listing-v2__price"])
    url_sel      = cfg.get("url_selector",    "a.gallery-listing-v2__image-link")
    price_locale = cfg.get("price_locale",    "de")
    price_attr   = cfg.get("price_attr",      "text")   # "text" or "value"
    pag_param    = cfg.get("pagination_param", None)
    delay        = cfg.get("request_delay",   1.5)
    lang         = cfg.get("language",        "de")
    locale       = f"{lang}-{lang.upper()}"
    load_wait    = cfg.get("load_wait",       0)        # extra sleep after page load (s)

    results: list[dict] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx     = browser.new_context(locale=locale, **_ctx_kwargs(cfg))
        pg      = ctx.new_page()
        page_num = 1

        while True:
            if page_num == 1:
                url = brand_url
            else:
                sep = "&" if "?" in brand_url else "?"
                url = f"{brand_url}{sep}{pag_param}={page_num}"

            pg.goto(url, wait_until="domcontentloaded", timeout=30000)

            if load_wait:
                time.sleep(load_wait)

            try:
                pg.wait_for_selector(card_sel, timeout=8000)
            except PWTimeout:
                break

            soup  = BeautifulSoup(pg.content(), "lxml")
            cards = soup.select(card_sel)
            if not cards:
                break

            page_items: list[dict] = []
            for card in cards:
                name_el = card.select_one(name_sel)
                name    = name_el.get_text(strip=True) if name_el else ""
                if not name:
                    continue

                price = None
                for sel in price_sels:
                    price_el = card.select_one(sel)
                    if price_el:
                        if price_attr == "value":
                            raw_val = price_el.get("value", "")
                            try:
                                price = float(raw_val) if raw_val else None
                            except (TypeError, ValueError):
                                price = None
                        else:
                            raw   = price_el.get_text(separator="", strip=True)
                            price = parse_price(raw, locale=price_locale)
                        if price is not None:
                            break

                url_el   = card.select_one(url_sel)
                prod_url = url_el.get("href", "") if url_el else ""
                if prod_url and not prod_url.startswith("http"):
                    base     = re.match(r"(https?://[^/]+)", brand_url)
                    prod_url = (base.group(1) if base else "") + prod_url

                page_items.append({"name": name, "price": price, "url": prod_url})

            results.extend(page_items)
            print(f"    page {page_num}: {len(page_items)} products")

            if not pag_param or not page_items:
                break

            page_num += 1
            time.sleep(delay)

        browser.close()

    return results


def scrape_amazon_search_page(cfg: dict, session: requests.Session) -> list[dict]:
    """
    Amazon-style: plain HTTP search results page, ASIN-based product URLs.
    Matches by model number extracted from the German/Dutch product title.
    """
    search_url   = cfg["search_url"]
    base_url_m   = re.match(r"(https?://[^/]+)", search_url)
    base_url     = base_url_m.group(1) if base_url_m else ""
    card_sel     = cfg.get("card_selector",   "[data-component-type='s-search-result']")
    name_sel     = cfg.get("name_selector",   "h2 span")
    price_sels   = cfg.get("price_selectors", [".a-price .a-offscreen"])
    price_locale = cfg.get("price_locale",    "de")
    delay        = cfg.get("request_delay",   2.5)
    max_pages    = cfg.get("max_pages",       5)

    results: list[dict] = []
    page = 1

    while page <= max_pages:
        url = search_url if page == 1 else f"{search_url}&page={page}"
        try:
            resp = session.get(url, timeout=20)
            resp.raise_for_status()
        except requests.RequestException as exc:
            print(f"    [warn] page {page}: {exc}")
            break

        soup  = BeautifulSoup(resp.text, "lxml")
        cards = soup.select(card_sel)
        if not cards:
            break

        page_items: list[dict] = []
        for card in cards:
            asin = card.get("data-asin", "")
            if not asin:
                continue

            name_el = card.select_one(name_sel)
            name    = name_el.get_text(strip=True) if name_el else ""
            if not name:
                continue

            price = None
            for sel in price_sels:
                price_el = card.select_one(sel)
                if price_el:
                    price = parse_price(price_el.get_text(strip=True), locale=price_locale)
                    if price is not None:
                        break

            prod_url = f"{base_url}/dp/{asin}"
            page_items.append({"name": name, "price": price, "url": prod_url, "sku": asin})

        results.extend(page_items)
        print(f"    page {page}: {len(page_items)} products")

        next_btn = soup.select_one("a.s-pagination-next")
        if not next_btn or "s-pagination-disabled" in next_btn.get("class", []):
            break

        page += 1
        time.sleep(delay)

    return results


def scrape_playwright_tweakwise(cfg: dict, session: requests.Session) -> list[dict]:
    """
    Tweakwise Navigator-style brand/search page (e.g. Kampeerhal Roden).
    Playwright renders the page; product data lives in the `data-combinations`
    JSON attribute on each `.twn-product-tile-dynamic` element.
    Each card may have multiple size/colour variants — we take the cheapest.
    """
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    brand_url    = cfg["brand_page_url"]
    card_sel     = cfg.get("card_selector", ".twn-starter__products-grid-item")
    tile_sel     = cfg.get("tile_selector", ".twn-product-tile-dynamic")
    delay        = cfg.get("request_delay", 1.5)
    load_wait    = cfg.get("load_wait", 5)
    goto_timeout = cfg.get("goto_timeout", 60000)
    max_retries  = cfg.get("max_retries", 2)
    lang         = cfg.get("language", "nl")
    locale       = f"{lang}-{lang.upper()}"
    base_url_m   = re.match(r"(https?://[^/]+)", brand_url)
    base_url     = base_url_m.group(1) if base_url_m else ""

    results: list[dict] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx     = browser.new_context(locale=locale, **_ctx_kwargs(cfg))
        pg      = ctx.new_page()

        for attempt in range(max_retries):
            try:
                pg.goto(brand_url, wait_until="domcontentloaded", timeout=goto_timeout)
                break
            except PWTimeout:
                if attempt == max_retries - 1:
                    print(f"    [warn] page load timed out after {max_retries} attempts")
                    browser.close()
                    return results
                print(f"    [warn] page load attempt {attempt + 1} timed out, retrying…")

        time.sleep(load_wait)

        try:
            pg.wait_for_selector(tile_sel, timeout=15000)
        except PWTimeout:
            print(f"    [warn] Tweakwise tiles not found after {load_wait}s wait")
            browser.close()
            return results

        soup  = BeautifulSoup(pg.content(), "lxml")
        tiles = soup.select(tile_sel)
        print(f"    found {len(tiles)} Tweakwise tiles")

        for tile in tiles:
            raw = tile.get("data-combinations", "")
            if not raw:
                continue
            try:
                combos = json.loads(raw)
            except json.JSONDecodeError:
                continue

            # Take the combination with the lowest price (skip if no price)
            best: dict | None = None
            for combo in combos:
                price_raw = combo.get("price")
                try:
                    p = float(price_raw) if price_raw is not None else None
                except (TypeError, ValueError):
                    p = None
                if p is None:
                    continue
                if best is None or p < best["price"]:
                    prod_url = combo.get("url", "")
                    if prod_url and not prod_url.startswith("http"):
                        prod_url = base_url + prod_url
                    best = {"name": combo.get("title", "").strip(),
                            "price": p,
                            "url": prod_url,
                            "sku": str(combo.get("itemNumber") or combo.get("sku") or "")}

            if best and best["name"]:
                results.append(best)

        browser.close()

    print(f"    extracted {len(results)} products")
    return results


def scrape_bol_react_router(cfg: dict, session: requests.Session) -> list[dict]:
    """
    Bol.com search page — products live in the React Router loader-data object
    injected as window.__reactRouterContext.  Playwright renders the page;
    we read the JS object directly to get titles, URLs and prices.
    Paginates through ?page=N up to metaData.maxPage.
    """
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    search_url = cfg["search_url"]
    delay      = cfg.get("request_delay", 3.0)
    load_wait  = cfg.get("load_wait", 8)
    max_pages  = cfg.get("max_pages", 10)
    lang       = cfg.get("language", "nl")
    locale     = f"{lang}-{lang.upper()}"

    results: list[dict] = []

    JS_EXTRACT = """
        () => {
            const ld = (window.__reactRouterContext?.state?.loaderData || {});
            const page = ld['routes/searchPage'] || {};
            const products = page.products || [];
            const maxPage  = (page.metaData || {}).maxPage || 1;
            return {
                maxPage,
                items: products.map(p => {
                    const offer = p.bestSellingOffer || {};
                    const prod  = offer.product || {};
                    const price = (offer.sellingPrice?.price?.amount) ?? null;
                    return {
                        title: prod.title || '',
                        url:   prod.url   || '',
                        price: price,
                        sku:   prod.ean || String(prod.id || ''),
                    };
                })
            };
        }
    """

    with sync_playwright() as pw:
        browser  = pw.chromium.launch(headless=True)
        ctx      = browser.new_context(locale=locale, **_ctx_kwargs(cfg))
        pg       = ctx.new_page()
        max_page = 1
        page_num = 1

        while page_num <= max_pages:
            url = search_url if page_num == 1 else f"{search_url}&page={page_num}"
            pg.goto(url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(load_wait)

            try:
                data = pg.evaluate(JS_EXTRACT)
            except Exception as exc:
                print(f"    [warn] JS eval failed page {page_num}: {exc}")
                break

            if page_num == 1:
                max_page = min(data.get("maxPage", 1), max_pages)
                print(f"    total pages: {max_page}")

            items = data.get("items", [])
            page_results = []
            for item in items:
                title = item.get("title", "").strip()
                if not title:
                    continue
                raw_url = item.get("url", "")
                prod_url = ("https://www.bol.com" + raw_url) if raw_url.startswith("/") else raw_url
                try:
                    price = float(item["price"]) if item.get("price") is not None else None
                except (TypeError, ValueError):
                    price = None
                page_results.append({"name": title, "price": price, "url": prod_url,
                                     "sku": str(item.get("sku") or "")})

            results.extend(page_results)
            print(f"    page {page_num}/{max_page}: {len(page_results)} products")

            if page_num >= max_page:
                break
            page_num += 1
            time.sleep(delay)

        browser.close()

    return results


def scrape_playwright_js_extract(cfg: dict, session: requests.Session) -> list[dict]:
    """
    Generic Playwright method that evaluates a JavaScript snippet on the page
    and returns [{name, price, url}] objects.  The JS snippet is stored in
    cfg['js_extract'] (a string of an arrow function body).

    Also supports an optional cfg['scroll'] = true to scroll to the bottom
    before evaluating.
    """
    from playwright.sync_api import sync_playwright

    brand_url  = cfg["brand_page_url"]
    js_fn      = cfg["js_extract"]
    delay      = cfg.get("request_delay", 1.5)
    load_wait  = cfg.get("load_wait", 5)
    do_scroll  = cfg.get("scroll", False)
    lang       = cfg.get("language", "nl")
    locale     = f"{lang}-{lang.upper()}"

    results: list[dict] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx     = browser.new_context(locale=locale, **_ctx_kwargs(cfg))
        pg      = ctx.new_page()
        pg.goto(brand_url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(load_wait)
        if do_scroll:
            pg.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(2)

        try:
            raw = pg.evaluate(js_fn)
        except Exception as exc:
            print(f"    [error] JS eval: {exc}")
            browser.close()
            return results

        if isinstance(raw, list):
            for item in raw:
                name  = str(item.get("name", "")).strip()
                if not name:
                    continue
                try:
                    price = float(item["price"]) if item.get("price") is not None else None
                except (TypeError, ValueError):
                    price = None
                results.append({"name": name, "price": price, "url": item.get("url", "")})

        print(f"    extracted {len(results)} products")
        browser.close()

    return results


def scrape_rest_api(cfg: dict, session: requests.Session) -> list[dict]:
    """
    Vrijbuiter-style: JSON REST search API.
    Filters results by brand_filter_field == brand_filter_value (in-memory).
    """
    search_url   = cfg["search_url"]
    query_param  = cfg.get("query_param",  "q")
    query        = cfg.get("search_query", "Mestic")
    results_key  = cfg.get("results_key",  "products")
    name_path    = cfg.get("name_path",    "name")
    price_path   = cfg.get("price_path",   "price")
    url_path     = cfg.get("url_path",     "url")
    sku_path     = cfg.get("sku_path",     "")
    brand_field  = cfg.get("brand_filter_field",  "brand.name")
    brand_value  = cfg.get("brand_filter_value",  "Mestic").lower()

    try:
        resp = session.get(
            search_url, params={query_param: query}, timeout=20,
            headers={"Accept": "application/json, text/plain, */*"},
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, json.JSONDecodeError) as exc:
        print(f"    [error] {exc}")
        return []

    raw_items = data.get(results_key, [])
    print(f"    API returned {len(raw_items)} items")

    results = []
    for item in raw_items:
        # Brand filter
        brand_actual = (get_nested(item, brand_field) or "").lower()
        if brand_value and brand_actual != brand_value:
            continue

        name  = get_nested(item, name_path) or ""
        price_raw = get_nested(item, price_path)
        try:
            price = float(price_raw) if price_raw is not None else None
        except (TypeError, ValueError):
            price = None
        url = get_nested(item, url_path) or ""
        sku = str(get_nested(item, sku_path) or "") if sku_path else ""

        results.append({"name": name, "price": price, "url": url, "sku": sku})

    return results


METHODS = {
    "json_ld_brand_page":          scrape_json_ld_brand_page,
    "html_brand_page":             scrape_html_brand_page,
    "playwright_html_brand_page":  scrape_playwright_html_brand_page,
    "playwright_tweakwise":        scrape_playwright_tweakwise,
    "bol_react_router":            scrape_bol_react_router,
    "playwright_js_extract":       scrape_playwright_js_extract,
    "amazon_search_page":          scrape_amazon_search_page,
    "rest_api":                    scrape_rest_api,
}


# ── matching ──────────────────────────────────────────────────────────────────

_NAME_STOP = {
    "mestic", "de", "nl", "en", "met", "voor", "und", "mit", "für",
    "ac", "dc", "le", "la", "per", "van", "the",
}


def _tokenize(s: str) -> set[str]:
    return {t for t in re.split(r"[\s/\-,\.()\[\]]+", s.lower())
            if len(t) > 2 and t not in _NAME_STOP}


def build_db_lookups(conn: sqlite3.Connection) -> tuple[dict, dict, dict, dict, list, dict]:
    rows = conn.execute(
        "SELECT id, product_name, ean, model_number, article_number FROM products"
    ).fetchall()
    ean_lookup     = {r[2]: (r[0], r[1]) for r in rows if r[2]}
    model_lookup   = {r[3].upper(): (r[0], r[1]) for r in rows if r[3]}
    article_lookup = {r[4]: (r[0], r[1]) for r in rows if r[4]}
    name_index     = {r[0]: (r[1], _tokenize(r[1])) for r in rows}
    all_db         = [(r[0], r[1], r[2], r[3]) for r in rows]

    # retailer_product_map: (retailer, map_key) → [product_id, ...]
    # map_key is retailer SKU when available, otherwise retailer URL
    product_map: dict[tuple[str, str], list[int]] = {}
    for row in conn.execute(
        "SELECT retailer, map_key, product_id FROM retailer_product_map"
    ).fetchall():
        key = (row[0], row[1])
        product_map.setdefault(key, []).append(row[2])

    return ean_lookup, model_lookup, article_lookup, name_index, all_db, product_map


def _name_match(item_name: str, name_index: dict) -> tuple[int | None, float]:
    """Jaccard token overlap against all DB product names; returns best (pid, score)."""
    item_tokens = _tokenize(item_name)
    if not item_tokens:
        return None, 0.0
    best_pid, best_score = None, 0.0
    for pid, (_pname, ptokens) in name_index.items():
        if not ptokens:
            continue
        overlap = len(item_tokens & ptokens)
        if not overlap:
            continue
        score = overlap / len(item_tokens | ptokens)
        if score > best_score:
            best_score, best_pid = score, pid
    return (best_pid, best_score) if best_score >= 0.35 else (None, 0.0)


def _map_key(item: dict) -> str:
    """Stable key for the retailer_product_map: SKU if provided, else URL."""
    return item.get("sku") or item.get("url") or ""


def match_items_multi(
    item: dict,
    retailer_id: str,
    ean_lookup: dict,
    model_lookup: dict,
    product_map: dict,
    article_lookup: dict | None = None,
    name_index: dict | None = None,
) -> list[tuple[int, str]]:
    """Return [(product_id, method), ...] — multiple entries for combined product pages.

    Priority: ean → map_lookup → article → model(s) → name_match.
    """
    url  = item.get("url", "")
    name = item.get("name", "")

    # 1. EAN embedded in URL
    ean = extract_ean_from_url(url)
    if ean and ean in ean_lookup:
        return [(ean_lookup[ean][0], "ean")]

    # 2. Persistent map: (retailer, map_key) → known products
    key = _map_key(item)
    if key:
        pids = product_map.get((retailer_id, key))
        if pids:
            return [(pid, "map") for pid in pids]

    # 3. Mestic article number embedded in URL
    if article_lookup:
        article = extract_article_from_url(url)
        if article and article in article_lookup:
            return [(article_lookup[article][0], "article")]

    # 4. All model codes from name — handles combined pages like "RTA-1700i/2200i"
    models = extract_models_from_name(name)
    matched = [(model_lookup[m][0], "model") for m in models if m in model_lookup]
    if matched:
        return matched

    # 5. Token-overlap name match (last resort)
    if name_index:
        pid, _score = _name_match(name, name_index)
        if pid is not None:
            return [(pid, "name_match")]

    return []


# ── database ──────────────────────────────────────────────────────────────────

def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS products (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name   TEXT    NOT NULL,
            model_number   TEXT,
            article_number TEXT,
            ean            TEXT,
            category       TEXT,
            product_url    TEXT    UNIQUE NOT NULL,
            first_seen     TEXT    NOT NULL
        );
        CREATE TABLE IF NOT EXISTS price_snapshots (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id   INTEGER NOT NULL,
            retailer     TEXT    NOT NULL,
            price_eur    REAL,
            scraped_date TEXT    NOT NULL,
            retailer_url TEXT,
            match_method TEXT,
            FOREIGN KEY (product_id) REFERENCES products(id)
        );
        CREATE INDEX IF NOT EXISTS idx_snap_product_date
            ON price_snapshots(product_id, scraped_date);
        CREATE TABLE IF NOT EXISTS retailer_product_map (
            retailer    TEXT    NOT NULL,
            map_key     TEXT    NOT NULL,
            product_id  INTEGER NOT NULL REFERENCES products(id),
            match_method TEXT   NOT NULL,
            mapped_date  TEXT   NOT NULL,
            PRIMARY KEY (retailer, map_key, product_id)
        );
        CREATE INDEX IF NOT EXISTS idx_rp_map_lookup
            ON retailer_product_map(retailer, map_key);
    """)
    conn.commit()


def already_scraped(conn: sqlite3.Connection, retailer_id: str) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) FROM price_snapshots WHERE retailer=? AND scraped_date=?",
        (retailer_id, TODAY),
    ).fetchone()
    return row[0] > 0


def write_snapshots(
    conn:             sqlite3.Connection,
    retailer_id:      str,
    items:            list[dict],
    ean_lookup:       dict,
    model_lookup:     dict,
    all_db_products:  list,
    product_map:      dict,
    article_lookup:   dict | None = None,
    name_index:       dict | None = None,
) -> dict:
    cur = conn.cursor()
    matched_ids: set[int] = set()
    stats: dict[str, int] = {"ean": 0, "map": 0, "article": 0, "model": 0,
                              "name_match": 0, "no_match_retailer": 0, "not_available": 0}

    for item in items:
        matches = match_items_multi(item, retailer_id, ean_lookup, model_lookup, product_map,
                                    article_lookup, name_index)
        if not matches:
            stats["no_match_retailer"] += 1
            continue

        key = _map_key(item)
        for pid, method in matches:
            cur.execute("""
                INSERT INTO price_snapshots
                    (product_id, retailer, price_eur, scraped_date, retailer_url, match_method)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (pid, retailer_id, item.get("price"), TODAY, item.get("url"), method))
            matched_ids.add(pid)
            stats[method] = stats.get(method, 0) + 1

            # Persist to map so future runs skip fuzzy matching
            if method != "map" and key:
                cur.execute("""
                    INSERT OR IGNORE INTO retailer_product_map
                        (retailer, map_key, product_id, match_method, mapped_date)
                    VALUES (?, ?, ?, ?, ?)
                """, (retailer_id, key, pid, method, TODAY))
                # Update in-memory map too so same-day re-runs benefit
                product_map.setdefault((retailer_id, key), [])
                if pid not in product_map[(retailer_id, key)]:
                    product_map[(retailer_id, key)].append(pid)

    # Mark every unmatched DB product as not_available for this retailer today
    for pid, name, ean, model in all_db_products:
        if pid not in matched_ids:
            cur.execute("""
                INSERT INTO price_snapshots
                    (product_id, retailer, price_eur, scraped_date, retailer_url, match_method)
                VALUES (?, ?, NULL, ?, NULL, 'not_available')
            """, (pid, retailer_id, TODAY))
            stats["not_available"] += 1

    conn.commit()
    return stats


# ── per-retailer runner ───────────────────────────────────────────────────────

def run_retailer(
    cfg:             dict,
    conn:            sqlite3.Connection,
    ean_lookup:      dict,
    model_lookup:    dict,
    all_db_products: list,
    product_map:     dict,
    article_lookup:  dict | None = None,
    name_index:      dict | None = None,
) -> None:
    rid  = cfg["id"]
    name = cfg.get("name", rid)
    method_key = cfg.get("method")

    print(f"\n{'─'*60}")
    print(f"Retailer: {name}  [{method_key}]")

    if not cfg.get("enabled", True):
        print("  [skip] disabled in retailers.toml")
        return

    if already_scraped(conn, rid):
        print(f"  [skip] already have data for {TODAY}")
        return

    scrape_fn = METHODS.get(method_key)
    if scrape_fn is None:
        print(f"  [error] unknown method '{method_key}'")
        return

    session = make_session(cfg.get("language", "nl"))
    try:
        print(f"  Fetching …")
        items = scrape_fn(cfg, session)
        print(f"  Retrieved {len(items)} products")
    except Exception as exc:
        import traceback
        print(f"  [ERROR] scrape failed: {exc}")
        traceback.print_exc()
        return

    if len(items) == 0 and SCRAPERAPI_KEY:
        print("  [fallback] 0 products — retrying via ScraperAPI proxy…")
        warnings.filterwarnings("ignore", message="Unverified HTTPS request")
        proxy_url = f"http://scraperapi:{SCRAPERAPI_KEY}@proxy-server.scraperapi.com:8001"
        session.proxies.update({"http": proxy_url, "https": proxy_url})
        session.verify = False
        cfg_proxy = {**cfg, "_proxy": {
            "server": SCRAPERAPI_PROXY,
            "username": "scraperapi",
            "password": SCRAPERAPI_KEY,
        }}
        try:
            items = scrape_fn(cfg_proxy, session)
            print(f"  [fallback] Retrieved {len(items)} products via ScraperAPI")
        except Exception as exc:
            import traceback
            print(f"  [fallback] ScraperAPI also failed: {exc}")
            traceback.print_exc()

    stats = write_snapshots(conn, rid, items, ean_lookup, model_lookup,
                            all_db_products, product_map, article_lookup, name_index)

    # Print result table
    matched = sum(stats.get(m, 0) for m in ("ean", "map", "article", "model", "name_match"))
    print(f"  Matched: {matched}  "
          f"(EAN={stats.get('ean',0)}, map={stats.get('map',0)}, article={stats.get('article',0)}, "
          f"model={stats.get('model',0)}, name={stats.get('name_match',0)})  "
          f"| Not at retailer: {stats['not_available']}  "
          f"| Unrecognised: {stats['no_match_retailer']}")

    # Price comparison
    _print_price_comparison(conn, rid)


def _print_price_comparison(conn: sqlite3.Connection, retailer_id: str) -> None:
    rows = conn.execute("""
        SELECT p.product_name,
               msrp.price_eur,
               obl.price_eur,
               ROUND((msrp.price_eur - obl.price_eur) / msrp.price_eur * 100, 1)
        FROM products p
        JOIN price_snapshots msrp ON msrp.product_id = p.id
                                  AND msrp.retailer = 'mestic.nl'
        JOIN price_snapshots obl  ON obl.product_id  = p.id
                                  AND obl.retailer   = ?
                                  AND obl.scraped_date = ?
                                  AND obl.price_eur IS NOT NULL
        WHERE msrp.scraped_date = (
            SELECT MAX(s.scraped_date) FROM price_snapshots s
            WHERE s.retailer='mestic.nl' AND s.product_id=p.id
        )
        GROUP BY p.id
        ORDER BY (msrp.price_eur - obl.price_eur) DESC
        LIMIT 10
    """, (retailer_id, TODAY)).fetchall()

    if not rows:
        print("  (no price matches to compare)")
        return

    print(f"\n  Top discounts vs Mestic MSRP:")
    print(f"    {'Product':<45} {'MSRP':>8}  {'Retailer':>9}  {'%':>6}")
    print(f"    {'─'*75}")
    for name, msrp, ret, pct in rows:
        diff = ret - msrp
        sign = "+" if diff > 0 else ""
        print(f"    {name[:45]:<45} €{msrp:>7.2f}  €{ret:>8.2f}  {sign}{pct:>5.1f}%")


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    args = sys.argv[1:]

    # Load config
    retailers = load_config()

    if "--list" in args:
        print(f"Configured retailers in {CONFIG_FILE}:")
        for rid, cfg in retailers.items():
            status = "enabled" if cfg.get("enabled", True) else "DISABLED"
            print(f"  {rid:<20} {cfg.get('name',''):<25} [{cfg.get('method','')}]  {status}")
        return

    # Filter by CLI args if any (run specific retailer IDs)
    wanted = [a for a in args if not a.startswith("--")]
    if wanted:
        unknown = [r for r in wanted if r not in retailers]
        if unknown:
            print(f"Unknown retailer IDs: {unknown}")
            print(f"Known: {list(retailers)}")
            sys.exit(1)
        run_ids = wanted
    else:
        run_ids = list(retailers.keys())

    print(f"Mestic retailer scraper  —  {TODAY}")
    print(f"Running: {', '.join(run_ids)}")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    ensure_schema(conn)

    ean_lookup, model_lookup, article_lookup, name_index, all_db_products, product_map = build_db_lookups(conn)
    print(f"DB: {len(all_db_products)} products loaded  "
          f"(EAN={len(ean_lookup)}, article={len(article_lookup)}, model={len(model_lookup)}, "
          f"map_entries={len(product_map)})")

    for rid in run_ids:
        run_retailer(retailers[rid], conn, ean_lookup, model_lookup,
                     all_db_products, product_map, article_lookup, name_index)

    print(f"\n{'═'*60}")
    print(f"All done. Summary for {TODAY}:")
    for row in conn.execute("""
        SELECT retailer,
               COUNT(CASE WHEN price_eur IS NOT NULL THEN 1 END)   found,
               COUNT(CASE WHEN price_eur IS NULL THEN 1 END)       not_avail
        FROM price_snapshots
        WHERE scraped_date = ?
        GROUP BY retailer
        ORDER BY retailer
    """, (TODAY,)):
        print(f"  {row[0]:<20} found={row[1]:>3}  not_available={row[2]:>3}")

    conn.close()


if __name__ == "__main__":
    main()
