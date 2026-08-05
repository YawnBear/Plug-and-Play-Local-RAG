import type { Metadata } from "next";
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-mono/500.css";
import "@fontsource/ibm-plex-mono/700.css";
import "./globals.css";
import { AppShell } from "@/components/shell/app-shell";

export const metadata: Metadata = {
  title: "Local RAG / Grounded document answers",
  description: "Upload local PDFs and ask citation-grounded questions.",
};

const themeBootScript = `
(() => {
  try {
    const mode = localStorage.getItem("rag-theme") || "system";
    const resolved = mode === "system"
      ? (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")
      : mode;
    document.documentElement.dataset.theme = resolved;
    document.documentElement.dataset.themeMode = mode;
    const motionMode = localStorage.getItem("rag-motion") || "system";
    const reduceMotion = motionMode === "reduced" ||
      (motionMode === "system" && matchMedia("(prefers-reduced-motion: reduce)").matches);
    document.documentElement.dataset.motionMode = motionMode;
    document.documentElement.dataset.reduceMotion = String(reduceMotion);
  } catch {}
})();
`;

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeBootScript }} />
      </head>
      <body>
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
