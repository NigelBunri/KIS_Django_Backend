"use client";

import { useTheme } from "next-themes";
import { SunIcon, MoonIcon } from "@heroicons/react/24/outline";

export function ThemeToggleButton() {
  const { theme, setTheme } = useTheme();

  return (
    <button
      aria-label="Toggle theme"
      className="rounded-full border border-slate-800 p-2 text-slate-300 transition hover:bg-slate-900/60"
      onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
    >
      {theme === "dark" ? <SunIcon className="h-5 w-5" /> : <MoonIcon className="h-5 w-5" />}
    </button>
  );
}

export function ThemeSelector() {
  return (
    <div className="mt-8 flex items-center justify-between rounded-2xl border border-slate-800 px-4 py-3">
      <div>
        <p className="text-xs uppercase tracking-wide text-slate-500">Theme</p>
        <p className="text-sm text-white">Dark / Light</p>
      </div>
      <ThemeToggleButton />
    </div>
  );
}
