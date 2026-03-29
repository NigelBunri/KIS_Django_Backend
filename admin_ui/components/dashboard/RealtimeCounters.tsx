"use client";

type Counter = {
  title: string;
  value: number | string;
  detail?: string;
};

type RealtimeCountersProps = {
  activeUsers: number;
  requestsPerMinute: number;
  errors: number;
  revenue: number;
  loading?: boolean;
};

export function RealtimeCounters({
  activeUsers,
  requestsPerMinute,
  errors,
  revenue,
  loading,
}: RealtimeCountersProps) {
  const counters: Counter[] = [
    {
      title: "Active users",
      value: loading ? "loading…" : activeUsers,
      detail: "24h rolling",
    },
    {
      title: "Requests / min",
      value: loading ? "loading…" : requestsPerMinute,
      detail: "Live stream",
    },
    {
      title: "Errors",
      value: loading ? "loading…" : errors,
      detail: "Recent window",
    },
    {
      title: "Revenue (USD)",
      value: loading ? "loading…" : `$${revenue.toLocaleString(undefined, { maximumFractionDigits: 2 })}`,
      detail: "Cumulative",
    },
  ];

  return (
    <div className="glass-card flex flex-wrap gap-4 rounded-3xl border border-white/5 bg-slate-900/60 p-6">
      {counters.map((counter) => (
        <div key={counter.title} className="min-w-[13rem] space-y-1">
          <p className="text-xs uppercase tracking-widest text-slate-500">{counter.title}</p>
          <p className="text-2xl font-semibold text-white">{counter.value}</p>
          {counter.detail && <p className="text-sm text-slate-400">{counter.detail}</p>}
        </div>
      ))}
    </div>
  );
}
