"use client";

import { useState } from "react";
import PriceChanges from "@/components/PriceChanges";
import ProductTable from "@/components/ProductTable";

export default function Home() {
  const [scrollToId, setScrollToId] = useState<number | null>(null);

  return (
    <>
      <PriceChanges onSelect={(id) => setScrollToId(id)} />
      <ProductTable scrollToId={scrollToId} />
    </>
  );
}
