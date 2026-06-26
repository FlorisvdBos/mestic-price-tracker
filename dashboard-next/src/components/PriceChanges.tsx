"use client";

import { useEffect, useState, useMemo } from "react";
import { TrendingDown, TrendingUp } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { PriceChange } from "@/lib/db";
import { retailerLabel } from "@/lib/retailer-labels";

const fmt = (n: number) =>
  new Intl.NumberFormat("nl-NL", { style: "currency", currency: "EUR" }).format(n);

type Props = { onSelect: (productId: number) => void };

export default function PriceChanges({ onSelect }: Props) {
  const [changes, setChanges] = useState<PriceChange[]>([]);
  const [period, setPeriod] = useState("week");
  const [sortBy, setSortBy] = useState<"eur" | "pct">("eur");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetch(`/api/price-changes?period=${period}`)
      .then((r) => r.json())
      .then((d) => { setChanges(d); setLoading(false); });
  }, [period]);

  const sorted = useMemo(() => {
    const key = sortBy === "eur" ? "change_eur" : "change_pct";
    return [...changes].sort((a, b) => a[key] - b[key]);
  }, [changes, sortBy]);

  const drops = sorted.filter((c) => c.change_eur < 0);
  const rises = sorted.filter((c) => c.change_eur > 0);

  return (
    <section>
      <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
        <h2 className="text-xl font-semibold">Price Changes</h2>
        <div className="flex items-center gap-2 flex-wrap">
          <div className="flex rounded-md border overflow-hidden text-sm">
            {(["yesterday", "week", "month"] as const).map((p) => (
              <button
                key={p}
                onClick={() => setPeriod(p)}
                className={`px-3 py-1.5 transition-colors ${
                  period === p ? "bg-primary text-primary-foreground" : "bg-white hover:bg-muted"
                }`}
              >
                {p === "yesterday" ? "Yesterday" : p === "week" ? "7 days" : "30 days"}
              </button>
            ))}
          </div>
          <Select value={sortBy} onValueChange={(v) => setSortBy(v as "eur" | "pct")}>
            <SelectTrigger className="w-28 h-9 text-sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="eur">Sort by €</SelectItem>
              <SelectItem value="pct">Sort by %</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>

      {loading ? (
        <div className="text-muted-foreground text-sm py-8 text-center">Loading…</div>
      ) : drops.length === 0 && rises.length === 0 ? (
        <div className="text-muted-foreground text-sm py-8 text-center">No price changes found for this period.</div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {drops.length > 0 && (
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-base flex items-center gap-2 text-green-700">
                  <TrendingDown className="h-4 w-4" />
                  Price Drops ({drops.length})
                </CardTitle>
              </CardHeader>
              <CardContent className="p-0">
                <div className="divide-y max-h-96 overflow-y-auto">
                  {drops.map((c, i) => (
                    <ChangeRow key={i} change={c} onSelect={onSelect} />
                  ))}
                </div>
              </CardContent>
            </Card>
          )}
          {rises.length > 0 && (
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-base flex items-center gap-2 text-red-700">
                  <TrendingUp className="h-4 w-4" />
                  Price Rises ({rises.length})
                </CardTitle>
              </CardHeader>
              <CardContent className="p-0">
                <div className="divide-y max-h-96 overflow-y-auto">
                  {rises.map((c, i) => (
                    <ChangeRow key={i} change={c} onSelect={onSelect} />
                  ))}
                </div>
              </CardContent>
            </Card>
          )}
        </div>
      )}
    </section>
  );
}

function ChangeRow({ change: c, onSelect }: { change: PriceChange; onSelect: (id: number) => void }) {
  const isDown = c.change_eur < 0;
  return (
    <button
      className="w-full text-left px-4 py-3 hover:bg-muted/50 transition-colors"
      onClick={() => onSelect(c.product_id)}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm font-medium truncate">{c.product_name}</p>
          <p className="text-xs text-muted-foreground">{retailerLabel(c.retailer)}</p>
        </div>
        <div className="text-right shrink-0">
          <Badge variant={isDown ? "success" : "destructive"} className="mb-1">
            {isDown ? "" : "+"}
            {fmt(c.change_eur)} ({isDown ? "" : "+"}
            {c.change_pct}%)
          </Badge>
          <p className="text-xs text-muted-foreground">
            {fmt(c.old_price)} → {fmt(c.new_price)}
          </p>
        </div>
      </div>
      <p className="text-xs text-muted-foreground mt-1">
        {c.old_date} → {c.new_date}
      </p>
    </button>
  );
}
