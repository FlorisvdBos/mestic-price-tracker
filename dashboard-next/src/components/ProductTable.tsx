"use client";

import { useEffect, useState, useRef, Fragment } from "react";
import { ChevronDown, ChevronRight, ExternalLink } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from "recharts";
import type { ProductRow, RetailerPrice, HistoryPoint } from "@/lib/db";
import { retailerLabel } from "@/lib/retailer-labels";

const fmt = (n: number | null) =>
  n == null ? "—" : new Intl.NumberFormat("nl-NL", { style: "currency", currency: "EUR" }).format(n);

const COLORS = [
  "#3b82f6", "#10b981", "#f59e0b", "#8b5cf6", "#ec4899",
  "#f97316", "#14b8a6", "#eab308", "#ef4444", "#6366f1",
];

type Props = { scrollToId: number | null };

export default function ProductTable({ scrollToId }: Props) {
  const [products, setProducts] = useState<ProductRow[]>([]);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [retailerPrices, setRetailerPrices] = useState<Record<number, RetailerPrice[]>>({});
  const [historyProduct, setHistoryProduct] = useState<ProductRow | null>(null);
  const [history, setHistory] = useState<HistoryPoint[]>([]);
  const [loading, setLoading] = useState(true);
  const rowRefs = useRef<Record<number, HTMLTableRowElement | null>>({});

  useEffect(() => {
    fetch("/api/products")
      .then((r) => r.json())
      .then((d) => { setProducts(d); setLoading(false); });
  }, []);

  useEffect(() => {
    if (scrollToId == null) return;
    const el = rowRefs.current[scrollToId];
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      el.classList.add("bg-primary/10");
      setTimeout(() => el.classList.remove("bg-primary/10"), 1500);
    }
  }, [scrollToId]);

  function toggleExpand(id: number) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) { next.delete(id); return next; }
      next.add(id);
      if (!retailerPrices[id]) {
        fetch(`/api/retailer-prices/${id}`)
          .then((r) => r.json())
          .then((d) => setRetailerPrices((p) => ({ ...p, [id]: d })));
      }
      return next;
    });
  }

  function openHistory(product: ProductRow) {
    setHistoryProduct(product);
    fetch(`/api/price-history/${product.id}`)
      .then((r) => r.json())
      .then((d) => setHistory(d));
  }

  const chartData = buildChartData(history);
  const retailers = Array.from(new Set(history.filter((h) => h.retailer !== "mestic.nl").map((h) => h.retailer)));

  if (loading) return <div className="text-muted-foreground text-sm py-8 text-center">Loading products…</div>;

  const categories = Array.from(new Set(products.map((p) => p.category))).sort();

  return (
    <>
      <section>
        <h2 className="text-xl font-semibold mb-4">
          Products <span className="text-muted-foreground font-normal text-base">({products.length})</span>
        </h2>

        {categories.map((cat) => {
          const catProducts = products.filter((p) => p.category === cat);
          return (
            <Card key={cat} className="mb-4">
              <CardHeader className="py-3 px-4">
                <CardTitle className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">
                  {cat} <span className="font-normal">({catProducts.length})</span>
                </CardTitle>
              </CardHeader>
              <CardContent className="p-0">
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b bg-muted/30 text-left">
                        <th className="px-4 py-2 font-medium text-muted-foreground w-6"></th>
                        <th className="px-4 py-2 font-medium text-muted-foreground">Product</th>
                        <th className="px-4 py-2 font-medium text-muted-foreground text-right hidden sm:table-cell">MSRP</th>
                        <th className="px-4 py-2 font-medium text-muted-foreground text-right">Lowest</th>
                        <th className="px-4 py-2 font-medium text-muted-foreground text-right hidden md:table-cell">Avg</th>
                        <th className="px-4 py-2 font-medium text-muted-foreground text-right hidden md:table-cell">Highest</th>
                        <th className="px-4 py-2 font-medium text-muted-foreground text-right hidden sm:table-cell">Shops</th>
                      </tr>
                    </thead>
                    <tbody>
                      {catProducts.map((p) => (
                        <Fragment key={p.id}>
                          <tr
                            ref={(el) => { rowRefs.current[p.id] = el; }}
                            className="border-b hover:bg-muted/30 transition-colors cursor-pointer"
                            onClick={() => toggleExpand(p.id)}
                          >
                            <td className="px-4 py-3 text-muted-foreground">
                              {expanded.has(p.id) ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                            </td>
                            <td className="px-4 py-3">
                              <button
                                className="font-medium hover:text-primary text-left"
                                onClick={(e) => { e.stopPropagation(); openHistory(p); }}
                              >
                                {p.product_name}
                              </button>
                              <p className="text-xs text-muted-foreground">{p.model_number}</p>
                            </td>
                            <td className="px-4 py-3 text-right hidden sm:table-cell text-muted-foreground">{fmt(p.msrp)}</td>
                            <td className="px-4 py-3 text-right font-medium">
                              {p.min_price ? (
                                <span className="text-green-700">{fmt(p.min_price)}</span>
                              ) : "—"}
                            </td>
                            <td className="px-4 py-3 text-right hidden md:table-cell">{fmt(p.avg_price)}</td>
                            <td className="px-4 py-3 text-right hidden md:table-cell">{fmt(p.max_price)}</td>
                            <td className="px-4 py-3 text-right hidden sm:table-cell">
                              {p.retailer_count > 0 ? (
                                <Badge variant="secondary">{p.retailer_count}</Badge>
                              ) : (
                                <span className="text-muted-foreground">—</span>
                              )}
                            </td>
                          </tr>
                          {expanded.has(p.id) && (
                            <tr className="border-b bg-muted/10">
                              <td colSpan={7} className="px-6 py-3">
                                <RetailerPriceList
                                  prices={retailerPrices[p.id] ?? null}
                                  msrp={p.msrp}
                                  onHistoryClick={() => openHistory(p)}
                                />
                              </td>
                            </tr>
                          )}
                        </Fragment>
                      ))}
                    </tbody>
                  </table>
                </div>
              </CardContent>
            </Card>
          );
        })}
      </section>

      <Dialog open={historyProduct != null} onOpenChange={(o) => !o && setHistoryProduct(null)}>
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>{historyProduct?.product_name}</DialogTitle>
          </DialogHeader>
          {history.length === 0 ? (
            <p className="text-muted-foreground text-sm py-4">No price history available.</p>
          ) : (
            <ResponsiveContainer width="100%" height={320}>
              <LineChart data={chartData} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                <XAxis dataKey="date" tick={{ fontSize: 11 }} />
                <YAxis tickFormatter={(v) => `€${v}`} tick={{ fontSize: 11 }} width={60} />
                <Tooltip formatter={(v: number) => fmt(v)} />
                <Legend />
                {retailers.map((r, i) => (
                  <Line
                    key={r}
                    type="monotone"
                    dataKey={r}
                    name={retailerLabel(r)}
                    stroke={COLORS[i % COLORS.length]}
                    dot={false}
                    connectNulls
                    strokeWidth={2}
                  />
                ))}
              </LineChart>
            </ResponsiveContainer>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}

function RetailerPriceList({
  prices,
  msrp,
  onHistoryClick,
}: {
  prices: RetailerPrice[] | null;
  msrp: number | null;
  onHistoryClick: () => void;
}) {
  if (prices === null) return <p className="text-sm text-muted-foreground">Loading…</p>;
  if (prices.length === 0) return <p className="text-sm text-muted-foreground">Not available at any retailer.</p>;

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between mb-2">
        <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
          Prices by retailer
        </p>
        <button
          className="text-xs text-primary hover:underline"
          onClick={onHistoryClick}
        >
          View price history →
        </button>
      </div>
      {prices.map((rp) => {
        const discount = msrp && rp.price ? Math.round((1 - rp.price / msrp) * 100) : null;
        return (
          <div key={rp.retailer} className="flex items-center justify-between gap-4 text-sm">
            <span className="text-muted-foreground w-36 shrink-0">{retailerLabel(rp.retailer)}</span>
            <span className="font-medium">{fmt(rp.price)}</span>
            {discount !== null && discount > 0 && (
              <Badge variant="success" className="text-xs">{discount}% off</Badge>
            )}
            {rp.url && (
              <a
                href={rp.url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-primary hover:underline ml-auto"
                onClick={(e) => e.stopPropagation()}
              >
                <ExternalLink className="h-3.5 w-3.5" />
              </a>
            )}
          </div>
        );
      })}
    </div>
  );
}

function buildChartData(history: HistoryPoint[]) {
  const byDate: Record<string, Record<string, number>> = {};
  for (const h of history) {
    if (h.retailer === "mestic.nl") continue;
    if (!byDate[h.scraped_date]) byDate[h.scraped_date] = {};
    byDate[h.scraped_date][h.retailer] = h.price;
  }
  return Object.entries(byDate)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, vals]) => ({ date, ...vals }));
}
