"use client";

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchModelData, ModelListParams } from "@/lib/api";

export function useModelData(
  app_label: string,
  model_name: string,
  params: ModelListParams = {},
  enabled = true
) {
  const serializedParams = useMemo(() => JSON.stringify(params), [params]);

  return useQuery({
    queryKey: ["crud", "data", app_label, model_name, serializedParams],
    queryFn: () => fetchModelData(app_label, model_name, params),
    keepPreviousData: true,
    enabled: Boolean(app_label && model_name && enabled),
    refetchOnWindowFocus: false,
    staleTime: 10_000,
    cacheTime: 30_000,
    retry: 1,
  });
}
