"use client";

import { create } from "zustand";

export type TimeRangeOption = "1h" | "24h" | "7d" | "30d" | "custom";

type TimeRangeState = {
  range: TimeRangeOption;
  setRange: (range: TimeRangeOption) => void;
};

export const useTimeRangeStore = create<TimeRangeState>((set) => ({
  range: "24h",
  setRange: (range: TimeRangeOption) => set({ range }),
}));
