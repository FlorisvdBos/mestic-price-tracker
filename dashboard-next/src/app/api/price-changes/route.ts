import { NextRequest, NextResponse } from "next/server";
import { getPriceChanges } from "@/lib/db";

function sinceDateFor(period: string): string {
  const d = new Date();
  if (period === "week") d.setDate(d.getDate() - 7);
  else if (period === "month") d.setDate(d.getDate() - 30);
  else d.setDate(d.getDate() - 1); // yesterday
  return d.toISOString().slice(0, 10);
}

export async function GET(req: NextRequest) {
  const period = req.nextUrl.searchParams.get("period") ?? "week";
  const since = sinceDateFor(period);
  const data = await getPriceChanges(since);
  return NextResponse.json(data);
}
