#!/usr/bin/env python3
"""
Generate a self-contained dashboard.html from mestic_tracker.db.
Run after the scraper:  python3 generate_dashboard.py
"""

import json
import sqlite3
from datetime import date
from pathlib import Path

DB_PATH  = "mestic_tracker.db"
OUT_PATH = "dashboard.html"
TODAY    = date.today().isoformat()


# ── data loading ──────────────────────────────────────────────────────────────

def load_data() -> dict:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    retailers = [r[0] for r in conn.execute(
        """SELECT DISTINCT retailer FROM price_snapshots
           WHERE scraped_date = ? AND retailer != 'mestic.nl'
           ORDER BY retailer""",
        (TODAY,),
    ).fetchall()]

    product_rows = conn.execute("""
        SELECT p.id, p.product_name, p.model_number, p.category,
               ps.price_eur AS msrp
        FROM products p
        LEFT JOIN price_snapshots ps
               ON ps.product_id = p.id
              AND ps.retailer = 'mestic.nl'
              AND ps.scraped_date = (
                  SELECT MAX(s.scraped_date) FROM price_snapshots s
                  WHERE s.retailer = 'mestic.nl' AND s.product_id = p.id
              )
        ORDER BY p.category, p.product_name
    """).fetchall()

    retailer_prices: dict = {}
    for row in conn.execute("""
        SELECT product_id, retailer, price_eur, retailer_url
        FROM price_snapshots
        WHERE scraped_date = ? AND retailer != 'mestic.nl' AND price_eur IS NOT NULL
    """, (TODAY,)).fetchall():
        retailer_prices[(row["product_id"], row["retailer"])] = {
            "price": row["price_eur"],
            "url":   row["retailer_url"] or "",
        }

    products = []
    for row in product_rows:
        pid  = row["id"]
        msrp = row["msrp"]
        retailers_data = {}
        for r in retailers:
            entry = retailer_prices.get((pid, r))
            if entry and msrp:
                price    = entry["price"]
                discount = round((msrp - price) / msrp * 100, 1)
                retailers_data[r] = {"price": price, "url": entry["url"], "discount": discount}
            elif entry:
                retailers_data[r] = {"price": entry["price"], "url": entry["url"], "discount": None}
            else:
                retailers_data[r] = None

        products.append({
            "name":     row["product_name"],
            "model":    row["model_number"] or "",
            "category": row["category"] or "",
            "msrp":     msrp,
            "retailers": retailers_data,
        })

    top10 = sorted(
        [
            {
                "product":  p["name"],
                "model":    p["model"],
                "retailer": r,
                "msrp":     p["msrp"],
                "price":    rd["price"],
                "discount": rd["discount"],
                "url":      rd["url"],
            }
            for p in products
            for r, rd in p["retailers"].items()
            if rd and rd["discount"] is not None and rd["discount"] > 0 and p["msrp"]
        ],
        key=lambda x: x["discount"],
        reverse=True,
    )[:10]

    scraped = conn.execute(
        "SELECT COUNT(DISTINCT retailer) FROM price_snapshots WHERE scraped_date=? AND retailer!='mestic.nl'",
        (TODAY,),
    ).fetchone()[0]

    conn.close()
    return {
        "date":          TODAY,
        "retailers":     retailers,
        "products":      products,
        "top10":         top10,
        "n_retailers":   scraped,
        "n_products":    len(products),
        "n_with_prices": sum(
            1 for p in products
            if any(rd for rd in p["retailers"].values() if rd and rd["price"] is not None)
        ),
    }


# ── HTML template ─────────────────────────────────────────────────────────────

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mestic Price Dashboard — {date}</title>
<style>
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

body {{
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  font-size: 14px;
  background: #f0f2f5;
  color: #1a1a2e;
  line-height: 1.5;
}}

/* header */
.site-header {{
  background: #1a1a2e;
  color: #fff;
  padding: 20px 32px;
  display: flex;
  align-items: baseline;
  gap: 16px;
}}
.site-header h1 {{ font-size: 20px; font-weight: 700; letter-spacing: -.3px; }}
.site-header .meta {{ font-size: 13px; color: #8899bb; }}

/* stats bar */
.stats-bar {{
  background: #16213e;
  color: #ccd6f6;
  padding: 10px 32px;
  display: flex;
  gap: 32px;
  font-size: 13px;
}}
.stats-bar strong {{ color: #e2e8f0; }}

/* layout */
main {{ max-width: 1400px; margin: 0 auto; padding: 24px 24px; }}

/* section cards */
section {{
  background: #fff;
  border-radius: 10px;
  box-shadow: 0 1px 4px rgba(0,0,0,.08);
  margin-bottom: 24px;
  overflow: hidden;
}}
.section-head {{
  padding: 16px 20px 12px;
  border-bottom: 1px solid #e8eaf0;
  display: flex;
  align-items: center;
  gap: 12px;
}}
.section-head h2 {{ font-size: 15px; font-weight: 700; color: #1a1a2e; }}
.section-head .badge {{
  background: #eef2ff;
  color: #4361ee;
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 20px;
}}

/* tables */
.table-wrap {{ overflow-x: auto; }}
table {{ width: 100%; border-collapse: collapse; }}
th, td {{ padding: 9px 12px; text-align: left; white-space: nowrap; }}
th {{
  background: #f7f8fc;
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .5px;
  color: #6b7280;
  border-bottom: 2px solid #e8eaf0;
  cursor: pointer;
  user-select: none;
}}
th:hover {{ background: #eef0f8; }}
th.sorted-asc::after  {{ content: " ↑"; }}
th.sorted-desc::after {{ content: " ↓"; }}
td {{ border-bottom: 1px solid #f0f2f5; vertical-align: middle; }}
tr:last-child td {{ border-bottom: none; }}
tr:hover td {{ background: #fafbff; }}

/* product name */
.prod-name {{ font-weight: 600; color: #1a1a2e; max-width: 320px; white-space: normal; line-height: 1.3; }}
.prod-model {{ font-size: 11px; color: #8892b0; font-family: monospace; }}
.prod-cat   {{ font-size: 11px; color: #a0aec0; max-width: 160px; white-space: normal; line-height: 1.3; }}

/* prices */
.price {{ font-variant-numeric: tabular-nums; }}
.msrp  {{ color: #4a5568; font-weight: 600; }}
.na    {{ color: #d1d5db; font-size: 12px; }}

/* discount badges */
.disc {{
  display: inline-flex; align-items: center; gap: 4px;
  border-radius: 5px; padding: 2px 6px; font-weight: 600; font-size: 12px;
}}
.disc-hi  {{ background: #d1fae5; color: #065f46; }}  /* ≥ 20 % */
.disc-mid {{ background: #ecfdf5; color: #047857; }}  /* 10–19 % */
.disc-lo  {{ background: #f0fdf4; color: #16a34a; }}  /*  1–9  % */
.disc-neg {{ background: #fff1f2; color: #be123c; }}  /* negative */
.disc-zero {{ color: #9ca3af; }}

/* top-10 */
.top10-table .rank {{ font-weight: 700; color: #4361ee; width: 32px; text-align: center; }}
.top10-table .retailer-chip {{
  background: #eef2ff; color: #4361ee;
  padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: 600;
}}
.top10-table a {{ color: #4361ee; text-decoration: none; }}
.top10-table a:hover {{ text-decoration: underline; }}

/* search */
.search-wrap {{ padding: 12px 20px; border-bottom: 1px solid #f0f2f5; }}
#search {{
  width: 100%; max-width: 360px;
  padding: 7px 12px; border: 1px solid #d1d5db;
  border-radius: 6px; font-size: 13px; outline: none;
}}
#search:focus {{ border-color: #4361ee; box-shadow: 0 0 0 3px #eef2ff; }}

/* category group rows */
.cat-row td {{
  background: #f7f8fc;
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .5px;
  color: #8892b0;
  padding: 6px 12px;
  border-bottom: 1px solid #e8eaf0;
}}
</style>
</head>
<body>

<header class="site-header">
  <h1>Mestic Price Dashboard</h1>
  <span class="meta">Data for {date}</span>
</header>

<div class="stats-bar">
  <span>Products: <strong id="stat-products"></strong></span>
  <span>With retailer prices: <strong id="stat-priced"></strong></span>
  <span>Retailers: <strong id="stat-retailers"></strong></span>
</div>

<main>

  <!-- Top 10 -->
  <section>
    <div class="section-head">
      <h2>Top 10 Biggest Discounts Today</h2>
      <span class="badge">vs Mestic MSRP</span>
    </div>
    <div class="table-wrap">
      <table class="top10-table" id="top10-table">
        <thead>
          <tr>
            <th>#</th>
            <th>Product</th>
            <th>Retailer</th>
            <th>MSRP</th>
            <th>Retailer Price</th>
            <th>Saving</th>
            <th>Discount</th>
          </tr>
        </thead>
        <tbody id="top10-body"></tbody>
      </table>
    </div>
  </section>

  <!-- All Products -->
  <section>
    <div class="section-head">
      <h2>All Products</h2>
      <span class="badge" id="products-count"></span>
    </div>
    <div class="search-wrap">
      <input id="search" type="search" placeholder="Filter by product name or model…">
    </div>
    <div class="table-wrap">
      <table id="products-table">
        <thead><tr id="products-thead"></tr></thead>
        <tbody id="products-body"></tbody>
      </table>
    </div>
  </section>

</main>

<script>
const DATA = {data_json};

const RETAILER_LABELS = {{
  fritz_berger: "Fritz Berger",
  obelink:      "Obelink",
  vrijbuiter:   "Vrijbuiter",
  wagner:       "Wagner",
}};

function fmt(n) {{
  return n == null ? null : "€ " + n.toLocaleString("nl-NL", {{minimumFractionDigits: 2, maximumFractionDigits: 2}});
}}

function discBadge(pct) {{
  if (pct == null) return '<span class="na">—</span>';
  if (pct === 0)   return '<span class="disc disc-zero">0%</span>';
  const cls = pct >= 20 ? "disc-hi" : pct >= 10 ? "disc-mid" : pct > 0 ? "disc-lo" : "disc-neg";
  const sign = pct > 0 ? "-" : "+";
  return `<span class="disc ${{cls}}">${{sign}}${{Math.abs(pct).toFixed(1)}}%</span>`;
}}

// ── stats ─────────────────────────────────────────────────────────────────────
document.getElementById("stat-products").textContent  = DATA.n_products;
document.getElementById("stat-priced").textContent    = DATA.n_with_prices;
document.getElementById("stat-retailers").textContent = DATA.n_retailers;

// ── top 10 ────────────────────────────────────────────────────────────────────
const top10Body = document.getElementById("top10-body");
DATA.top10.forEach((row, i) => {{
  const saving = row.msrp - row.price;
  const link   = row.url ? `<a href="${{row.url}}" target="_blank">${{row.product}}</a>` : row.product;
  const label  = RETAILER_LABELS[row.retailer] || row.retailer;
  top10Body.insertAdjacentHTML("beforeend", `
    <tr>
      <td class="rank">${{i + 1}}</td>
      <td><div class="prod-name">${{link}}</div>
          ${{row.model ? `<div class="prod-model">${{row.model}}</div>` : ""}}</td>
      <td><span class="retailer-chip">${{label}}</span></td>
      <td class="price msrp">${{fmt(row.msrp)}}</td>
      <td class="price">${{fmt(row.price)}}</td>
      <td class="price" style="color:#065f46;font-weight:600">-${{fmt(saving).replace("€ ","")}} €</td>
      <td>${{discBadge(row.discount)}}</td>
    </tr>`);
}});

// ── products table ────────────────────────────────────────────────────────────
const thead = document.getElementById("products-thead");
const cols  = ["Product", "Model", "Category", "MSRP", ...DATA.retailers.map(r => RETAILER_LABELS[r] || r)];
cols.forEach((c, i) => {{
  const th = document.createElement("th");
  th.textContent   = c;
  th.dataset.col   = i;
  th.addEventListener("click", () => sortTable(i));
  thead.appendChild(th);
}});
document.getElementById("products-count").textContent = DATA.products.length + " products";

let sortCol = -1, sortDir = 1;

function cellValue(p, colIdx) {{
  if (colIdx === 0) return p.name;
  if (colIdx === 1) return p.model;
  if (colIdx === 2) return p.category;
  if (colIdx === 3) return p.msrp ?? -Infinity;
  const r  = DATA.retailers[colIdx - 4];
  const rd = p.retailers[r];
  return rd ? rd.price : -Infinity;
}}

function sortTable(col) {{
  if (sortCol === col) {{ sortDir *= -1; }}
  else                 {{ sortCol = col; sortDir = 1; }}
  document.querySelectorAll("#products-thead th").forEach((th, i) => {{
    th.className = i === col ? (sortDir === 1 ? "sorted-asc" : "sorted-desc") : "";
  }});
  renderProducts();
}}

function renderProducts() {{
  const q = document.getElementById("search").value.toLowerCase();
  let rows = DATA.products.filter(p =>
    !q || p.name.toLowerCase().includes(q) || p.model.toLowerCase().includes(q)
  );

  if (sortCol >= 0) {{
    rows = [...rows].sort((a, b) => {{
      const va = cellValue(a, sortCol);
      const vb = cellValue(b, sortCol);
      if (typeof va === "string") return va.localeCompare(vb) * sortDir;
      return ((va ?? -Infinity) - (vb ?? -Infinity)) * sortDir;
    }});
  }}

  const tbody = document.getElementById("products-body");
  tbody.innerHTML = "";

  let lastCat = null;
  rows.forEach(p => {{
    // Category group header (only when not filtering/sorting)
    if (!q && sortCol < 0 && p.category !== lastCat) {{
      lastCat = p.category;
      const catRow = tbody.insertRow();
      catRow.className = "cat-row";
      const cell = catRow.insertCell();
      cell.colSpan = cols.length;
      cell.textContent = p.category || "Uncategorised";
    }}

    const tr = tbody.insertRow();

    // Name
    const nameTd = tr.insertCell();
    nameTd.innerHTML = `<div class="prod-name">${{p.name}}</div>`;

    // Model
    const modelTd = tr.insertCell();
    modelTd.innerHTML = p.model ? `<span class="prod-model">${{p.model}}</span>` : '<span class="na">—</span>';

    // Category
    const catTd = tr.insertCell();
    catTd.innerHTML = `<span class="prod-cat">${{p.category}}</span>`;

    // MSRP
    const msrpTd = tr.insertCell();
    msrpTd.className = "price msrp";
    msrpTd.textContent = p.msrp ? fmt(p.msrp) : "—";

    // Retailer columns
    DATA.retailers.forEach(r => {{
      const td = tr.insertCell();
      const rd = p.retailers[r];
      if (!rd) {{
        td.innerHTML = '<span class="na">—</span>';
        return;
      }}
      const priceStr = fmt(rd.price);
      const link     = rd.url
        ? `<a href="${{rd.url}}" target="_blank" style="color:inherit;text-decoration:none">${{priceStr}}</a>`
        : priceStr;
      td.innerHTML = `<span class="price">${{link}}</span> ${{discBadge(rd.discount)}}`;
    }});
  }});
}}

document.getElementById("search").addEventListener("input", renderProducts);
renderProducts();
</script>
</body>
</html>
"""


def main() -> None:
    print(f"Reading {DB_PATH} for {TODAY}…")
    data = load_data()
    print(f"  {data['n_products']} products, {data['n_retailers']} retailers, "
          f"{data['n_with_prices']} products with at least one price")

    html = TEMPLATE.format(
        date=data["date"],
        data_json=json.dumps(data, ensure_ascii=False),
    )
    Path(OUT_PATH).write_text(html, encoding="utf-8")
    print(f"Written → {OUT_PATH}")


if __name__ == "__main__":
    main()
