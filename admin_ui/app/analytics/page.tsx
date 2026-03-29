"use client";

import { QueryProvider } from "@/components/providers/QueryProvider";
import { AnalyticsBoard } from "@/components/sections/AnalyticsBoard";

function AnalyticsPageContent() {
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm text-slate-400">App-level micro analytics</p>
          <h1 className="text-3xl font-semibold text-white">App Analytics</h1>
        </div>
      </div>
      <AnalyticsBoard />
    </div>
  );
}

export default function AnalyticsPage() {
  return (
    <QueryProvider>
      <AnalyticsPageContent />
    </QueryProvider>
  );
}
