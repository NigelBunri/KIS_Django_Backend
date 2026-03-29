import "@/styles/globals.css";
import type { ReactNode } from "react";
import { QueryProvider } from "@/components/providers/QueryProvider";
import { ThemeProvider } from "@/components/providers/ThemeProvider";
import { Sidebar } from "@/components/ui/Sidebar";
import { TopNav } from "@/components/ui/TopNav";

export const metadata = {
  title: "Admin Control & Observability",
  description: "Enterprise-grade admin panel for monitoring, analytics, and CRUD management"
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning className="h-full">
      <body className="h-full antialiased">
        <QueryProvider>
          <ThemeProvider>
            <div className="flex h-full min-h-screen">
            <Sidebar />
            <div className="flex flex-1 flex-col overflow-hidden">
              <TopNav />
              <main className="flex-1 overflow-y-auto p-6">{children}</main>
            </div>
          </div>
        </ThemeProvider>
        </QueryProvider>
      </body>
    </html>
  );
}
