"use client";

import type { Route } from "next";
import Link from "next/link";
import {
  BoltIcon,
  ChartBarIcon,
  SparklesIcon,
  Squares2X2Icon,
  UsersIcon,
} from "@heroicons/react/24/outline";
import {
  useApplyPartnerAutomationRecipe,
  usePartnerAutomationRecipes,
  usePartnerDifferentiatorInsights,
  usePartnerExperienceTemplates,
  usePartnerPublicHub,
  usePartnerTeamStructure,
} from "@/hooks/usePartnerServers";
import { Skeleton } from "@/components/ui/Skeleton";

function accentClasses(accent?: string) {
  if (accent === "emerald") {
    return "border-emerald-500/20 bg-emerald-500/10";
  }
  if (accent === "amber") {
    return "border-amber-500/20 bg-amber-500/10";
  }
  if (accent === "rose") {
    return "border-rose-500/20 bg-rose-500/10";
  }
  return "border-cyan-500/20 bg-cyan-500/10";
}

export function PartnerDifferentiatorHub({ partnerId }: { partnerId: string }) {
  const hubQuery = usePartnerPublicHub(partnerId);
  const insightsQuery = usePartnerDifferentiatorInsights(partnerId);
  const teamQuery = usePartnerTeamStructure(partnerId);
  const recipesQuery = usePartnerAutomationRecipes(partnerId);
  const templatesQuery = usePartnerExperienceTemplates(partnerId);
  const applyRecipe = useApplyPartnerAutomationRecipe(partnerId);

  if (
    hubQuery.isLoading ||
    insightsQuery.isLoading ||
    teamQuery.isLoading ||
    recipesQuery.isLoading ||
    templatesQuery.isLoading
  ) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-64 rounded-[2rem]" />
        <div className="grid gap-4 lg:grid-cols-2">
          <Skeleton className="h-72 rounded-[2rem]" />
          <Skeleton className="h-72 rounded-[2rem]" />
        </div>
      </div>
    );
  }

  if (
    hubQuery.isError ||
    insightsQuery.isError ||
    teamQuery.isError ||
    recipesQuery.isError ||
    templatesQuery.isError ||
    !hubQuery.data ||
    !insightsQuery.data ||
    !teamQuery.data
  ) {
    return (
      <div className="glass-card rounded-[2rem] border border-rose-500/20 bg-rose-950/20 p-8">
        <p className="text-sm uppercase tracking-[0.3em] text-rose-300">Phase 5 Hub</p>
        <h1 className="mt-3 text-3xl font-semibold text-white">Differentiator hub could not load</h1>
      </div>
    );
  }

  const hub = hubQuery.data;
  const insights = insightsQuery.data;
  const team = teamQuery.data;
  const recipes = recipesQuery.data ?? [];
  const templates = templatesQuery.data ?? [];
  const profile = hub.profile as Record<string, unknown>;
  const brandColors = (profile.brand_colors as string[] | undefined) ?? [];

  return (
    <div className="space-y-4">
      <section className="overflow-hidden rounded-[2rem] border border-slate-800 bg-[radial-gradient(circle_at_top_left,rgba(34,211,238,0.22),rgba(2,6,23,0.98)_48%)]">
        <div className="grid gap-6 px-8 py-8 lg:grid-cols-[minmax(0,1.25fr)_360px]">
          <div>
            <p className="text-[11px] uppercase tracking-[0.35em] text-cyan-300/75">Phase 5 Hub</p>
            <h1 className="mt-3 text-4xl font-semibold text-white">{hub.name}</h1>
            <p className="mt-4 max-w-3xl text-sm text-slate-200">
              {String(profile.tagline || hub.description || "Branded partner hub, analytics, automation, and templates that push this server beyond a standard Discord workspace.")}
            </p>
            <div className="mt-6 flex flex-wrap gap-3">
              {brandColors.length > 0 ? brandColors.slice(0, 4).map((color) => (
                <span key={color} className="rounded-full border border-slate-700 px-3 py-1 text-xs text-slate-200">
                  {color}
                </span>
              )) : (
                <span className="rounded-full border border-slate-700 px-3 py-1 text-xs text-slate-200">Brand palette not configured</span>
              )}
              <Link
                href={`/partners/${partnerId}` as Route}
                className="rounded-full border border-cyan-500/30 bg-cyan-500/10 px-4 py-1.5 text-xs uppercase tracking-[0.25em] text-cyan-200"
              >
                Back to server
              </Link>
            </div>
          </div>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-1">
            <div className="rounded-[1.6rem] border border-slate-800 bg-slate-950/65 p-4">
              <p className="text-xs uppercase tracking-[0.25em] text-slate-500">Public metrics</p>
              <div className="mt-3 grid grid-cols-2 gap-3 text-sm text-white">
                <div>
                  <p className="text-2xl font-semibold">{hub.public_metrics.active_members}</p>
                  <p className="text-slate-500">members</p>
                </div>
                <div>
                  <p className="text-2xl font-semibold">{hub.public_metrics.channels}</p>
                  <p className="text-slate-500">channels</p>
                </div>
                <div>
                  <p className="text-2xl font-semibold">{hub.public_metrics.apps}</p>
                  <p className="text-slate-500">apps</p>
                </div>
                <div>
                  <p className="text-2xl font-semibold">{hub.public_metrics.categories}</p>
                  <p className="text-slate-500">categories</p>
                </div>
              </div>
            </div>
            <div className="rounded-[1.6rem] border border-slate-800 bg-slate-950/65 p-4">
              <p className="text-xs uppercase tracking-[0.25em] text-slate-500">Landing builder</p>
              <p className="mt-3 text-sm text-slate-300">
                {Object.keys(hub.landing_builder || {}).length > 0
                  ? "Landing builder configuration is present and ready for a public partner hub surface."
                  : "Landing builder is still minimal. Phase 5 hub will still render from the organization profile."}
              </p>
            </div>
          </div>
        </div>
      </section>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.2fr)_360px]">
        <div className="space-y-4">
          <section className="rounded-[2rem] border border-slate-800 bg-slate-950/80 p-6">
            <div className="flex items-center gap-3">
              <ChartBarIcon className="h-5 w-5 text-cyan-300" />
              <div>
                <p className="text-xs uppercase tracking-[0.25em] text-slate-500">Differentiator insights</p>
                <p className="text-sm text-slate-300">Analytics that go beyond basic server counts.</p>
              </div>
            </div>
            <div className="mt-5 grid gap-3 md:grid-cols-2">
              <div className="rounded-[1.4rem] border border-slate-800 bg-slate-900/70 p-4">
                <p className="text-xs uppercase tracking-[0.25em] text-slate-500">Onboarding funnel</p>
                <p className="mt-3 text-2xl font-semibold text-white">{insights.onboarding_funnel.completion_rate}%</p>
                <p className="mt-2 text-sm text-slate-400">
                  {insights.onboarding_funnel.completed}/{insights.onboarding_funnel.started} completed
                </p>
              </div>
              <div className="rounded-[1.4rem] border border-slate-800 bg-slate-900/70 p-4">
                <p className="text-xs uppercase tracking-[0.25em] text-slate-500">Team activation</p>
                <p className="mt-3 text-2xl font-semibold text-white">{insights.team_activation.assignment_rate}%</p>
                <p className="mt-2 text-sm text-slate-400">
                  {insights.team_activation.assigned_members}/{insights.team_activation.active_members} active members assigned
                </p>
              </div>
            </div>
            <div className="mt-5 grid gap-4 lg:grid-cols-2">
              <div className="rounded-[1.4rem] border border-slate-800 bg-slate-900/70 p-4">
                <p className="text-xs uppercase tracking-[0.25em] text-slate-500">Role health</p>
                <div className="mt-3 space-y-2">
                  {insights.role_health.map((role) => (
                    <div key={role.role} className="flex items-center justify-between text-sm text-slate-200">
                      <span>{role.role}</span>
                      <span>{role.count}</span>
                    </div>
                  ))}
                </div>
              </div>
              <div className="rounded-[1.4rem] border border-slate-800 bg-slate-900/70 p-4">
                <p className="text-xs uppercase tracking-[0.25em] text-slate-500">App adoption</p>
                <div className="mt-3 space-y-2">
                  {insights.app_adoption.map((app) => (
                    <div key={app.app} className="flex items-center justify-between text-sm text-slate-200">
                      <span>{app.app}</span>
                      <span>{app.count}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </section>

          <section className="rounded-[2rem] border border-slate-800 bg-slate-950/80 p-6">
            <div className="flex items-center gap-3">
              <UsersIcon className="h-5 w-5 text-emerald-300" />
              <div>
                <p className="text-xs uppercase tracking-[0.25em] text-slate-500">Team structure</p>
                <p className="text-sm text-slate-300">Role lanes for better organization administration.</p>
              </div>
            </div>
            <div className="mt-5 grid gap-3 md:grid-cols-2">
              {team.lanes.map((lane) => (
                <div key={lane.key} className="rounded-[1.4rem] border border-slate-800 bg-slate-900/70 p-4">
                  <p className="text-xs uppercase tracking-[0.25em] text-slate-500">{lane.key}</p>
                  <div className="mt-3 space-y-2">
                    {lane.members.slice(0, 5).map((member) => (
                      <div key={member.user_id} className="rounded-xl border border-slate-800 bg-slate-950/70 px-3 py-2">
                        <p className="text-sm font-medium text-white">{member.display_name || member.user_id}</p>
                        <p className="mt-1 text-xs text-slate-400">{member.status}</p>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </section>
        </div>

        <div className="space-y-4">
          <section className="rounded-[2rem] border border-slate-800 bg-slate-950/80 p-6">
            <div className="flex items-center gap-3">
              <BoltIcon className="h-5 w-5 text-amber-300" />
              <div>
                <p className="text-xs uppercase tracking-[0.25em] text-slate-500">Automation recipes</p>
                <p className="text-sm text-slate-300">One-click admin recipes built on the existing automation engine.</p>
              </div>
            </div>
            <div className="mt-4 space-y-3">
              {recipes.map((recipe) => (
                <div key={recipe.key} className="rounded-[1.4rem] border border-slate-800 bg-slate-900/70 p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="text-sm font-medium text-white">{recipe.title}</p>
                      <p className="mt-2 text-xs text-slate-400">{recipe.description}</p>
                    </div>
                    <span className="rounded-full border border-slate-800 px-2 py-1 text-[10px] text-slate-300">
                      {recipe.fits ? "fit" : "generic"}
                    </span>
                  </div>
                  <button
                    onClick={() => void applyRecipe.mutateAsync(recipe.key)}
                    disabled={applyRecipe.isPending}
                    className="mt-4 rounded-xl bg-amber-400 px-3 py-2 text-sm font-medium text-slate-950 transition hover:bg-amber-300 disabled:opacity-60"
                  >
                    Apply recipe
                  </button>
                </div>
              ))}
            </div>
          </section>

          <section className="rounded-[2rem] border border-slate-800 bg-slate-950/80 p-6">
            <div className="flex items-center gap-3">
              <SparklesIcon className="h-5 w-5 text-cyan-300" />
              <div>
                <p className="text-xs uppercase tracking-[0.25em] text-slate-500">Experience templates</p>
                <p className="text-sm text-slate-300">Branded server directions based on active apps and partner identity.</p>
              </div>
            </div>
            <div className="mt-4 space-y-3">
              {templates.map((template) => (
                <div key={template.key} className={`rounded-[1.4rem] border p-4 ${accentClasses(template.accent)}`}>
                  <div className="flex items-center justify-between gap-3">
                    <p className="text-sm font-medium text-white">{template.title}</p>
                    <span className="rounded-full border border-white/10 px-2 py-1 text-[10px] text-slate-100">
                      {template.fits ? "recommended" : "available"}
                    </span>
                  </div>
                  <p className="mt-2 text-xs text-slate-200">{template.description}</p>
                </div>
              ))}
            </div>
          </section>

          <section className="rounded-[2rem] border border-slate-800 bg-slate-950/80 p-6">
            <div className="flex items-center gap-3">
              <Squares2X2Icon className="h-5 w-5 text-rose-300" />
              <div>
                <p className="text-xs uppercase tracking-[0.25em] text-slate-500">Connected apps</p>
                <p className="text-sm text-slate-300">Server modules that make the partner experience multi-domain, not chat-only.</p>
              </div>
            </div>
            <div className="mt-4 space-y-3">
              {hub.apps.map((app) => (
                <div key={app.id} className="rounded-[1.4rem] border border-slate-800 bg-slate-900/70 p-4">
                  <p className="text-sm font-medium text-white">{app.name}</p>
                  <p className="mt-2 text-xs text-slate-400">{app.description || app.module || app.type}</p>
                </div>
              ))}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
