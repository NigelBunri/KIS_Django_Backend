"use client";

import { useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useMonitoringAlerts } from "@/hooks/useMonitoringAlerts";
import { usePerformanceInsights } from "@/hooks/usePerformanceInsights";
import { useTheme } from "next-themes";

export function MonitoringBoard() {
  const alertsQuery = useMonitoringAlerts();
  const performanceQuery = usePerformanceInsights();
  const alerts = alertsQuery.data ?? [];
  const insights = performanceQuery.data ?? {};
  const peakHours = insights.peak_hours ?? [];
  const topEndpoints = insights.top_endpoints ?? [];
  const dbGrowth = insights.db_growth ?? {};
  const memoryUsage = insights.memory_usage ?? {};

  const [acknowledged, setAcknowledged] = useState<Record<string, boolean>>({});

  const avgLatency = insights.average_response_ms ?? 0;
  const slowQueries = insights.slow_queries ?? 0;
  const cacheHit = insights.cache_hit_rate ?? 0;
  const totalRequests = insights.total_requests ?? 0;

  const dbGrowthSeries = useMemo(
    () =>
      Object.entries(dbGrowth).map(([label, value]) => ({ label, value })),
    [dbGrowth]
  );

  const heatmap = useMemo(() => {
    const allHours = Array.from({ length: 24 }, (_, index) => index);
    const lookup = Object.fromEntries(
      (peakHours ?? []).map((entry: Record<string, any>) => [entry.hour, entry.count])
    );
    return allHours.map((hour) => ({ hour, count: lookup[hour] ?? 0 }));
  }, [peakHours]);

  const alertTimeline = useMemo(() => alerts.slice(0, 5), [alerts]);

  const handleAcknowledge = (id: string) => {
    setAcknowledged((prev) => ({ ...prev, [id]: true }));
  };

  const { resolvedTheme } = useTheme();
  const isLight = resolvedTheme === "light";

  const peakTextColor = isLight ? "text-slate-900" : "text-slate-200";
  const peakSubTextColor = isLight ? "text-slate-500" : "text-slate-400";

  return (
    <div className="space-y-6">
      <div className="grid gap-4 lg:grid-cols-2">
        <div className="glass-card rounded-3xl border border-white/5 bg-slate-900/70 p-5">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-xs uppercase tracking-widest text-slate-500">Live incidents</p>
              <h2 className="text-lg font-semibold text-white">Monitoring alerts</h2>
            </div>
            <span className="text-xs text-slate-400">Realtime</span>
          </div>
          <div className="mt-4 space-y-3">
            {alerts.length === 0 && (
              <p className="text-sm text-slate-500">No incidents detected.</p>
            )}
            {alerts.map((alert) => (
              <div
                key={`${alert.type}-${alert.value}-${alert.message}`}
                className={`rounded-2xl border border-white/5 bg-slate-950/60 p-4 ${
                  acknowledged[alert.type] ? "opacity-60" : ""
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className="text-xs uppercase tracking-widest text-slate-500">{alert.type}</span>
                  <span className={`text-xs ${alert.severity === "critical" ? "text-rose-400" : "text-amber-300"}`}>
                    {alert.severity?.toUpperCase() ?? "Info"}
                  </span>
                </div>
                <p className="mt-2 text-base font-semibold text-white">{alert.message}</p>
                <div className="mt-2 flex items-center justify-between text-xs text-slate-400">
                  <span>Value: {alert.value ?? "—"}</span>
                  <button
                    onClick={() => handleAcknowledge(alert.type)}
                    className="rounded-full border border-slate-800 px-3 py-1 text-[10px] uppercase tracking-widest"
                  >
                    Acknowledge
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
        <div className="glass-card rounded-3xl border border-white/5 bg-slate-900/70 p-5">
          <p className="text-xs uppercase tracking-widest text-slate-500">Performance</p>
          <h2 className="text-lg font-semibold text-white">System health</h2>
          <div className="mt-4 grid gap-3">
            <div className="rounded-2xl border border-white/5 bg-slate-950/60 p-4">
              <p className="text-xs text-slate-400">Avg response time</p>
              <p className="text-2xl font-semibold text-white">{avgLatency.toFixed(1)} ms</p>
            </div>
            <div className="rounded-2xl border border-white/5 bg-slate-950/60 p-4">
              <p className="text-xs text-slate-400">Slow queries</p>
              <p className="text-2xl font-semibold text-white">{slowQueries}</p>
            </div>
            <div className="rounded-2xl border border-white/5 bg-slate-950/60 p-4">
              <p className="text-xs text-slate-400">Cache hit rate</p>
              <p className="text-2xl font-semibold text-white">{cacheHit}%</p>
            </div>
            <div className="rounded-2xl border border-white/5 bg-slate-950/60 p-4">
              <p className="text-xs text-slate-400">Memory (GB)</p>
              <p className="text-2xl font-semibold text-white">
                {memoryUsage.used_gb ?? "—"} / {memoryUsage.total_gb ?? "—"}
              </p>
            </div>
          </div>
          <p className="mt-3 text-xs text-slate-500">Total requests: {totalRequests}</p>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="glass-card rounded-3xl border border-white/5 bg-slate-900/70 p-5">
          <div className="flex items-center justify-between">
            <p className="text-xs uppercase tracking-widest text-slate-500">DB growth</p>
            <span className="text-xs text-slate-400">Rows per table</span>
          </div>
          <div className="mt-4 h-44">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={dbGrowthSeries}>
                <CartesianGrid stroke="#1e293b" strokeDasharray="3 3" />
                <XAxis dataKey="label" tick={{ fill: "#94a3b8" }} />
                <YAxis tick={{ fill: "#94a3b8" }} />
                <Tooltip />
                <Bar dataKey="value" fill="#22d3ee" radius={[6, 6, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
        <div className="glass-card rounded-3xl border border-white/5 bg-slate-900/70 p-5">
          <div className="flex items-center justify-between">
            <p className="text-xs uppercase tracking-widest text-slate-500">Top endpoints</p>
            <span className="text-xs text-slate-400">By load</span>
          </div>
          <div className="mt-4 h-44">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={topEndpoints}>
                <CartesianGrid stroke="#1e293b" strokeDasharray="3 3" />
                <XAxis dataKey="endpoint" tick={{ fill: "#94a3b8" }} />
                <YAxis tick={{ fill: "#94a3b8" }} />
                <Tooltip />
                <Line type="monotone" dataKey="count" stroke="#f472b6" strokeWidth={3} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      <div className="glass-card rounded-3xl border border-white/5 bg-slate-900/70 p-5">
        <div className="flex items-center justify-between">
          <p className="text-xs uppercase tracking-widest text-slate-500">Peak traffic</p>
          <span className="text-xs text-slate-400">Hourly heatmap</span>
        </div>
        <div className="mt-4 grid grid-cols-4 gap-3 text-xs">
        {heatmap.map((entry) => (
            <div
              key={entry.hour}
              className={`rounded-2xl border border-white/5 p-3 text-center ${
                isLight
                  ? "bg-gradient-to-b from-slate-100 to-white/80 border-slate-200"
                  : "bg-gradient-to-b from-slate-900 to-slate-950"
              }`}
            >
              <div className={`text-lg font-semibold ${peakTextColor}`}>{entry.hour}:00</div>
              <p className={`text-xs ${peakSubTextColor}`}>{entry.count} ops</p>
            </div>
          ))}
        </div>
      </div>

      <div className="glass-card rounded-3xl border border-white/5 bg-slate-900/70 p-5">
        <p className="text-xs uppercase tracking-widest text-slate-500">Alert history</p>
        <div className="mt-3 space-y-2">
          {alertTimeline.length === 0 ? (
            <p className="text-sm text-slate-500">No alert history yet.</p>
          ) : (
            alertTimeline.map((alert) => (
              <div key={`${alert.type}-${alert.message}`} className="flex items-center justify-between rounded-2xl border border-white/5 bg-slate-950/60 p-3 text-xs">
                <div>
                  <p className="font-semibold text-white">{alert.type}</p>
                  <p className="text-slate-400">{alert.message}</p>
                </div>
                <span className="text-slate-300">Severity {alert.severity}</span>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
