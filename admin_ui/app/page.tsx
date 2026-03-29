"use client";

import { useMemo } from "react";
import { StatsGrid } from "@/components/dashboard/StatsGrid";
import { ChartsBoard } from "@/components/dashboard/ChartsBoard";
import { RealtimeCounters } from "@/components/dashboard/RealtimeCounters";
import { QueryProvider } from "@/components/providers/QueryProvider";
import { useDashboardData } from "@/hooks/useDashboardData";
import { useMicroAnalytics } from "@/hooks/useMicroAnalytics";
import { useRealtimeCounters } from "@/hooks/useRealtimeCounters";
import { TimeRangeSelector } from "@/components/dashboard/TimeRangeSelector";
import { useTimeRangeStore } from "@/hooks/useTimeRangeStore";
import { ErrorBoundary } from "@/components/ui/ErrorBoundary";
import { Skeleton } from "@/components/ui/Skeleton";

function DashboardContent() {
  const { data, isLoading } = useDashboardData();
  const microQuery = useMicroAnalytics();
  const liveMetrics = useRealtimeCounters();
  const timeRange = useTimeRangeStore((state) => state.range);

  const stats = useMemo(() => {
    if (!data) {
      return [];
    }
    const activeWidget = data.widgets?.active_users;
    const rpmWidget = data.widgets?.requests_per_minute;
    const revenueWidget = data.widgets?.revenue;
    const errorValue = data.graphs?.error_rate?.[0]?.value ?? 0;
    const dbGrowth = data.database_growth ?? {};
    const dbTotal = Object.values(dbGrowth).reduce((sum, value) => sum + (value as number), 0);
    const health = data.system_health?.status ?? "pending";
    return [
      {
        label: "Active users",
        value: activeWidget?.value ?? "—",
        delta: activeWidget?.aux
          ? `7d ${activeWidget.aux["7d"] ?? 0} • 30d ${activeWidget.aux["30d"] ?? 0}`
          : undefined,
      },
      {
        label: "Requests / min",
        value: rpmWidget?.value ?? "—",
      },
      {
        label: "Error rate",
        value: `${errorValue}%`,
      },
      {
        label: "Revenue (USD)",
        value: revenueWidget?.value ? `$${revenueWidget.value}` : "—",
      },
      {
        label: "System health",
        value: health,
      },
      {
        label: "DB size",
        value: dbTotal ? dbTotal.toLocaleString() : "—",
        detail: "total rows",
      },
    ];
  }, [data]);

  return (
    <QueryProvider>
      <ErrorBoundary>
        <div className="space-y-6">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div>
              <p className="text-sm text-slate-400">Overview</p>
              <h1 className="text-3xl font-semibold text-white">Global Dashboard</h1>
            </div>
            <TimeRangeSelector />
          </div>
          {isLoading ? (
            <Skeleton className="h-40 w-full" />
          ) : (
            <StatsGrid stats={stats} />
          )}
          <RealtimeCounters
            activeUsers={liveMetrics.counters.activeUsers}
            requestsPerMinute={liveMetrics.counters.requestsPerMinute}
            errors={liveMetrics.counters.errors}
            revenue={liveMetrics.counters.revenue as number}
            loading={liveMetrics.isLoading}
          />
          <ChartsBoard
            graphs={data?.graphs}
            microApps={microQuery.data}
            liveActivity={data?.live_activity}
            timeRange={timeRange}
          />
        </div>
      </ErrorBoundary>
    </QueryProvider>
  );
}

export default function DashboardPage() {
  return <DashboardContent />;
}
