"use client";

import { QueryProvider } from "@/components/providers/QueryProvider";
import { ActivityFeed } from "@/components/sections/ActivityFeed";

function ActivityPageContent() {
  return (
    <div className="space-y-6">
      <div>
        <p className="text-sm text-slate-400">Immutable audit trail</p>
        <h1 className="text-3xl font-semibold text-white">Activity Logs</h1>
      </div>
      <ActivityFeed />
    </div>
  );
}

export default function ActivityPage() {
  return (
    <QueryProvider>
      <ActivityPageContent />
    </QueryProvider>
  );
}
