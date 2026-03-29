"use client";

import { useMemo } from "react";

type StatsItem = {
  label: string;
  value: string | number;
  delta?: string;
  detail?: string;
};

export function StatsGrid({ stats }: { stats: StatsItem[] }) {
  const items = useMemo(
    () => stats.map((item) => ({ ...item, key: item.label.toLowerCase().replace(/\s+/g, "-") })),
    [stats]
  );

  return (
    <div className="grid gap-4 md:grid-cols-3 lg:grid-cols-4">
      {items.map((item) => (
        <div
          key={item.key}
          className="glass-card rounded-3xl border border-white/5 bg-slate-900/80 p-6"
        >
          <p className="text-xs uppercase tracking-widest text-slate-500">{item.label}</p>
          <p className="mt-2 text-3xl font-semibold text-white">{item.value}</p>
          {item.delta && <p className="mt-1 text-sm text-emerald-400">{item.delta}</p>}
          {item.detail && <p className="mt-3 text-sm text-slate-400">{item.detail}</p>}
        </div>
      ))}
    </div>
  );
}
