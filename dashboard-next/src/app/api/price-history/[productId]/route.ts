import { NextRequest, NextResponse } from "next/server";
import { getPriceHistory } from "@/lib/db";

export async function GET(_: NextRequest, { params }: { params: { productId: string } }) {
  const data = await getPriceHistory(Number(params.productId));
  return NextResponse.json(data);
}
