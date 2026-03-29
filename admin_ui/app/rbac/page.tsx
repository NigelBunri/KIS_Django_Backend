"use client";

import { QueryProvider } from "@/components/providers/QueryProvider";
import { RbacMatrix } from "@/components/sections/RbacMatrix";

function RbacPageContent() {
  return (
    <div className="space-y-6">
      <div>
        <p className="text-sm text-slate-400">Permissions & roles</p>
        <h1 className="text-3xl font-semibold text-white">RBAC Console</h1>
      </div>
      <RbacMatrix />
    </div>
  );
}

export default function RbacPage() {
  return (
    <QueryProvider>
      <RbacPageContent />
    </QueryProvider>
  );
}
