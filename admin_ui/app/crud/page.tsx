"use client";

import { QueryProvider } from "@/components/providers/QueryProvider";
import { CrudEngine } from "@/components/sections/CrudEngine";

function CrudPageContent() {
  return (
    <div className="space-y-6">
      <div className="flex justify-between">
        <div>
          <p className="text-sm text-slate-400">Universal CRUD</p>
          <h1 className="text-3xl font-semibold text-white">CRUD Engine</h1>
        </div>
      </div>
      <CrudEngine />
    </div>
  );
}

export default function CrudPage() {
  return (
    <QueryProvider>
      <CrudPageContent />
    </QueryProvider>
  );
}
