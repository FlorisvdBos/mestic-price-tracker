import "server-only";
import fs from "fs";
import path from "path";
import initSqlJs, { Database } from "sql.js";

const DB_PATH = path.resolve(process.cwd(), "../mestic_tracker.db");

let _db: Database | null = null;

async function getDb(): Promise<Database> {
  if (_db) return _db;
  const SQL = await initSqlJs();
  const buf = fs.readFileSync(DB_PATH);
  _db = new SQL.Database(buf);
  return _db;
}

export type PriceChange = {
  product_id: number;
  product_name: string;
  model_number: string;
  category: string;
  retailer: string;
  old_price: number;
  new_price: number;
  change_eur: number;
  change_pct: number;
  new_date: string;
  old_date: string;
};

export type ProductRow = {
  id: number;
  product_name: string;
  model_number: string;
  category: string;
  msrp: number | null;
  min_price: number | null;
  max_price: number | null;
  avg_price: number | null;
  retailer_count: number;
};

export type RetailerPrice = {
  retailer: string;
  price: number;
  scraped_date: string;
  url: string | null;
};

export type HistoryPoint = {
  scraped_date: string;
  retailer: string;
  price: number;
};


function rows<T>(db: Database, sql: string, params: (string | number)[] = []): T[] {
  const stmt = db.prepare(sql);
  stmt.bind(params);
  const result: T[] = [];
  while (stmt.step()) {
    result.push(stmt.getAsObject() as T);
  }
  stmt.free();
  return result;
}

export async function getPriceChanges(since: string): Promise<PriceChange[]> {
  const db = await getDb();
  const sql = `
    SELECT
      p.id          AS product_id,
      p.product_name,
      p.model_number,
      p.category,
      new.retailer,
      old.price_eur AS old_price,
      new.price_eur AS new_price,
      ROUND(new.price_eur - old.price_eur, 2)                              AS change_eur,
      ROUND((new.price_eur - old.price_eur) / old.price_eur * 100.0, 1)   AS change_pct,
      new.scraped_date AS new_date,
      old.scraped_date AS old_date
    FROM products p
    JOIN price_snapshots new ON new.product_id = p.id
      AND new.retailer != 'mestic.nl'
      AND new.scraped_date = (
        SELECT MAX(s.scraped_date) FROM price_snapshots s
        WHERE s.product_id = p.id AND s.retailer = new.retailer
      )
    JOIN price_snapshots old ON old.product_id = p.id
      AND old.retailer = new.retailer
      AND old.scraped_date = (
        SELECT MAX(s.scraped_date) FROM price_snapshots s
        WHERE s.product_id = p.id AND s.retailer = new.retailer
          AND s.scraped_date < new.scraped_date
          AND s.scraped_date >= ?
      )
    WHERE new.price_eur IS NOT NULL
      AND old.price_eur IS NOT NULL
      AND new.price_eur != old.price_eur
    ORDER BY change_eur ASC
  `;
  return rows<PriceChange>(db, sql, [since]);
}

export async function getProducts(): Promise<ProductRow[]> {
  const db = await getDb();
  const sql = `
    SELECT
      p.id,
      p.product_name,
      p.model_number,
      p.category,
      msrp.price_eur  AS msrp,
      MIN(ps.price_eur) AS min_price,
      MAX(ps.price_eur) AS max_price,
      ROUND(AVG(ps.price_eur), 2) AS avg_price,
      COUNT(DISTINCT ps.retailer) AS retailer_count
    FROM products p
    LEFT JOIN price_snapshots msrp
      ON msrp.id = (
        SELECT id FROM price_snapshots s
        WHERE s.retailer = 'mestic.nl' AND s.product_id = p.id
        ORDER BY s.scraped_date DESC, s.id DESC LIMIT 1
      )
    LEFT JOIN price_snapshots ps
      ON ps.product_id = p.id
      AND ps.retailer != 'mestic.nl'
      AND ps.price_eur IS NOT NULL
      AND ps.scraped_date = (
        SELECT MAX(s.scraped_date) FROM price_snapshots s
        WHERE s.product_id = p.id AND s.retailer = ps.retailer
      )
    GROUP BY p.id, p.product_name, p.model_number, p.category, msrp.price_eur
    ORDER BY p.category, p.product_name
  `;
  return rows<ProductRow>(db, sql);
}

export async function getRetailerPrices(productId: number): Promise<RetailerPrice[]> {
  const db = await getDb();
  const sql = `
    SELECT ps.retailer, ps.price_eur AS price, ps.scraped_date, ps.retailer_url AS url
    FROM price_snapshots ps
    WHERE ps.product_id = ?
      AND ps.retailer != 'mestic.nl'
      AND ps.price_eur IS NOT NULL
      AND ps.scraped_date = (
        SELECT MAX(s.scraped_date) FROM price_snapshots s
        WHERE s.product_id = ? AND s.retailer = ps.retailer
      )
    ORDER BY ps.price_eur ASC
  `;
  return rows<RetailerPrice>(db, sql, [productId, productId]);
}

export async function getPriceHistory(productId: number): Promise<HistoryPoint[]> {
  const db = await getDb();
  const sql = `
    SELECT scraped_date, retailer, price_eur AS price
    FROM price_snapshots
    WHERE product_id = ? AND price_eur IS NOT NULL
    ORDER BY scraped_date ASC, retailer ASC
  `;
  return rows<HistoryPoint>(db, sql, [productId]);
}
