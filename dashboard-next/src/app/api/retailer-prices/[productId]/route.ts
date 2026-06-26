import { NextRequest, NextResponse } from "next/server";
import { getRetailerPrices } from "@/lib/db";

export async function GET(_: NextRequest, { params }: { params: { productId: string } }) {
  const data = await getRetailerPrices(Number(params.productId));
  return NextResponse.json(data);
}
