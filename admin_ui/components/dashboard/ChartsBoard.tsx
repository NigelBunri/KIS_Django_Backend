"use client";

import { useMemo } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  Cell,
} from "recharts";
import type { TimeRangeOption } from "@/hooks/useTimeRangeStore";

type TrendPoint = {
  label: string;
  value: number;
};

type LiveActivityEntry = {
  id: number;
  created_at: string;
  method: string;
};

type MicroAppInsight = {
  app_label: string;
  usage_frequency: number;
  conversion_rate: number;
  feature_adoption: number;
};

type ChartsBoardProps = {
  graphs?: Record<string, TrendPoint[]>;
  microApps?: MicroAppInsight[];
  liveActivity?: LiveActivityEntry[];
  timeRange: TimeRangeOption;
};

const getFilteredTrend = (trend: TrendPoint[], range: TimeRangeOption) => {
  if (!trend || !trend.length) return [];
  if (range === "custom" || range === "24h") {
    return trend;
  }
  return trend.filter((item) => item.label.toLowerCase().includes(range));
};

export function ChartsBoard({
  graphs = {},
  microApps = [],
  liveActivity = [],
  timeRange,
}: ChartsBoardProps) {
  const trafficData = useMemo(
    () => getFilteredTrend(graphs.active_user_trend ?? [], timeRange),
    [graphs.active_user_trend, timeRange]
  );
  const revenueData = useMemo(
    () => graphs.revenue_trend ?? [],
    [graphs.revenue_trend]
  );
  const usageData = useMemo(() => {
    return microApps
      .map((app) => ({
        name: app.app_label,
        value: app.usage_frequency,
      }))
      .sort((a, b) => b.value - a.value)
      .slice(0, 6);
  }, [microApps]);
  const adoptionData = useMemo(
    () =>
      microApps
        .map((app) => ({
          name: app.app_label,
          value: Number(app.feature_adoption ?? 0),
        }))
        .filter((entry) => entry.value > 0)
        .slice(0, 5),
    [microApps]
  );
  const heatmapTotals = useMemo(() => {
    const buckets = Array.from({ length: 24 }, (_, hour) => ({ hour, count: 0 }));
    liveActivity.forEach((item) => {
      const date = new Date(item.created_at);
      const hour = date.getHours();
      buckets[hour].count += 1;
    });
    return buckets;
  }, [liveActivity]);

  const maxHeatmapCount = Math.max(...heatmapTotals.map((item) => item.count), 1);

  return (
    <div className="grid gap-5 lg:grid-cols-2">
      <div className="glass-card rounded-3xl border border-white/5 bg-slate-900/70 p-5">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-slate-400">Traffic</h3>
          <p className="text-xs text-slate-500">Trend overview</p>
        </div>
        <div className="mt-4 h-60">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={trafficData}>
              <CartesianGrid stroke="#1e293b" strokeDasharray="3 3" />
              <XAxis dataKey="label" tick={{ fontSize: 11, fill: "#94a3b8" }} />
              <YAxis tick={{ fontSize: 11, fill: "#94a3b8" }} />
              <Tooltip />
              <Line
                type="monotone"
                dataKey="value"
                stroke="#8b5cf6"
                strokeWidth={3}
                dot={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>
      <div className="glass-card rounded-3xl border border-white/5 bg-slate-900/70 p-5">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-slate-400">Revenue</h3>
          <p className="text-xs text-slate-500">Rolling sum</p>
        </div>
        <div className="mt-4 h-60">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={revenueData}>
              <defs>
                <linearGradient id="revGradient" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#fcd34d" stopOpacity={0.8} />
                  <stop offset="95%" stopColor="#fcd34d" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid stroke="#1e293b" strokeDasharray="3 3" />
              <XAxis dataKey="label" tick={{ fontSize: 11, fill: "#94a3b8" }} />
              <YAxis tick={{ fontSize: 11, fill: "#94a3b8" }} />
              <Tooltip />
              <Area
                type="monotone"
                dataKey="value"
                stroke="#fcd34d"
                fill="url(#revGradient)"
                strokeWidth={3}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </div>
      <div className="glass-card rounded-3xl border border-white/5 bg-slate-900/70 p-5">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-slate-400">App usage</h3>
          <p className="text-xs text-slate-500">Top performers</p>
        </div>
        <div className="mt-4 h-56">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={usageData}>
              <CartesianGrid stroke="#1e293b" strokeDasharray="3 3" />
              <XAxis dataKey="name" tick={{ fontSize: 11, fill: "#94a3b8" }} />
              <YAxis tick={{ fontSize: 11, fill: "#94a3b8" }} />
              <Tooltip />
              <Bar dataKey="value" fill="#34d399" radius={[6, 6, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
      <div className="glass-card rounded-3xl border border-white/5 bg-slate-900/70 p-5">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-slate-400">Feature adoption</h3>
          <p className="text-xs text-slate-500">Based on micro apps</p>
        </div>
        <div className="mt-4 flex h-56 items-center justify-center">
          {adoptionData.length ? (
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={adoptionData}
                  dataKey="value"
                  nameKey="name"
                  innerRadius={60}
                  outerRadius={80}
                  stroke="none"
                >
                  {adoptionData.map((entry, index) => (
                    <Cell
                      key={entry.name}
                      fill={`hsl(${(index / adoptionData.length) * 360}, 70%, 60%)`}
                    />
                  ))}
                </Pie>
                <Tooltip />
                <Legend />
              </PieChart>
            </ResponsiveContainer>
          ) : (
            <p className="text-sm text-slate-500">No adoption data yet</p>
          )}
        </div>
      </div>
      <div className="glass-card rounded-3xl border border-white/5 bg-slate-900/70 p-5">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-slate-400">Activity heatmap</h3>
          <p className="text-xs text-slate-500">Realtime hourly</p>
        </div>
        <div className="mt-4 grid grid-cols-4 gap-2 text-xs">
          {heatmapTotals.map((entry) => {
            const intensity = entry.count / maxHeatmapCount;
            const opacity = 0.2 + intensity * 0.8;
            return (
              <div
                key={entry.hour}
                className="flex flex-col items-center justify-center rounded-2xl border border-white/5 p-3"
                style={{ background: `rgba(99, 102, 241, ${opacity})` }}
              >
                <span className="text-slate-200">{entry.hour}:00</span>
                <span className="text-sm font-semibold text-white">{entry.count}</span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
