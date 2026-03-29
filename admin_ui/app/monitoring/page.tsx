"use client";

import { QueryProvider } from "@/components/providers/QueryProvider";
import { MonitoringBoard } from "@/components/sections/MonitoringBoard";

function MonitoringPageContent() {
  return (
    <div className="space-y-6">
      <div>
        <p className="text-sm text-slate-400">Live health & alerts</p>
        <h1 className="text-3xl font-semibold text-white">Monitoring & Anomalies</h1>
      </div>
      <MonitoringBoard />
    </div>
  );
}

export default function MonitoringPage() {
  return (
    <QueryProvider>
      <MonitoringPageContent />
    </QueryProvider>
  );
}
