"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchLiveMetrics } from "@/lib/api";
import { useWebsocket } from "@/hooks/useWebsocket";

export type LiveWidget = {
  value: number | string;
  label?: string;
  aux?: Record<string, number>;
};

export function useRealtimeCounters() {
  const { data, isLoading } = useQuery({
    queryKey: ["dashboard", "live"],
    queryFn: fetchLiveMetrics,
    staleTime: 5_000,
    cacheTime: 20_000,
    refetchInterval: 15_000,
    retry: 1,
  });

  const [socketState, setSocketState] = useState<any>(null);

  useWebsocket((payload) => {
    if (!payload) {
      return;
    }
    if (payload.dashboard) {
      setSocketState(payload.dashboard);
    } else if (payload.widgets) {
      setSocketState(payload);
    }
  });

  const sourceWidgets = socketState ?? data?.widgets ?? {};
  const sourceGraphs = socketState?.graphs ?? data?.graphs ?? {};

  return useMemo(
    () => ({
      isLoading,
      widgets: sourceWidgets,
      graphs: sourceGraphs,
      counters: {
        activeUsers: Number(sourceWidgets?.active_users?.value ?? 0),
        requestsPerMinute: Number(
          sourceWidgets?.requests_per_minute?.value ?? 0
        ),
        revenue: Number(sourceWidgets?.revenue?.value ?? 0),
        errors: (() => {
          const graphValue = Number(sourceGraphs?.error_rate?.[0]?.value ?? 0);
          const widgetValue = Number(sourceWidgets?.error_rate?.value ?? 0);
          if (Number.isFinite(graphValue) && graphValue >= 0) {
            return graphValue;
          }
          if (Number.isFinite(widgetValue) && widgetValue >= 0) {
            return widgetValue;
          }
          return 0;
        })(),
      },
    }),
    [isLoading, sourceWidgets, sourceGraphs]
  );
}
