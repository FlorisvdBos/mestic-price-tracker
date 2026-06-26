import { NextResponse } from "next/server";
import { getProducts } from "@/lib/db";

export async function GET() {
  const data = await getProducts();
  return NextResponse.json(data);
}
