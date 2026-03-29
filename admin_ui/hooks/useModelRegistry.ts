"use client";

import { useQuery } from "@tanstack/react-query";
import { fetchModelRegistry } from "@/lib/api";

export function useModelRegistry() {
  return useQuery({
    queryKey: ["crud", "registry"],
    queryFn: fetchModelRegistry,
    staleTime: 30_000,
    cacheTime: 2 * 60_000,
    refetchInterval: 60_000,
    retry: 1,
  });
}
