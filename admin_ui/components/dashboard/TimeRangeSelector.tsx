"use client";

import { TimeRangeOption, useTimeRangeStore } from "@/hooks/useTimeRangeStore";

const rangeLabels: Record<TimeRangeOption, string> = {
  "1h": "1h",
  "24h": "24h",
  "7d": "7d",
  "30d": "30d",
  custom: "Custom",
};

export function TimeRangeSelector() {
  const { range, setRange } = useTimeRangeStore();

  return (
    <div className="flex flex-wrap items-center gap-2 text-xs uppercase tracking-widest text-slate-400">
      <span className="text-[10px] text-slate-500">Time range:</span>
      {Object.entries(rangeLabels).map(([value, label]) => {
        const option = value as TimeRangeOption;
        return (
          <button
            key={option}
            onClick={() => setRange(option)}
            className={`rounded-full px-3 py-1 transition ${
              range === option
                ? "bg-indigo-500/40 text-white"
                : "bg-slate-900/80 text-slate-400 hover:bg-slate-800/70"
            }`}
          >
            {label}
          </button>
        );
      })}
    </div>
  );
}
