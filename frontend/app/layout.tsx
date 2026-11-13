import type { Metadata } from "next";
import type { ReactNode } from "react";
import { Atkinson_Hyperlegible, Literata } from "next/font/google";
import "./globals.css";

const atkinson = Atkinson_Hyperlegible({
  subsets: ["latin"],
  weight: ["400", "700"],
  variable: "--font-atkinson",
  display: "swap",
});

const literata = Literata({
  subsets: ["latin"],
  weight: ["600"],
  variable: "--font-literata",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Folio",
  description: "Personal AI agent",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={`${atkinson.variable} ${literata.variable} h-full`}>
      <body className="min-h-full">{children}</body>
    </html>
  );
}
