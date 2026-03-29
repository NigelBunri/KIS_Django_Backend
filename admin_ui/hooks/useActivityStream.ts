"use client";

import { useMemo } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { fetchActivityFeed } from "@/lib/api";

export type ActivityFilters = {
  actor_id?: string;
  endpoint?: string;
  ip_address?: string;
  method?: string;
  app?: string;
  status_code?: string;
};

export function useActivityStream(filters: ActivityFilters = {}) {
  const normalizedFilters = useMemo(() => {
    return Object.entries(filters).reduce<Record<string, string>>((acc, [key, value]) => {
      if (value) {
        acc[key] = value;
      }
      return acc;
    }, {});
  }, [filters]);

  return useInfiniteQuery({
    queryKey: ["activity", "stream", normalizedFilters],
    queryFn: ({ pageParam = 1 }) =>
      fetchActivityFeed({ ...normalizedFilters, page: pageParam }),
    getNextPageParam: (lastPage) => {
      if (lastPage.pagination.page < lastPage.pagination.total_pages) {
        return lastPage.pagination.page + 1;
      }
      return undefined;
    },
    refetchOnWindowFocus: false,
    staleTime: 30_000,
    cacheTime: 2 * 60_000,
  });
}
