"use client";

import { useQuery } from "@tanstack/react-query";
import { fetchDashboardOverview } from "@/lib/api";

export function useDashboardData() {
  return useQuery({
    queryKey: ["dashboard", "overview"],
    queryFn: fetchDashboardOverview,
    staleTime: 15_000,
    cacheTime: 45_000,
    refetchInterval: 30_000,
    retry: 1,
  });
}
