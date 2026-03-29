"use client";

import { BellIcon, MagnifyingGlassIcon, SparklesIcon } from "@heroicons/react/24/outline";
import { ThemeToggleButton } from "@/components/ui/ThemeToggle";

export function TopNav() {
  return (
    <header className="glass-card sticky top-0 z-20 m-4 flex items-center justify-between gap-4 border border-slate-800 bg-slate-950/60 px-6 py-4 shadow-lg backdrop-blur-xl">
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2 rounded-2xl border border-indigo-500/20 bg-slate-900/70 px-3 py-2 text-sm text-slate-400">
          <MagnifyingGlassIcon className="h-4 w-4 text-slate-400" />
          <input
            type="search"
            placeholder="Search metrics, tables, users..."
            className="w-64 bg-transparent text-sm placeholder:text-slate-500 focus:outline-none"
          />
        </div>
      </div>
      <div className="flex items-center gap-3">
        <div className="text-slate-400">
          <BellIcon className="h-5 w-5" />
        </div>
        <div className="h-10 w-px bg-slate-800" />
        <div className="flex items-center gap-2 rounded-2xl border border-slate-800 bg-slate-900/70 px-4 py-2 text-sm text-slate-300">
          <SparklesIcon className="h-4 w-4 text-amber-400" />
          <span>Live</span>
        </div>
        <ThemeToggleButton />
        <div className="rounded-2xl border border-slate-800 bg-slate-900/70 px-4 py-2 text-sm text-slate-200">
          nigel@isa
        </div>
      </div>
    </header>
  );
}
