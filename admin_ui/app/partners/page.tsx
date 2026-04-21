"use client";

import type { Route } from "next";
import Link from "next/link";
import { ArrowRightIcon, UserPlusIcon } from "@heroicons/react/24/outline";
import { QueryProvider } from "@/components/providers/QueryProvider";
import { Skeleton } from "@/components/ui/Skeleton";
import { usePartnerServers } from "@/hooks/usePartnerServers";

function PartnersIndexContent() {
  const partnersQuery = usePartnerServers();

  if (partnersQuery.isLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-40 w-full rounded-[2rem]" />
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 6 }).map((_, index) => (
            <Skeleton key={index} className="h-48 rounded-[2rem]" />
          ))}
        </div>
      </div>
    );
  }

  if (partnersQuery.isError) {
    return (
      <div className="glass-card rounded-[2rem] border border-rose-500/20 bg-rose-950/20 p-8">
        <p className="text-sm uppercase tracking-[0.3em] text-rose-300">Partner Servers</p>
        <h1 className="mt-3 text-3xl font-semibold text-white">Partner list could not load</h1>
        <p className="mt-4 text-sm text-slate-300">
          Check the platform API configuration and confirm the partner endpoints are reachable from `admin_ui`.
        </p>
      </div>
    );
  }

  const partners = partnersQuery.data ?? [];

  return (
    <div className="space-y-6">
      <section className="glass-card rounded-[2rem] border border-slate-800 bg-[radial-gradient(circle_at_top_left,#164e63_0%,rgba(15,23,42,0.92)_35%,rgba(2,6,23,1)_100%)] p-8">
        <p className="text-sm uppercase tracking-[0.35em] text-cyan-200/70">Phase 3</p>
        <h1 className="mt-3 max-w-3xl text-4xl font-semibold text-white">Partner servers now have a dedicated frontend shell.</h1>
        <p className="mt-4 max-w-2xl text-sm leading-6 text-slate-300">
          Open any partner below to enter the Discord-style server layout: server rail, permission-aware channel tree,
          app launcher, and member rail. Messaging stays separate until the message-system phase is wired in.
        </p>
        <div className="mt-6 flex flex-wrap gap-3">
          {partners[0] ? (
            <Link
              href={`/partners/${partners[0].id}` as Route}
              className="rounded-2xl bg-cyan-400 px-5 py-3 text-sm font-medium text-slate-950 transition hover:bg-cyan-300"
            >
              Open first server
            </Link>
          ) : null}
          <button className="rounded-2xl border border-slate-700 bg-slate-900/70 px-5 py-3 text-sm text-slate-200">
            <UserPlusIcon className="mr-2 inline h-4 w-4" />
            Invite flow entry
          </button>
        </div>
      </section>

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {partners.map((partner) => (
          <Link
            key={partner.id}
            href={`/partners/${partner.id}` as Route}
            className="glass-card group rounded-[2rem] border border-slate-800 bg-slate-950/70 p-5 transition hover:border-cyan-400/30"
          >
            <div className="flex items-start justify-between gap-4">
              <div className="flex h-14 w-14 items-center justify-center rounded-[1.5rem] bg-gradient-to-br from-cyan-500/30 to-blue-500/20 text-sm font-semibold text-white">
                {partner.name
                  .split(/\s+/)
                  .slice(0, 2)
                  .map((part) => part[0]?.toUpperCase() ?? "")
                  .join("")}
              </div>
              <span className="rounded-full border border-slate-800 px-3 py-1 text-[10px] uppercase tracking-[0.25em] text-slate-400">
                {partner.member_role || "member"}
              </span>
            </div>
            <h2 className="mt-5 text-xl font-semibold text-white">{partner.name}</h2>
            <p className="mt-2 text-sm text-slate-400">Open the permission-aware server shell and browse categories, channels, and apps.</p>
            <div className="mt-6 flex items-center justify-between text-sm text-slate-300">
              <span>{partner.slug}</span>
              <span className="inline-flex items-center gap-2 text-cyan-300">
                Open
                <ArrowRightIcon className="h-4 w-4 transition group-hover:translate-x-1" />
              </span>
            </div>
          </Link>
        ))}
      </section>
    </div>
  );
}

export default function PartnersIndexPage() {
  return (
    <QueryProvider>
      <PartnersIndexContent />
    </QueryProvider>
  );
}
