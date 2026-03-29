"use client";

import { useQuery } from "@tanstack/react-query";
import { fetchPerformanceInsights } from "@/lib/api";

export function usePerformanceInsights() {
  return useQuery({
    queryKey: ["monitoring", "performance"],
    queryFn: fetchPerformanceInsights,
    staleTime: 20_000,
    refetchInterval: 60_000,
    retry: 1,
    cacheTime: 2 * 60_000,
  });
}
