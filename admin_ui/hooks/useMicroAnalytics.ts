"use client";

import { useQuery } from "@tanstack/react-query";
import { fetchMicroAnalytics } from "@/lib/api";

export function useMicroAnalytics() {
  return useQuery({
    queryKey: ["micro", "apps"],
    queryFn: fetchMicroAnalytics,
    staleTime: 45_000,
    cacheTime: 2 * 60_000,
    refetchInterval: 90_000,
    retry: 1,
  });
}
