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
               ON ps.id = (
                   SELECT id FROM price_snapshots s
                   WHERE s.retailer = 'mestic.nl' AND s.product_id = p.id
                   ORDER BY s.scraped_date DESC, s.id DESC
                   LIMIT 1
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

    # Historical daily prices per product per retailer (last 180 days)
    history: dict[int, dict] = {}
    for row in conn.execute("""
        SELECT product_id, retailer, scraped_date, price_eur
        FROM price_snapshots
        WHERE price_eur IS NOT NULL
          AND scraped_date >= date('now', '-180 days')
        ORDER BY product_id, retailer, scraped_date
    """).fetchall():
        history.setdefault(row["product_id"], {}).setdefault(row["retailer"], []).append(
            {"date": row["scraped_date"], "price": row["price_eur"]}
        )

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
            "name":      row["product_name"],
            "model":     row["model_number"] or "",
            "category":  row["category"] or "",
            "msrp":      msrp,
            "retailers": retailers_data,
            "history":   history.get(pid, {}),
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
# Uses ___DATE___ and ___DATA___ as placeholders (replaced with str.replace)
# so the JS code can use natural { } syntax without escaping.

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mestic Price Dashboard — ___DATE___</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  font-size: 14px;
  background: #f0f2f5;
  color: #1a1a2e;
  line-height: 1.5;
}

/* header */
.site-header {
  background: #1a1a2e;
  color: #fff;
  padding: 20px 32px;
  display: flex;
  align-items: baseline;
  gap: 16px;
}
.site-header h1 { font-size: 20px; font-weight: 700; letter-spacing: -.3px; }
.site-header .meta { font-size: 13px; color: #8899bb; }

/* stats bar */
.stats-bar {
  background: #16213e;
  color: #ccd6f6;
  padding: 10px 32px;
  display: flex;
  gap: 32px;
  font-size: 13px;
}
.stats-bar strong { color: #e2e8f0; }

/* layout */
main { max-width: 1400px; margin: 0 auto; padding: 24px; }

/* section cards */
section {
  background: #fff;
  border-radius: 10px;
  box-shadow: 0 1px 4px rgba(0,0,0,.08);
  margin-bottom: 24px;
  overflow: hidden;
}
.section-head {
  padding: 16px 20px 12px;
  border-bottom: 1px solid #e8eaf0;
  display: flex;
  align-items: center;
  gap: 12px;
}
.section-head h2 { font-size: 15px; font-weight: 700; color: #1a1a2e; }
.section-head .badge {
  background: #eef2ff;
  color: #4361ee;
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 20px;
}

/* tables */
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; }
th, td { padding: 9px 12px; text-align: left; white-space: nowrap; }
th {
  background: #f7f8fc;
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .5px;
  color: #6b7280;
  border-bottom: 2px solid #e8eaf0;
  cursor: pointer;
  user-select: none;
}
th:hover { background: #eef0f8; }
th.sorted-asc::after  { content: " ↑"; }
th.sorted-desc::after { content: " ↓"; }
td { border-bottom: 1px solid #f0f2f5; vertical-align: middle; }
tr:last-child td { border-bottom: none; }

/* clickable product rows */
#products-body tr.data-row { cursor: pointer; }
#products-body tr.data-row:hover td { background: #f0f4ff; }

/* product name */
.prod-name { font-weight: 600; color: #1a1a2e; max-width: 320px; white-space: normal; line-height: 1.3; }
.prod-model { font-size: 11px; color: #8892b0; font-family: monospace; }
.prod-cat   { font-size: 11px; color: #a0aec0; max-width: 160px; white-space: normal; line-height: 1.3; }

/* prices */
.price { font-variant-numeric: tabular-nums; }
.msrp  { color: #4a5568; font-weight: 600; }
.na    { color: #d1d5db; font-size: 12px; }

/* discount badges */
.disc {
  display: inline-flex; align-items: center; gap: 4px;
  border-radius: 5px; padding: 2px 6px; font-weight: 600; font-size: 12px;
}
.disc-hi   { background: #d1fae5; color: #065f46; }
.disc-mid  { background: #ecfdf5; color: #047857; }
.disc-lo   { background: #f0fdf4; color: #16a34a; }
.disc-neg  { background: #fff1f2; color: #be123c; }
.disc-zero { color: #9ca3af; }

/* top-10 */
.top10-table .rank { font-weight: 700; color: #4361ee; width: 32px; text-align: center; }
.top10-table .retailer-chip {
  background: #eef2ff; color: #4361ee;
  padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: 600;
}
.top10-table a { color: #4361ee; text-decoration: none; }
.top10-table a:hover { text-decoration: underline; }

/* search */
.search-wrap { padding: 12px 20px; border-bottom: 1px solid #f0f2f5; }
#search {
  width: 100%; max-width: 360px;
  padding: 7px 12px; border: 1px solid #d1d5db;
  border-radius: 6px; font-size: 13px; outline: none;
}
#search:focus { border-color: #4361ee; box-shadow: 0 0 0 3px #eef2ff; }

/* category group rows */
.cat-row td {
  background: #f7f8fc;
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .5px;
  color: #8892b0;
  padding: 6px 12px;
  border-bottom: 1px solid #e8eaf0;
}

/* ── modal ── */
#modal-overlay {
  display: none;
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,.5);
  z-index: 200;
  align-items: center;
  justify-content: center;
  padding: 24px;
}
#modal-card {
  background: #fff;
  border-radius: 12px;
  box-shadow: 0 24px 64px rgba(0,0,0,.25);
  width: 100%;
  max-width: 860px;
  padding: 28px 28px 24px;
  position: relative;
}
#modal-close {
  position: absolute;
  top: 14px; right: 14px;
  background: none; border: none;
  font-size: 18px; line-height: 1;
  cursor: pointer; color: #6b7280;
  padding: 5px 9px; border-radius: 6px;
}
#modal-close:hover { background: #f3f4f6; color: #1a1a2e; }
#modal-title    { font-size: 16px; font-weight: 700; color: #1a1a2e; padding-right: 40px; }
#modal-subtitle { font-size: 12px; color: #8892b0; font-family: monospace; margin-top: 3px; margin-bottom: 20px; min-height: 16px; }
#modal-chart-wrap { position: relative; height: 320px; }
#modal-no-data  { color: #9ca3af; text-align: center; padding: 80px 0; font-size: 13px; }
</style>
</head>
<body>

<header class="site-header">
  <h1>Mestic Price Dashboard</h1>
  <span class="meta">Data for ___DATE___</span>
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
      <table class="top10-table">
        <thead>
          <tr>
            <th>#</th><th>Product</th><th>Retailer</th>
            <th>MSRP</th><th>Retailer Price</th><th>Saving</th><th>Discount</th>
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
      <input id="search" type="search" placeholder="Filter by product name or model… (click any row for price history)">
    </div>
    <div class="table-wrap">
      <table id="products-table">
        <thead><tr id="products-thead"></tr></thead>
        <tbody id="products-body"></tbody>
      </table>
    </div>
  </section>

</main>

<!-- Price history modal -->
<div id="modal-overlay" onclick="if(event.target===this)closeModal()">
  <div id="modal-card">
    <button id="modal-close" onclick="closeModal()" title="Close (Esc)">✕</button>
    <div id="modal-title"></div>
    <div id="modal-subtitle"></div>
    <div id="modal-chart-wrap">
      <canvas id="modal-canvas"></canvas>
      <div id="modal-no-data" style="display:none">No price history available yet.</div>
    </div>
  </div>
</div>

<script>
const DATA = ___DATA___;

const RETAILER_LABELS = {
  "mestic.nl":      "Mestic.nl (MSRP)",
  fritz_berger:     "Fritz Berger",
  obelink:          "Obelink",
  vrijbuiter:       "Vrijbuiter",
  wagner:           "Wagner",
  kampeerhal_roden: "Kampeerhal Roden",
  bol_com:          "Bol.com",
  van_den_elzen:    "Van den Elzen",
  amazon_de:        "Amazon.de",
  kampeerwereld:    "Kampeerwereld",
};

const RETAILER_COLORS = {
  "mestic.nl":      { border: "#9ca3af", fill: "rgba(156,163,175,.08)", dash: [6,3] },
  fritz_berger:     { border: "#3b82f6", fill: "rgba(59,130,246,.08)",  dash: [] },
  obelink:          { border: "#10b981", fill: "rgba(16,185,129,.08)",  dash: [] },
  vrijbuiter:       { border: "#f59e0b", fill: "rgba(245,158,11,.08)",  dash: [] },
  wagner:           { border: "#8b5cf6", fill: "rgba(139,92,246,.08)",  dash: [] },
  kampeerhal_roden: { border: "#ec4899", fill: "rgba(236,72,153,.08)",  dash: [] },
  bol_com:          { border: "#f97316", fill: "rgba(249,115,22,.08)",   dash: [] },
  van_den_elzen:    { border: "#14b8a6", fill: "rgba(20,184,166,.08)",   dash: [] },
  amazon_de:        { border: "#eab308", fill: "rgba(234,179,8,.08)",    dash: [] },
  kampeerwereld:    { border: "#ef4444", fill: "rgba(239,68,68,.08)",    dash: [] },
};

function fmt(n) {
  return n == null ? null : "€ " + n.toLocaleString("nl-NL", {minimumFractionDigits: 2, maximumFractionDigits: 2});
}

function discBadge(pct) {
  if (pct == null) return '<span class="na">—</span>';
  if (pct === 0)   return '<span class="disc disc-zero">0%</span>';
  const cls  = pct >= 20 ? "disc-hi" : pct >= 10 ? "disc-mid" : pct > 0 ? "disc-lo" : "disc-neg";
  const sign = pct > 0 ? "-" : "+";
  return `<span class="disc ${cls}">${sign}${Math.abs(pct).toFixed(1)}%</span>`;
}

// ── stats ─────────────────────────────────────────────────────────────────────
document.getElementById("stat-products").textContent  = DATA.n_products;
document.getElementById("stat-priced").textContent    = DATA.n_with_prices;
document.getElementById("stat-retailers").textContent = DATA.n_retailers;

// ── top 10 ────────────────────────────────────────────────────────────────────
const top10Body = document.getElementById("top10-body");
DATA.top10.forEach((row, i) => {
  const saving = row.msrp - row.price;
  const link   = row.url ? `<a href="${row.url}" target="_blank">${row.product}</a>` : row.product;
  const label  = RETAILER_LABELS[row.retailer] || row.retailer;
  top10Body.insertAdjacentHTML("beforeend", `
    <tr>
      <td class="rank">${i + 1}</td>
      <td><div class="prod-name">${link}</div>
          ${row.model ? `<div class="prod-model">${row.model}</div>` : ""}</td>
      <td><span class="retailer-chip">${label}</span></td>
      <td class="price msrp">${fmt(row.msrp)}</td>
      <td class="price">${fmt(row.price)}</td>
      <td class="price" style="color:#065f46;font-weight:600">-${fmt(saving).replace("€ ","")} €</td>
      <td>${discBadge(row.discount)}</td>
    </tr>`);
});

// ── products table ────────────────────────────────────────────────────────────
const thead = document.getElementById("products-thead");
const cols  = ["Product", "Model", "Category", "MSRP",
               ...DATA.retailers.map(r => RETAILER_LABELS[r] || r)];
cols.forEach((c, i) => {
  const th = document.createElement("th");
  th.textContent = c;
  th.addEventListener("click", () => sortTable(i));
  thead.appendChild(th);
});
document.getElementById("products-count").textContent = DATA.products.length + " products";

let sortCol = -1, sortDir = 1;
let lastRendered = [];

function cellValue(p, colIdx) {
  if (colIdx === 0) return p.name;
  if (colIdx === 1) return p.model;
  if (colIdx === 2) return p.category;
  if (colIdx === 3) return p.msrp ?? -Infinity;
  const rd = p.retailers[DATA.retailers[colIdx - 4]];
  return rd ? rd.price : -Infinity;
}

function sortTable(col) {
  sortDir = sortCol === col ? sortDir * -1 : 1;
  sortCol = col;
  document.querySelectorAll("#products-thead th").forEach((th, i) => {
    th.className = i === col ? (sortDir === 1 ? "sorted-asc" : "sorted-desc") : "";
  });
  renderProducts();
}

function renderProducts() {
  const q = document.getElementById("search").value.toLowerCase();
  let rows = DATA.products.filter(p =>
    !q || p.name.toLowerCase().includes(q) || p.model.toLowerCase().includes(q)
  );
  if (sortCol >= 0) {
    rows = [...rows].sort((a, b) => {
      const va = cellValue(a, sortCol), vb = cellValue(b, sortCol);
      return typeof va === "string"
        ? va.localeCompare(vb) * sortDir
        : ((va ?? -Infinity) - (vb ?? -Infinity)) * sortDir;
    });
  }

  lastRendered = rows;
  const tbody = document.getElementById("products-body");
  tbody.innerHTML = "";
  let lastCat = null;

  rows.forEach((p, dataIdx) => {
    if (!q && sortCol < 0 && p.category !== lastCat) {
      lastCat = p.category;
      const catRow = tbody.insertRow();
      catRow.className = "cat-row";
      const cell = catRow.insertCell();
      cell.colSpan = cols.length;
      cell.textContent = p.category || "Uncategorised";
    }

    const tr = tbody.insertRow();
    tr.className = "data-row";
    tr.addEventListener("click", () => openModal(dataIdx));

    const nameTd = tr.insertCell();
    nameTd.innerHTML = `<div class="prod-name">${p.name}</div>`;

    const modelTd = tr.insertCell();
    modelTd.innerHTML = p.model ? `<span class="prod-model">${p.model}</span>` : '<span class="na">—</span>';

    const catTd = tr.insertCell();
    catTd.innerHTML = `<span class="prod-cat">${p.category}</span>`;

    const msrpTd = tr.insertCell();
    msrpTd.className = "price msrp";
    msrpTd.textContent = p.msrp ? fmt(p.msrp) : "—";

    DATA.retailers.forEach(r => {
      const td = tr.insertCell();
      const rd = p.retailers[r];
      if (!rd) { td.innerHTML = '<span class="na">—</span>'; return; }
      const priceStr = fmt(rd.price);
      const link = rd.url
        ? `<a href="${rd.url}" target="_blank" style="color:inherit;text-decoration:none">${priceStr}</a>`
        : priceStr;
      td.innerHTML = `<span class="price">${link}</span> ${discBadge(rd.discount)}`;
    });
  });
}

document.getElementById("search").addEventListener("input", renderProducts);
renderProducts();

// ── price history modal ───────────────────────────────────────────────────────
let chartInstance = null;

function openModal(dataIdx) {
  const p = lastRendered[dataIdx];
  document.getElementById("modal-title").textContent    = p.name;
  document.getElementById("modal-subtitle").textContent = p.model || "";
  document.getElementById("modal-overlay").style.display = "flex";
  renderChart(p.history || {});
}

function closeModal() {
  document.getElementById("modal-overlay").style.display = "none";
  if (chartInstance) { chartInstance.destroy(); chartInstance = null; }
}

document.addEventListener("keydown", e => { if (e.key === "Escape") closeModal(); });

function renderChart(history) {
  if (chartInstance) { chartInstance.destroy(); chartInstance = null; }

  const canvas  = document.getElementById("modal-canvas");
  const noData  = document.getElementById("modal-no-data");

  // Collect all unique dates across every retailer
  const allDates = [...new Set(
    Object.values(history).flatMap(entries => entries.map(e => e.date))
  )].sort();

  if (allDates.length === 0) {
    canvas.style.display  = "none";
    noData.style.display  = "block";
    return;
  }
  canvas.style.display = "block";
  noData.style.display = "none";

  const datasets = Object.entries(history)
    .filter(([, entries]) => entries.length > 0)
    .map(([retailer, entries]) => {
      const map = Object.fromEntries(entries.map(e => [e.date, e.price]));
      const c   = RETAILER_COLORS[retailer] || { border: "#6b7280", fill: "rgba(107,114,128,.08)", dash: [] };
      return {
        label:           RETAILER_LABELS[retailer] || retailer,
        data:            allDates.map(d => map[d] ?? null),
        borderColor:     c.border,
        backgroundColor: c.fill,
        borderDash:      c.dash,
        spanGaps:        false,
        tension:         0.2,
        pointRadius:     allDates.length === 1 ? 5 : 3,
        pointHoverRadius: 6,
        fill:            false,
      };
    });

  chartInstance = new Chart(canvas.getContext("2d"), {
    type: "line",
    data: { labels: allDates, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { position: "top", labels: { boxWidth: 12, padding: 16 } },
        tooltip: {
          callbacks: {
            label: ctx => ctx.parsed.y == null
              ? null
              : `${ctx.dataset.label}: ${fmt(ctx.parsed.y)}`,
          },
        },
      },
      scales: {
        x: { grid: { color: "#f0f2f5" } },
        y: {
          grid: { color: "#f0f2f5" },
          ticks: { callback: v => fmt(v) },
        },
      },
    },
  });
}
</script>
</body>
</html>
"""


def main() -> None:
    print(f"Reading {DB_PATH} for {TODAY}…")
    data = load_data()
    print(f"  {data['n_products']} products, {data['n_retailers']} retailers, "
          f"{data['n_with_prices']} products with at least one price")

    html = (TEMPLATE
            .replace("___DATE___", data["date"])
            .replace("___DATA___", json.dumps(data, ensure_ascii=False)))
    Path(OUT_PATH).write_text(html, encoding="utf-8")
    print(f"Written → {OUT_PATH}")


if __name__ == "__main__":
    main()
