"use client";

import { useMemo, useState } from "react";
import { useActivityStream, ActivityFilters } from "@/hooks/useActivityStream";
import { Skeleton } from "@/components/ui/Skeleton";

const viewModes = ["list", "timeline"] as const;

type ViewMode = (typeof viewModes)[number];

export function ActivityFeed() {
  const [filters, setFilters] = useState<ActivityFilters>({});
  const [viewMode, setViewMode] = useState<ViewMode>("list");
  const [selectedEntryId, setSelectedEntryId] = useState<number | null>(null);
  const streamQuery = useActivityStream(filters);
  const pages = streamQuery.data?.pages ?? [];
  const entries = useMemo(() => pages.flatMap((page) => page.stream ?? []), [pages]);

  const timeline = useMemo(() => {
    const grouped: Record<string, typeof entries> = {};
    entries.forEach((entry) => {
      const day = new Date(entry.created_at).toLocaleDateString();
      grouped[day] = grouped[day] ?? [];
      grouped[day].push(entry);
    });
    return Object.entries(grouped).map(([day, items]) => ({ day, items }));
  }, [entries]);

  const selectedEntry = entries.find((entry) => entry.id === selectedEntryId) ?? entries[0];
  const userProfileId = selectedEntry?.actor_id ?? null;
  const userProfile = useMemo(() => {
    if (!userProfileId) {
      return null;
    }
    const userHistory = entries.filter((entry) => entry.actor_id === userProfileId);
    return {
      userId: userProfileId,
      totalActions: userHistory.length,
      lastEndpoint: userHistory[0]?.path,
      lastAction: userHistory[0]?.method,
    };
  }, [entries, userProfileId]);

  const handleFilterChange = (key: keyof ActivityFilters, value: string) => {
    setFilters((prev) => {
      const next = { ...prev };
      if (!value) {
        delete next[key];
      } else {
        next[key] = value;
      }
      return next;
    });
  };

  return (
    <div className="glass-card rounded-3xl border border-white/5 bg-slate-900/70 p-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <p className="text-sm text-slate-400">Immutable audit stream</p>
          <h2 className="text-2xl font-semibold text-white">Activity Logs</h2>
        </div>
        <div className="flex items-center gap-2 text-xs uppercase tracking-widest text-slate-400">
          {viewModes.map((mode) => (
            <button
              key={mode}
              onClick={() => setViewMode(mode)}
              className={`rounded-full px-4 py-2 transition ${
                viewMode === mode ? "bg-indigo-500/30 text-white" : "bg-slate-900/60 text-slate-400"
              }`}
            >
              {mode}
            </button>
          ))}
        </div>
      </div>

      <div className="mt-5 grid gap-3 md:grid-cols-2">
        <div className="space-y-3">
          <div className="flex flex-wrap gap-2">
            <input
              placeholder="User ID"
              type="text"
              value={filters.actor_id ?? ""}
              onChange={(event) => handleFilterChange("actor_id", event.target.value)}
              className="flex-1 rounded-2xl border border-slate-800 bg-slate-900/70 px-4 py-2 text-sm text-white"
            />
            <input
              placeholder="Endpoint"
              value={filters.endpoint ?? ""}
              onChange={(event) => handleFilterChange("endpoint", event.target.value)}
              className="flex-1 rounded-2xl border border-slate-800 bg-slate-900/70 px-4 py-2 text-sm text-white"
            />
            <input
              placeholder="IP"
              value={filters.ip_address ?? ""}
              onChange={(event) => handleFilterChange("ip_address", event.target.value)}
              className="rounded-2xl border border-slate-800 bg-slate-900/70 px-4 py-2 text-sm text-white"
            />
          </div>
          <div className="flex flex-wrap gap-2">
            <input
              placeholder="Method"
              value={filters.method ?? ""}
              onChange={(event) => handleFilterChange("method", event.target.value)}
              className="rounded-2xl border border-slate-800 bg-slate-900/70 px-4 py-2 text-sm text-white"
            />
            <input
              placeholder="Status (e.g. 200)"
              value={filters.status_code ?? ""}
              onChange={(event) => handleFilterChange("status_code", event.target.value)}
              className="rounded-2xl border border-slate-800 bg-slate-900/70 px-4 py-2 text-sm text-white"
            />
          </div>
        </div>
        <div className="space-y-3">
          <div className="rounded-2xl border border-white/5 bg-slate-950/60 p-4">
            <p className="text-xs uppercase tracking-widest text-slate-500">User profile</p>
            {userProfile ? (
              <div className="space-y-1">
                <p className="text-sm text-white">User {userProfile.userId}</p>
                <p className="text-xs text-slate-400">Actions: {userProfile.totalActions}</p>
                <p className="text-xs text-slate-400">Last endpoint: {userProfile.lastEndpoint}</p>
                <p className="text-xs text-slate-400">Last verb: {userProfile.lastAction}</p>
              </div>
            ) : (
              <p className="text-xs text-slate-500">Select a row to inspect</p>
            )}
          </div>
          <div className="rounded-2xl border border-white/5 bg-slate-950/60 p-4">
            <p className="text-xs uppercase tracking-widest text-slate-500">Diff viewer</p>
            <div className="mt-2 max-h-32 overflow-y-auto rounded-xl bg-slate-900/80 p-3 text-xs text-slate-300">
              <pre>
                {selectedEntry
                  ? JSON.stringify(selectedEntry.metadata ?? { detail: "No metadata" }, null, 2)
                  : "Select an entry for metadata"}
              </pre>
            </div>
          </div>
        </div>
      </div>

      <div className="mt-5 space-y-4">
        {streamQuery.isLoading ? (
          <Skeleton className="h-40" />
        ) : streamQuery.isError ? (
          <p className="text-sm text-rose-400">Unable to load activity stream.</p>
        ) : viewMode === "timeline" ? (
          timeline.map((segment) => (
            <div key={segment.day} className="rounded-2xl border border-white/5 bg-slate-950/70 p-4">
              <p className="text-xs uppercase tracking-widest text-slate-500">{segment.day}</p>
              <div className="mt-2 space-y-2">
                {segment.items.map((entry) => {
                  const isSuspicious = entry.status_code >= 400;
                  return (
                    <div
                      key={entry.id}
                      className={`flex items-center justify-between rounded-2xl border border-white/5 p-3 text-xs text-slate-200 ${
                        isSuspicious ? "border-rose-500/60 bg-rose-950/40" : "bg-slate-950/60"
                      }`}
                      onClick={() => setSelectedEntryId(entry.id)}
                    >
                      <div>
                        <p className="text-sm font-semibold text-white">{entry.method}</p>
                        <p className="text-xs text-slate-400">{entry.path}</p>
                      </div>
                      <span className="text-xs text-slate-300">{entry.status_code}</span>
                    </div>
                  );
                })}
              </div>
            </div>
          ))
        ) : (
          <div className="space-y-3">
            {entries.map((entry) => {
              const isSuspicious = entry.status_code >= 400;
              return (
                <div
                  key={entry.id}
                  className={`flex flex-col gap-1 rounded-2xl border border-white/5 bg-slate-950/60 p-4 text-sm ${
                    isSuspicious ? "border-rose-500/60 bg-rose-950/40" : "bg-slate-950/60"
                  }`}
                  onClick={() => setSelectedEntryId(entry.id)}
                >
                  <div className="flex items-center justify-between">
                    <p className="font-semibold text-white">User {entry.actor_id}</p>
                    <span className="text-xs text-slate-400">{entry.created_at}</span>
                  </div>
                  <p className="text-xs text-slate-400">{entry.path}</p>
                  <div className="flex items-center gap-2 text-xs uppercase tracking-widest">
                    <span>{entry.method}</span>
                    <span className="text-slate-500">{entry.status_code}</span>
                    <span className={isSuspicious ? "text-rose-400" : "text-emerald-400"}>
                      {isSuspicious ? "Suspicious" : "Clean"}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      <div className="mt-5 flex flex-wrap items-center justify-between gap-3">
        <button
          disabled={!streamQuery.hasNextPage}
          onClick={() => streamQuery.fetchNextPage()}
          className="rounded-full border border-slate-800 px-4 py-2 text-xs uppercase tracking-widest text-slate-400 disabled:opacity-40"
        >
          {streamQuery.isFetchingNextPage ? "Loading…" : "Load more"}
        </button>
        <span className="text-xs text-slate-500">
          Showing {entries.length} entries{streamQuery.isFetching ? " • updating…" : ""}
        </span>
      </div>
    </div>
  );
}
