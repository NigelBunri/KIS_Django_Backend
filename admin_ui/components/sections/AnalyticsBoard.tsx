"use client";

import { useEffect, useMemo, useState } from "react";
import { BarChart, Bar, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useMicroAnalytics } from "@/hooks/useMicroAnalytics";

type MicroInsight = {
  app_label: string;
  usage_frequency: number;
  conversion_rate: number;
  feature_adoption: number;
  top_models?: { model: string; actions: number }[];
  top_users?: { user_id: number; actions: number }[];
  crud_heatmap?: Record<string, Record<number, number>>;
};

export function AnalyticsBoard() {
  const { data: insights = [], isLoading } = useMicroAnalytics();
  const [search, setSearch] = useState("");
  const filteredApps = useMemo(
    () =>
      insights.filter((insight) =>
        insight.app_label.toLowerCase().includes(search.toLowerCase())
      ),
    [insights, search]
  );
  const [activeApp, setActiveApp] = useState<MicroInsight | null>(
    () => filteredApps[0] ?? null
  );

  useEffect(() => {
    if (!filteredApps.length) {
      setActiveApp(null);
      return;
    }
    if (!activeApp || !filteredApps.some((app) => app.app_label === activeApp.app_label)) {
      setActiveApp(filteredApps[0]);
    }
  }, [filteredApps, activeApp]);

  const topModels = activeApp?.top_models ?? [];
  const topUsers = activeApp?.top_users ?? [];
  const heatmapBuckets = useMemo(() => {
    if (!activeApp?.crud_heatmap) {
      return [];
    }
    return Object.entries(activeApp.crud_heatmap).map(([method, hours]) => ({
      method,
      totals: hours,
    }));
  }, [activeApp]);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-sm text-slate-400">App-level micro analytics</p>
          <h1 className="text-3xl font-semibold text-white">App Analytics</h1>
        </div>
        <div className="flex items-center gap-2">
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Filter apps..."
            className="rounded-2xl border border-slate-800 bg-slate-900/70 px-4 py-2 text-sm text-slate-200 placeholder:text-slate-500 focus:outline-none focus:ring-2 focus:ring-indigo-500"
          />
          <button className="rounded-2xl border border-slate-800 px-4 py-2 text-xs uppercase tracking-widest text-slate-500">
            Export CSV
          </button>
        </div>
      </div>
      <div className="glass-card space-y-4 rounded-3xl border border-white/5 bg-slate-900/70 p-5">
        <div className="flex flex-wrap gap-2 text-sm">
          {filteredApps.map((insight) => (
            <button
              key={insight.app_label}
              onClick={() => setActiveApp(insight)}
              className={`rounded-2xl px-3 py-1 text-xs font-semibold transition ${
                activeApp?.app_label === insight.app_label
                  ? "bg-indigo-500/40 text-white"
                  : "bg-slate-900/80 text-slate-400 hover:bg-slate-800/70"
              }`}
            >
              {insight.app_label}
            </button>
          ))}
          {!filteredApps.length && (
            <span className="text-xs text-slate-500">No apps match your search</span>
          )}
        </div>
        {isLoading && (
          <p className="text-sm text-slate-500">Loading insight data…</p>
        )}
        {activeApp && !isLoading && (
          <div className="grid gap-4 lg:grid-cols-2">
            <div className="space-y-3">
              <p className="text-xs uppercase tracking-widest text-slate-500">
                Usage
              </p>
              <p className="text-3xl font-semibold text-white">
                {activeApp.usage_frequency.toLocaleString()}
              </p>
              <p className="text-sm text-slate-400">
                Conversion: {activeApp.conversion_rate.toFixed(2)}% • Adoption:{" "}
                {activeApp.feature_adoption.toFixed(1)}%
              </p>
              <div className="space-y-2">
                <h4 className="text-sm font-semibold text-white">Top models</h4>
                {topModels.length ? (
                  topModels.map((entry) => (
                    <div
                      key={entry.model}
                      className="flex items-center justify-between text-sm text-slate-200"
                    >
                      <span>{entry.model}</span>
                      <span className="text-xs text-slate-400">
                        {entry.actions} actions
                      </span>
                    </div>
                  ))
                ) : (
                  <p className="text-xs text-slate-500">No model activity yet</p>
                )}
              </div>
              <div className="space-y-2">
                <h4 className="text-sm font-semibold text-white">Top users</h4>
                {topUsers.length ? (
                  topUsers.map((entry) => (
                    <div
                      key={entry.user_id}
                      className="flex items-center justify-between text-sm text-slate-200"
                    >
                      <span>User {entry.user_id}</span>
                      <span className="text-xs text-slate-400">
                        {entry.actions} interactions
                      </span>
                    </div>
                  ))
                ) : (
                  <p className="text-xs text-slate-500">No user data yet</p>
                )}
              </div>
            </div>
            <div className="space-y-4">
              <div>
                <h4 className="text-sm font-semibold text-white">Model heatmap</h4>
                <div className="mt-3 grid gap-2 text-xs">
                  {heatmapBuckets.map((entry) => (
                    <div
                      key={entry.method}
                      className="rounded-2xl border border-white/5 bg-slate-950/60 p-3 text-slate-200"
                    >
                      <div className="flex items-center justify-between">
                        <span className="text-sm font-semibold text-white">
                          {entry.method}
                        </span>
                        <span className="text-xs text-slate-400">
                          {Object.values(entry.totals).reduce(
                            (sum, value) => sum + value,
                            0
                          )}{" "}
                          ops
                        </span>
                      </div>
                      <ResponsiveContainer width="100%" height={80}>
                        <BarChart
                          data={Object.entries(entry.totals).map(
                            ([hour, count]) => ({
                              hour: `${hour}:00`,
                              count,
                            })
                          )}
                        >
                          <CartesianGrid stroke="#1e293b" strokeDasharray="3 3" />
                          <XAxis dataKey="hour" tick={{ fill: "#94a3b8" }} />
                          <YAxis hide />
                          <Tooltip />
                          <Bar dataKey="count" fill="#7dd3fc" radius={[4, 4, 0, 0]} />
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
