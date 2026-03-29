"use client";

import { useQuery } from "@tanstack/react-query";
import { fetchMonitoringAlerts } from "@/lib/api";

export function useMonitoringAlerts() {
  return useQuery({
    queryKey: ["monitoring", "alerts"],
    queryFn: fetchMonitoringAlerts,
    staleTime: 30_000,
    refetchInterval: 60_000,
    retry: 1,
    cacheTime: 2 * 60_000,
  });
}
