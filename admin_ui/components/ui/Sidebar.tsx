"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { motion } from "framer-motion";
import {
  ArchiveBoxIcon,
  ChartPieIcon,
  ShieldCheckIcon,
  SparklesIcon,
  Squares2X2Icon,
  UserGroupIcon,
} from "@heroicons/react/24/outline";
import { ThemeSelector } from "@/components/ui/ThemeToggle";

const navItems = [
  { label: "Dashboard", href: "/", icon: ChartPieIcon },
  { label: "App Analytics", href: "/analytics", icon: SparklesIcon },
  { label: "Services", href: "/services", icon: Squares2X2Icon },
  { label: "CRUD Engine", href: "/crud", icon: ArchiveBoxIcon },
  { label: "Activity Logs", href: "/activity", icon: UserGroupIcon },
  { label: "RBAC(Role-Based Access Control)", href: "/rbac", icon: ShieldCheckIcon },
  { label: "Monitoring", href: "/monitoring", icon: ChartPieIcon },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="glass-card relative z-10 flex w-72 flex-col border-r border-slate-800 bg-slate-950/80 p-6 shadow-lg">
      <div className="mb-10">
        <div className="flex items-center gap-2 text-xl font-semibold tracking-tight text-white">
          <span className="h-10 w-10 rounded-2xl bg-gradient-to-br from-indigo-500 to-sky-500" />
          <span>Obs Control</span>
        </div>
        <p className="mt-2 text-sm text-slate-400">Live analytics & enterprise controls</p>
      </div>
      <nav className="flex-1 space-y-1">
        {navItems.map((item) => {
          const isActive = pathname === item.href;
          return (
            <Link key={item.label} href={item.href}>
              <motion.div
                initial={false}
                animate={{ x: isActive ? 6 : 0 }}
                className={`group flex items-center gap-3 rounded-2xl px-4 py-3 text-base transition ${
                  isActive
                    ? "bg-gradient-to-r from-indigo-500/20 to-sky-500/10 text-white"
                    : "text-slate-300 hover:text-white"
                }`}
              >
                <item.icon className="h-5 w-5" />
                {item.label}
              </motion.div>
            </Link>
          );
        })}
      </nav>
      <ThemeSelector />
    </aside>
  );
}
