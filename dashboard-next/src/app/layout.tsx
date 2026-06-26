import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Mestic Price Tracker",
  description: "Live price monitoring across European retailers",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-background antialiased">
        <header className="border-b bg-white sticky top-0 z-40">
          <div className="container mx-auto px-4 h-14 flex items-center gap-3">
            <span className="text-xl font-bold text-primary">Mestic</span>
            <span className="text-muted-foreground text-sm">Price Tracker</span>
          </div>
        </header>
        <main className="container mx-auto px-4 py-6 space-y-8">{children}</main>
      </body>
    </html>
  );
}
