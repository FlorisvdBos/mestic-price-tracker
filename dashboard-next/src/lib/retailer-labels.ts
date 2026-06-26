export const RETAILER_LABELS: Record<string, string> = {
  "mestic.nl": "Mestic.nl (MSRP)",
  fritz_berger: "Fritz Berger",
  obelink: "Obelink",
  vrijbuiter: "Vrijbuiter",
  wagner: "Wagner",
  kampeerhal_roden: "Kampeerhal Roden",
  bol_com: "Bol.com",
  van_den_elzen: "Van den Elzen",
  amazon_de: "Amazon.de",
  kampeerwereld: "Kampeerwereld",
};

export function retailerLabel(id: string) {
  return RETAILER_LABELS[id] ?? id;
}
