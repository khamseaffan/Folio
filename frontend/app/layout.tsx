import type { Metadata } from "next";
import type { ReactNode } from "react";
import { Outfit, Shippori_Mincho } from "next/font/google";
import "./globals.css";

const outfit = Outfit({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-outfit",
  display: "swap",
});

const mincho = Shippori_Mincho({
  subsets: ["latin"],
  weight: ["500", "600"],
  variable: "--font-mincho",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Folio",
  description: "A private assistant that remembers your work.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={`${outfit.variable} ${mincho.variable} h-full`}>
      <body className="min-h-full">{children}</body>
    </html>
  );
}
