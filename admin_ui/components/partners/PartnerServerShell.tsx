"use client";

import type { Route } from "next";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { startTransition, useMemo, useState } from "react";
import clsx from "clsx";
import {
  BellAlertIcon,
  BoltIcon,
  ChatBubbleLeftRightIcon,
  CheckBadgeIcon,
  Cog6ToothIcon,
  HashtagIcon,
  MegaphoneIcon,
  ShieldCheckIcon,
  SpeakerWaveIcon,
  UserPlusIcon,
  UsersIcon,
} from "@heroicons/react/24/outline";
import type {
  PartnerInvite,
  PartnerMemberDirectoryEntry,
  PartnerOrganizationApp,
  PartnerServerCategory,
  PartnerServerChannel,
  PartnerSummary,
} from "@/lib/api";
import {
  useCompletePartnerOnboarding,
  useCreatePartnerInvite,
  useDisablePartnerInvite,
  useModeratePartnerMember,
  usePartnerInvites,
  usePartnerMembers,
  usePartnerModerationActions,
  usePartnerNotificationPreferences,
  usePartnerOnboarding,
  usePartnerScreening,
  usePartnerServerShell,
  usePartnerServers,
  useUpdatePartnerNotificationPreferences,
  useUpdatePartnerScreening,
} from "@/hooks/usePartnerServers";
import { Skeleton } from "@/components/ui/Skeleton";

function channelIcon(channelType: PartnerServerChannel["channel_type"]) {
  if (channelType === "announcement") {
    return MegaphoneIcon;
  }
  if (channelType === "private") {
    return ShieldCheckIcon;
  }
  return HashtagIcon;
}

function initialsFor(name: string) {
  const words = name.split(/\s+/).filter(Boolean).slice(0, 2);
  return words.map((part) => part[0]?.toUpperCase() ?? "").join("") || "PS";
}

function roleLabel(role?: string | null) {
  if (!role) {
    return "Guest";
  }
  return role.replace(/_/g, " ");
}

function formatDateTime(value?: string | null) {
  if (!value) {
    return "Not set";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString();
}

function groupChannels(
  categories: PartnerServerCategory[],
  channels: PartnerServerChannel[]
): Array<{ key: string; title: string; isPrivate: boolean; channels: PartnerServerChannel[] }> {
  const byCategory = new Map<string, { key: string; title: string; isPrivate: boolean; channels: PartnerServerChannel[] }>();

  for (const category of categories) {
    byCategory.set(String(category.id), {
      key: String(category.id),
      title: category.name,
      isPrivate: category.is_private,
      channels: [],
    });
  }

  const uncategorized = {
    key: "uncategorized",
    title: "Lobby",
    isPrivate: false,
    channels: [] as PartnerServerChannel[],
  };

  for (const channel of channels) {
    const categoryKey = channel.category_id ?? (channel.category != null ? String(channel.category) : "");
    const bucket = byCategory.get(categoryKey);
    if (bucket) {
      bucket.channels.push(channel);
    } else {
      uncategorized.channels.push(channel);
    }
  }

  const grouped = Array.from(byCategory.values()).filter((group) => group.channels.length > 0);
  if (uncategorized.channels.length > 0) {
    grouped.unshift(uncategorized);
  }
  return grouped;
}

function appGroups(apps: PartnerOrganizationApp[]) {
  const groups = new Map<string, PartnerOrganizationApp[]>();
  for (const app of apps) {
    const key = app.group || "Workspace";
    if (!groups.has(key)) {
      groups.set(key, []);
    }
    groups.get(key)?.push(app);
  }
  return Array.from(groups.entries());
}

function memberGroupLabel(member: PartnerMemberDirectoryEntry) {
  if (member.is_banned) {
    return "Banned";
  }
  if (member.role_names.length > 0) {
    return member.role_names[0];
  }
  return roleLabel(member.membership_role);
}

function buildMemberGroups(members: PartnerMemberDirectoryEntry[]) {
  const groups = new Map<string, PartnerMemberDirectoryEntry[]>();
  for (const member of members) {
    const label = memberGroupLabel(member);
    if (!groups.has(label)) {
      groups.set(label, []);
    }
    groups.get(label)?.push(member);
  }
  return Array.from(groups.entries()).map(([title, items]) => ({ title, items }));
}

const notificationOptions = [
  { value: "all", label: "All activity" },
  { value: "mentions", label: "Mentions only" },
  { value: "none", label: "Muted" },
] as const;

export function PartnerServerShell({ partnerId }: { partnerId: string }) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const selectedChannelId = searchParams.get("channel");

  const [activePanel, setActivePanel] = useState<"invites" | "onboarding" | "screening" | "moderation" | null>(null);
  const [inviteLabel, setInviteLabel] = useState("");
  const [inviteRole, setInviteRole] = useState("member");
  const [inviteMaxUses, setInviteMaxUses] = useState("");
  const [inviteExpiresAt, setInviteExpiresAt] = useState("");
  const [screeningRules, setScreeningRules] = useState("");
  const [screeningEnabled, setScreeningEnabled] = useState(false);
  const [requireRulesAcceptance, setRequireRulesAcceptance] = useState(false);
  const [selectedOnboardingRoles, setSelectedOnboardingRoles] = useState<string[]>([]);
  const [selectedOnboardingChannels, setSelectedOnboardingChannels] = useState<string[]>([]);
  const [acceptRules, setAcceptRules] = useState(false);
  const [moderationReason, setModerationReason] = useState("");
  const [moderationTargetId, setModerationTargetId] = useState("");
  const [moderationAction, setModerationAction] = useState<"mute" | "unmute" | "timeout" | "kick" | "ban" | "unban">("mute");
  const [moderationExpiry, setModerationExpiry] = useState("");

  const serversQuery = usePartnerServers();
  const shellQuery = usePartnerServerShell(partnerId);
  const invitesQuery = usePartnerInvites(partnerId);
  const onboardingQuery = usePartnerOnboarding(partnerId);
  const membersQuery = usePartnerMembers(partnerId);
  const moderationQuery = usePartnerModerationActions(partnerId);
  const screeningQuery = usePartnerScreening(partnerId);
  const notificationsQuery = usePartnerNotificationPreferences(partnerId);

  const createInvite = useCreatePartnerInvite(partnerId);
  const disableInvite = useDisablePartnerInvite(partnerId);
  const completeOnboarding = useCompletePartnerOnboarding(partnerId);
  const moderateMember = useModeratePartnerMember(partnerId);
  const updateScreening = useUpdatePartnerScreening(partnerId);
  const updateNotifications = useUpdatePartnerNotificationPreferences(partnerId);

  const groupedChannels = useMemo(() => {
    if (!shellQuery.data) {
      return [];
    }
    return groupChannels(shellQuery.data.categories, shellQuery.data.channels);
  }, [shellQuery.data]);

  const selectedChannel = useMemo(() => {
    return shellQuery.data?.channels.find((channel) => channel.id === selectedChannelId) ?? shellQuery.data?.channels[0] ?? null;
  }, [selectedChannelId, shellQuery.data]);

  const groupedMembers = useMemo(() => buildMemberGroups(membersQuery.data ?? []), [membersQuery.data]);
  const groupedApps = useMemo(() => appGroups(shellQuery.data?.apps ?? []), [shellQuery.data?.apps]);

  const onboardingConfig = onboardingQuery.data?.config;
  const onboardingProgress = onboardingQuery.data?.progress;

  const selectedMember = (membersQuery.data ?? []).find((member) => member.user_id === moderationTargetId) ?? null;

  const selectChannel = (channelId: string) => {
    const nextParams = new URLSearchParams(searchParams.toString());
    nextParams.set("channel", channelId);
    startTransition(() => {
      router.replace(`${pathname}?${nextParams.toString()}` as never);
    });
  };

  const toggleOnboardingRole = (roleId: string) => {
    setSelectedOnboardingRoles((current) =>
      current.includes(roleId) ? current.filter((item) => item !== roleId) : [...current, roleId]
    );
  };

  const toggleOnboardingChannel = (channelId: string) => {
    setSelectedOnboardingChannels((current) =>
      current.includes(channelId) ? current.filter((item) => item !== channelId) : [...current, channelId]
    );
  };

  const submitInvite = async () => {
    await createInvite.mutateAsync({
      label: inviteLabel || undefined,
      membership_role: inviteRole,
      max_uses: inviteMaxUses ? Number(inviteMaxUses) : null,
      expires_at: inviteExpiresAt ? new Date(inviteExpiresAt).toISOString() : null,
    });
    setInviteLabel("");
    setInviteMaxUses("");
    setInviteExpiresAt("");
  };

  const submitOnboarding = async () => {
    await completeOnboarding.mutateAsync({
      accept_rules: acceptRules,
      role_ids: selectedOnboardingRoles,
      channel_ids: selectedOnboardingChannels,
    });
  };

  const saveScreening = async () => {
    await updateScreening.mutateAsync({
      enabled: screeningEnabled,
      require_rules_acceptance: requireRulesAcceptance,
      rules_text: screeningRules,
    });
  };

  const submitModeration = async () => {
    if (!moderationTargetId) {
      return;
    }
    await moderateMember.mutateAsync({
      memberUserId: moderationTargetId,
      action: moderationAction,
      reason: moderationReason || undefined,
      expires_at: moderationExpiry ? new Date(moderationExpiry).toISOString() : null,
    });
    setModerationReason("");
    setModerationExpiry("");
  };

  const updateServerNotification = async (value: "all" | "mentions" | "none") => {
    await updateNotifications.mutateAsync({ server_notification_level: value });
  };

  const updateChannelNotification = async (channelId: string, value: "all" | "mentions" | "none") => {
    await updateNotifications.mutateAsync({
      channel_notifications: [{ channel_id: channelId, notification_level: value }],
    });
  };

  if (serversQuery.isLoading || shellQuery.isLoading) {
    return (
      <div className="grid min-h-[calc(100vh-8rem)] gap-4 xl:grid-cols-[80px_320px_minmax(0,1fr)_320px]">
        <Skeleton className="h-full min-h-[720px] rounded-[2rem]" />
        <Skeleton className="h-full min-h-[720px] rounded-[2rem]" />
        <Skeleton className="h-full min-h-[720px] rounded-[2rem]" />
        <Skeleton className="h-full min-h-[720px] rounded-[2rem]" />
      </div>
    );
  }

  if (serversQuery.isError || shellQuery.isError || !shellQuery.data) {
    return (
      <div className="glass-card rounded-[2rem] border border-rose-500/20 bg-rose-950/20 p-8">
        <p className="text-sm uppercase tracking-[0.3em] text-rose-300">Partner Shell</p>
        <h1 className="mt-3 text-3xl font-semibold text-white">Server shell could not load</h1>
        <p className="mt-4 max-w-2xl text-sm text-slate-300">
          The partner shell route is in place, but the frontend could not complete its initial API load. Check the
          partner APIs and the `NEXT_PUBLIC_PLATFORM_API_BASE` setting in `admin_ui`.
        </p>
      </div>
    );
  }

  const server = shellQuery.data.partner;
  const servers = serversQuery.data ?? [];
  const profile = shellQuery.data.organizationProfile;
  const inviteList = invitesQuery.data ?? [];
  const memberList = membersQuery.data ?? [];
  const moderationActions = moderationQuery.data ?? [];
  const screening = screeningQuery.data;
  const notificationPreferences = notificationsQuery.data;

  return (
    <div className="grid min-h-[calc(100vh-8rem)] gap-4 xl:grid-cols-[80px_320px_minmax(0,1fr)_320px]">
      <section className="glass-card flex flex-col items-center gap-4 rounded-[2rem] border border-slate-800 bg-slate-950/75 p-3">
        <div className="flex h-14 w-14 items-center justify-center rounded-[1.6rem] bg-gradient-to-br from-cyan-500 to-blue-600 text-lg font-semibold text-white shadow-lg shadow-cyan-950/40">
          K
        </div>
        <div className="h-px w-10 bg-slate-800" />
        <div className="flex flex-1 flex-col items-center gap-3 overflow-y-auto scrollbar-hide">
          {servers.map((item: PartnerSummary) => {
            const active = item.id === partnerId;
            return (
              <Link key={item.id} href={`/partners/${item.id}` as Route} className="relative block">
                <div
                  className={clsx(
                    "flex h-14 w-14 items-center justify-center rounded-[1.4rem] border text-sm font-semibold transition",
                    active
                      ? "border-cyan-400/40 bg-gradient-to-br from-cyan-500/30 to-blue-600/20 text-white"
                      : "border-slate-800 bg-slate-900/90 text-slate-300 hover:border-slate-700 hover:text-white"
                  )}
                  title={item.name}
                >
                  {item.avatar_url ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={item.avatar_url} alt={item.name} className="h-full w-full rounded-[1.3rem] object-cover" />
                  ) : (
                    initialsFor(item.name)
                  )}
                </div>
                {active ? <span className="absolute -left-2 top-1/2 h-7 w-1 -translate-y-1/2 rounded-full bg-cyan-300" /> : null}
              </Link>
            );
          })}
        </div>
        <Link
          href={"/partners" as Route}
          className="flex h-12 w-12 items-center justify-center rounded-2xl border border-dashed border-slate-700 bg-slate-900/80 text-slate-400 transition hover:text-white"
          title="All partner servers"
        >
          <UsersIcon className="h-5 w-5" />
        </Link>
      </section>

      <section className="glass-card flex flex-col overflow-hidden rounded-[2rem] border border-slate-800 bg-slate-950/80">
        <div className="border-b border-slate-800 px-5 py-5">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-[11px] uppercase tracking-[0.35em] text-cyan-300/70">Partner Server</p>
              <h1 className="mt-2 text-2xl font-semibold text-white">{server.name}</h1>
              <p className="mt-2 text-sm text-slate-400">{profile?.tagline || server.description || "Discord-style partner workspace shell."}</p>
            </div>
            <div className="rounded-2xl border border-cyan-500/20 bg-cyan-500/10 px-3 py-2 text-xs uppercase tracking-[0.3em] text-cyan-200">
              {roleLabel(server.member_role)}
            </div>
          </div>
          <div className="mt-5 grid gap-2 sm:grid-cols-2">
            <button
              onClick={() => setActivePanel((current) => (current === "invites" ? null : "invites"))}
              className="rounded-2xl border border-slate-700 bg-slate-900 px-4 py-2 text-left text-sm text-slate-200 transition hover:border-slate-600 hover:text-white"
            >
              <UserPlusIcon className="mr-2 inline h-4 w-4" />
              Invite members
            </button>
            <button
              onClick={() => {
                setActivePanel((current) => (current === "onboarding" ? null : "onboarding"));
                setSelectedOnboardingRoles(onboardingProgress?.selected_role_ids ?? []);
                setSelectedOnboardingChannels(onboardingProgress?.selected_channel_ids ?? onboardingConfig?.default_channel_ids ?? []);
                setAcceptRules(Boolean(onboardingProgress?.rules_accepted_at));
              }}
              className="rounded-2xl border border-slate-700 bg-slate-900 px-4 py-2 text-left text-sm text-slate-200 transition hover:border-slate-600 hover:text-white"
            >
              <BoltIcon className="mr-2 inline h-4 w-4" />
              Onboarding
            </button>
            <button
              onClick={() => {
                setActivePanel((current) => (current === "screening" ? null : "screening"));
                setScreeningRules(screening?.rules_text ?? "");
                setScreeningEnabled(Boolean(screening?.enabled));
                setRequireRulesAcceptance(Boolean(screening?.require_rules_acceptance));
              }}
              className="rounded-2xl border border-slate-700 bg-slate-900 px-4 py-2 text-left text-sm text-slate-200 transition hover:border-slate-600 hover:text-white"
            >
              <Cog6ToothIcon className="mr-2 inline h-4 w-4" />
              Screening rules
            </button>
            <button
              onClick={() => setActivePanel((current) => (current === "moderation" ? null : "moderation"))}
              className="rounded-2xl border border-slate-700 bg-slate-900 px-4 py-2 text-left text-sm text-slate-200 transition hover:border-slate-600 hover:text-white"
            >
              <CheckBadgeIcon className="mr-2 inline h-4 w-4" />
              Moderation
            </button>
            <Link
              href={`/partners/${partnerId}/hub` as Route}
              className="rounded-2xl border border-cyan-500/20 bg-cyan-500/10 px-4 py-2 text-left text-sm text-cyan-100 transition hover:border-cyan-400/30 hover:text-white"
            >
              <BoltIcon className="mr-2 inline h-4 w-4" />
              Phase 5 hub
            </Link>
          </div>
        </div>

        <div className="flex-1 space-y-6 overflow-y-auto px-4 py-5">
          {activePanel === "invites" ? (
            <div className="rounded-[1.8rem] border border-cyan-500/20 bg-cyan-500/5 p-4">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="text-xs uppercase tracking-[0.3em] text-cyan-300">Invite Console</p>
                  <p className="mt-2 text-sm text-slate-300">Create reusable or limited server invites, then disable them when needed.</p>
                </div>
                <span className="rounded-full border border-slate-700 px-3 py-1 text-xs text-slate-300">{inviteList.length} active records</span>
              </div>
              <div className="mt-4 grid gap-3 md:grid-cols-2">
                <input
                  value={inviteLabel}
                  onChange={(event) => setInviteLabel(event.target.value)}
                  placeholder="Invite label"
                  className="rounded-2xl border border-slate-700 bg-slate-950/80 px-4 py-3 text-sm text-white outline-none"
                />
                <select
                  value={inviteRole}
                  onChange={(event) => setInviteRole(event.target.value)}
                  className="rounded-2xl border border-slate-700 bg-slate-950/80 px-4 py-3 text-sm text-white outline-none"
                >
                  <option value="member">Member</option>
                  <option value="subscriber">Subscriber</option>
                </select>
                <input
                  value={inviteMaxUses}
                  onChange={(event) => setInviteMaxUses(event.target.value)}
                  placeholder="Max uses"
                  className="rounded-2xl border border-slate-700 bg-slate-950/80 px-4 py-3 text-sm text-white outline-none"
                />
                <input
                  type="datetime-local"
                  value={inviteExpiresAt}
                  onChange={(event) => setInviteExpiresAt(event.target.value)}
                  className="rounded-2xl border border-slate-700 bg-slate-950/80 px-4 py-3 text-sm text-white outline-none"
                />
              </div>
              <button
                onClick={() => void submitInvite()}
                disabled={createInvite.isPending}
                className="mt-4 rounded-2xl bg-cyan-500 px-4 py-3 text-sm font-medium text-slate-950 transition hover:bg-cyan-400 disabled:opacity-60"
              >
                {createInvite.isPending ? "Creating..." : "Create invite"}
              </button>
              <div className="mt-4 space-y-3">
                {inviteList.map((invite: PartnerInvite) => (
                  <div key={invite.id} className="rounded-2xl border border-slate-800 bg-slate-950/80 p-3">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <p className="text-sm font-medium text-white">{invite.label || invite.code}</p>
                        <p className="mt-1 text-xs text-slate-400">code: {invite.code} · role: {roleLabel(invite.membership_role)}</p>
                        <p className="mt-1 text-xs text-slate-500">
                          uses {invite.use_count}/{invite.max_uses ?? "unlimited"} · expires {formatDateTime(invite.expires_at)}
                        </p>
                      </div>
                      <button
                        onClick={() => void disableInvite.mutateAsync(invite.id)}
                        disabled={disableInvite.isPending || !invite.is_active}
                        className="rounded-xl border border-rose-500/20 px-3 py-2 text-xs text-rose-200 transition hover:bg-rose-500/10 disabled:opacity-50"
                      >
                        {invite.is_active ? "Disable" : "Disabled"}
                      </button>
                    </div>
                  </div>
                ))}
                {invitesQuery.isError ? <p className="text-sm text-amber-300">Invite management is only available to partner managers.</p> : null}
              </div>
            </div>
          ) : null}

          {activePanel === "onboarding" ? (
            <div className="rounded-[1.8rem] border border-emerald-500/20 bg-emerald-500/5 p-4">
              <p className="text-xs uppercase tracking-[0.3em] text-emerald-300">Onboarding flow</p>
              <p className="mt-2 text-sm text-slate-300">{onboardingConfig?.welcome_message || "Configure role picks, rules acceptance, and default channels here."}</p>
              <div className="mt-4 rounded-2xl border border-slate-800 bg-slate-950/80 p-4">
                <p className="text-xs uppercase tracking-[0.25em] text-slate-500">Rules</p>
                <p className="mt-3 whitespace-pre-wrap text-sm text-slate-200">{onboardingConfig?.rules_text || "No screening rules configured yet."}</p>
                <label className="mt-4 flex items-center gap-3 text-sm text-slate-300">
                  <input type="checkbox" checked={acceptRules} onChange={(event) => setAcceptRules(event.target.checked)} />
                  Accept rules for this server
                </label>
              </div>
              <div className="mt-4 grid gap-4 lg:grid-cols-2">
                <div className="rounded-2xl border border-slate-800 bg-slate-950/80 p-4">
                  <p className="text-xs uppercase tracking-[0.25em] text-slate-500">Role selection</p>
                  <div className="mt-3 space-y-2">
                    {(onboardingConfig?.role_options ?? []).map((role) => (
                      <label key={role.id} className="flex items-start gap-3 rounded-2xl border border-slate-800 px-3 py-3 text-sm text-slate-200">
                        <input
                          type="checkbox"
                          checked={selectedOnboardingRoles.includes(role.id)}
                          onChange={() => toggleOnboardingRole(role.id)}
                        />
                        <span>
                          <span className="block font-medium text-white">{role.name}</span>
                          <span className="text-xs text-slate-500">{role.description || "No description"}</span>
                        </span>
                      </label>
                    ))}
                  </div>
                </div>
                <div className="rounded-2xl border border-slate-800 bg-slate-950/80 p-4">
                  <p className="text-xs uppercase tracking-[0.25em] text-slate-500">Default channels</p>
                  <div className="mt-3 space-y-2">
                    {shellQuery.data.channels.map((channel) => (
                      <label key={channel.id} className="flex items-center gap-3 rounded-2xl border border-slate-800 px-3 py-3 text-sm text-slate-200">
                        <input
                          type="checkbox"
                          checked={selectedOnboardingChannels.includes(channel.id)}
                          onChange={() => toggleOnboardingChannel(channel.id)}
                        />
                        <span>{channel.name}</span>
                      </label>
                    ))}
                  </div>
                </div>
              </div>
              <button
                onClick={() => void submitOnboarding()}
                disabled={completeOnboarding.isPending}
                className="mt-4 rounded-2xl bg-emerald-500 px-4 py-3 text-sm font-medium text-slate-950 transition hover:bg-emerald-400 disabled:opacity-60"
              >
                {completeOnboarding.isPending ? "Saving..." : "Save onboarding"}
              </button>
              {onboardingProgress?.completed_at ? (
                <p className="mt-3 text-xs text-emerald-300">Completed at {formatDateTime(onboardingProgress.completed_at)}</p>
              ) : null}
            </div>
          ) : null}

          {activePanel === "screening" ? (
            <div className="rounded-[1.8rem] border border-amber-500/20 bg-amber-500/5 p-4">
              <p className="text-xs uppercase tracking-[0.3em] text-amber-300">Member screening</p>
              <div className="mt-4 space-y-3">
                <label className="flex items-center gap-3 text-sm text-slate-200">
                  <input type="checkbox" checked={screeningEnabled} onChange={(event) => setScreeningEnabled(event.target.checked)} />
                  Screening enabled
                </label>
                <label className="flex items-center gap-3 text-sm text-slate-200">
                  <input
                    type="checkbox"
                    checked={requireRulesAcceptance}
                    onChange={(event) => setRequireRulesAcceptance(event.target.checked)}
                  />
                  Require rules acceptance
                </label>
                <textarea
                  value={screeningRules}
                  onChange={(event) => setScreeningRules(event.target.value)}
                  rows={7}
                  placeholder="Write partner rules and screening copy"
                  className="w-full rounded-2xl border border-slate-700 bg-slate-950/80 px-4 py-3 text-sm text-white outline-none"
                />
                <button
                  onClick={() => void saveScreening()}
                  disabled={updateScreening.isPending}
                  className="rounded-2xl bg-amber-400 px-4 py-3 text-sm font-medium text-slate-950 transition hover:bg-amber-300 disabled:opacity-60"
                >
                  {updateScreening.isPending ? "Saving..." : "Save screening"}
                </button>
              </div>
            </div>
          ) : null}

          {activePanel === "moderation" ? (
            <div className="rounded-[1.8rem] border border-rose-500/20 bg-rose-500/5 p-4">
              <p className="text-xs uppercase tracking-[0.3em] text-rose-300">Moderation deck</p>
              <div className="mt-4 grid gap-3">
                <select
                  value={moderationTargetId}
                  onChange={(event) => setModerationTargetId(event.target.value)}
                  className="rounded-2xl border border-slate-700 bg-slate-950/80 px-4 py-3 text-sm text-white outline-none"
                >
                  <option value="">Select member</option>
                  {memberList.map((member) => (
                    <option key={member.user_id} value={member.user_id}>
                      {member.display_name || member.username || member.user_id}
                    </option>
                  ))}
                </select>
                <select
                  value={moderationAction}
                  onChange={(event) => setModerationAction(event.target.value as typeof moderationAction)}
                  className="rounded-2xl border border-slate-700 bg-slate-950/80 px-4 py-3 text-sm text-white outline-none"
                >
                  <option value="mute">Mute</option>
                  <option value="unmute">Unmute</option>
                  <option value="timeout">Timeout</option>
                  <option value="kick">Kick</option>
                  <option value="ban">Ban</option>
                  <option value="unban">Unban</option>
                </select>
                <input
                  value={moderationReason}
                  onChange={(event) => setModerationReason(event.target.value)}
                  placeholder="Reason"
                  className="rounded-2xl border border-slate-700 bg-slate-950/80 px-4 py-3 text-sm text-white outline-none"
                />
                <input
                  type="datetime-local"
                  value={moderationExpiry}
                  onChange={(event) => setModerationExpiry(event.target.value)}
                  className="rounded-2xl border border-slate-700 bg-slate-950/80 px-4 py-3 text-sm text-white outline-none"
                />
                <button
                  onClick={() => void submitModeration()}
                  disabled={moderateMember.isPending || !moderationTargetId}
                  className="rounded-2xl bg-rose-400 px-4 py-3 text-sm font-medium text-slate-950 transition hover:bg-rose-300 disabled:opacity-60"
                >
                  {moderateMember.isPending ? "Applying..." : "Apply moderation"}
                </button>
                {selectedMember ? (
                  <p className="text-xs text-slate-400">
                    Target status: {selectedMember.membership_status} · role: {selectedMember.membership_role}
                  </p>
                ) : null}
              </div>
            </div>
          ) : null}

          <div>
            <div className="mb-3 flex items-center justify-between px-1">
              <p className="text-[11px] uppercase tracking-[0.35em] text-slate-500">Channels</p>
              <span className="rounded-full border border-slate-800 px-2 py-1 text-[10px] text-slate-400">
                {shellQuery.data.channels.length}
              </span>
            </div>
            <div className="space-y-4">
              {groupedChannels.map((group) => (
                <div key={group.key}>
                  <div className="mb-2 flex items-center gap-2 px-2 text-xs uppercase tracking-[0.3em] text-slate-500">
                    <span>{group.title}</span>
                    {group.isPrivate ? (
                      <span className="rounded-full border border-amber-500/20 bg-amber-500/10 px-2 py-0.5 text-[9px] text-amber-300">
                        private
                      </span>
                    ) : null}
                  </div>
                  <div className="space-y-1">
                    {group.channels.map((channel) => {
                      const Icon = channelIcon(channel.channel_type);
                      const active = selectedChannel?.id === channel.id;
                      return (
                        <button
                          key={channel.id}
                          onClick={() => selectChannel(channel.id)}
                          className={clsx(
                            "flex w-full items-center gap-3 rounded-2xl px-3 py-2.5 text-left transition",
                            active
                              ? "bg-gradient-to-r from-cyan-500/15 to-blue-500/10 text-white"
                              : "text-slate-300 hover:bg-slate-900 hover:text-white"
                          )}
                        >
                          <Icon className="h-4 w-4 shrink-0" />
                          <div className="min-w-0 flex-1">
                            <div className="truncate text-sm font-medium">{channel.name}</div>
                            <div className="truncate text-xs text-slate-500">
                              {channel.can_post ? "chat enabled" : "view only"}
                            </div>
                          </div>
                          {channel.channel_type === "announcement" ? (
                            <span className="h-2 w-2 rounded-full bg-cyan-300" />
                          ) : null}
                        </button>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div>
            <div className="mb-3 px-1 text-[11px] uppercase tracking-[0.35em] text-slate-500">Apps</div>
            <div className="space-y-3">
              {groupedApps.map(([groupName, apps]) => (
                <div key={groupName} className="rounded-[1.6rem] border border-slate-800 bg-slate-900/70 p-3">
                  <p className="mb-3 text-xs uppercase tracking-[0.3em] text-slate-500">{groupName}</p>
                  <div className="space-y-2">
                    {apps.map((app) => (
                      <div key={app.id} className="flex items-center justify-between gap-3 rounded-2xl bg-slate-950/70 px-3 py-2">
                        <div>
                          <p className="text-sm font-medium text-slate-100">{app.name}</p>
                          <p className="text-xs text-slate-500">{app.module || app.type}</p>
                        </div>
                        <span className="rounded-full border border-slate-800 px-2 py-1 text-[10px] uppercase tracking-[0.25em] text-slate-400">
                          {app.badge_label || app.type}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      <section className="glass-card overflow-hidden rounded-[2rem] border border-slate-800 bg-[radial-gradient(circle_at_top,#164e63_0%,rgba(2,6,23,0.94)_42%,rgba(2,6,23,1)_100%)]">
        <div className="border-b border-slate-800 px-6 py-5">
          <p className="text-[11px] uppercase tracking-[0.35em] text-cyan-200/70">Current Space</p>
          <h2 className="mt-3 text-3xl font-semibold text-white">{selectedChannel?.name || "Select a channel"}</h2>
          <p className="mt-3 max-w-2xl text-sm text-slate-300">
            {selectedChannel?.description ||
              "Messaging implementation is intentionally separate. This shell now covers invites, onboarding, moderation, screening, and notification controls on top of the server shell."}
          </p>
        </div>

        <div className="grid gap-4 p-6 lg:grid-cols-[minmax(0,1fr)_320px]">
          <div className="space-y-4">
            <div className="rounded-[1.8rem] border border-slate-800 bg-slate-950/70 p-5">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <p className="text-xs uppercase tracking-[0.3em] text-slate-500">Channel mode</p>
                  <p className="mt-2 text-lg font-medium text-white">{selectedChannel?.channel_type || "text"}</p>
                </div>
                <div className="rounded-2xl border border-slate-800 bg-slate-900/80 px-3 py-2 text-xs uppercase tracking-[0.25em] text-slate-300">
                  {selectedChannel?.can_post ? "write enabled" : "read only"}
                </div>
              </div>
              <div className="mt-5 grid gap-3 md:grid-cols-3">
                <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-4">
                  <p className="text-xs uppercase tracking-[0.3em] text-slate-500">Category</p>
                  <p className="mt-2 text-sm text-white">{selectedChannel?.category_name || "Lobby"}</p>
                </div>
                <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-4">
                  <p className="text-xs uppercase tracking-[0.3em] text-slate-500">Subscription</p>
                  <p className="mt-2 text-sm text-white">{selectedChannel?.is_subscribed ? "following" : "not joined"}</p>
                </div>
                <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-4">
                  <p className="text-xs uppercase tracking-[0.3em] text-slate-500">Role gate</p>
                  <p className="mt-2 text-sm text-white">{roleLabel(server.member_role)}</p>
                </div>
              </div>
            </div>

            <div className="grid gap-4 md:grid-cols-2">
              <div className="rounded-[1.8rem] border border-slate-800 bg-slate-950/70 p-5">
                <p className="text-xs uppercase tracking-[0.3em] text-slate-500">Partner profile</p>
                <p className="mt-3 text-lg font-medium text-white">{profile?.summary || "No organization summary yet."}</p>
                <div className="mt-4 space-y-2 text-sm text-slate-400">
                  {profile?.location ? <p>Location: {profile.location}</p> : null}
                  {profile?.website ? <p>Website: {profile.website}</p> : null}
                  <p>Members visible: {memberList.length}</p>
                  <p>Recent moderation entries: {moderationActions.length}</p>
                </div>
              </div>
              <div className="rounded-[1.8rem] border border-slate-800 bg-slate-950/70 p-5">
                <p className="text-xs uppercase tracking-[0.3em] text-slate-500">Phase 4 scope</p>
                <div className="mt-4 space-y-3 text-sm text-slate-300">
                  <p>Invites, onboarding, screening, moderation, and notification preferences are now surfaced inside the server shell.</p>
                  <p>Unread counters and live presence can be layered on cleanly when the messaging system phase is ready.</p>
                </div>
              </div>
            </div>

            <div className="rounded-[1.8rem] border border-slate-800 bg-slate-950/80 p-4">
              <div className="mb-4 flex items-center gap-3">
                <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-2">
                  <ChatBubbleLeftRightIcon className="h-5 w-5 text-cyan-300" />
                </div>
                <div>
                  <p className="text-xs uppercase tracking-[0.3em] text-slate-500">Member rail</p>
                  <p className="text-sm text-slate-300">{memberList.length} visible identities</p>
                </div>
              </div>
              <div className="space-y-4">
                {groupedMembers.map((group) => (
                  <div key={group.title}>
                    <p className="mb-2 text-xs uppercase tracking-[0.25em] text-slate-500">{group.title}</p>
                    <div className="space-y-2">
                      {group.items.map((item) => (
                        <div key={`${group.title}-${item.user_id}`} className="rounded-2xl border border-slate-800 bg-slate-900/70 px-3 py-3">
                          <div className="flex items-start justify-between gap-3">
                            <div>
                              <p className="text-sm font-medium text-white">{item.display_name || item.username || item.user_id}</p>
                              <p className="mt-1 text-xs text-slate-400">
                                {item.membership_status} · {roleLabel(item.membership_role)}
                              </p>
                              {item.timed_out_until ? (
                                <p className="mt-1 text-xs text-amber-300">Timed out until {formatDateTime(item.timed_out_until)}</p>
                              ) : null}
                            </div>
                            <div className="flex flex-wrap gap-2">
                              {item.is_banned ? <span className="rounded-full border border-rose-500/20 px-2 py-1 text-[10px] text-rose-300">banned</span> : null}
                              {item.is_muted ? <span className="rounded-full border border-amber-500/20 px-2 py-1 text-[10px] text-amber-300">muted</span> : null}
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <aside className="space-y-4">
            <div className="rounded-[1.8rem] border border-slate-800 bg-slate-950/80 p-4">
              <div className="mb-4 flex items-center gap-3">
                <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-2">
                  <SpeakerWaveIcon className="h-5 w-5 text-cyan-300" />
                </div>
                <div>
                  <p className="text-xs uppercase tracking-[0.3em] text-slate-500">Notifications</p>
                  <p className="text-sm text-slate-300">Server and channel preference controls</p>
                </div>
              </div>
              <div className="space-y-3">
                <div>
                  <p className="mb-2 text-xs uppercase tracking-[0.25em] text-slate-500">Server</p>
                  <select
                    value={notificationPreferences?.server_notification_level ?? "all"}
                    onChange={(event) => void updateServerNotification(event.target.value as "all" | "mentions" | "none")}
                    className="w-full rounded-2xl border border-slate-700 bg-slate-900/80 px-3 py-3 text-sm text-white outline-none"
                  >
                    {notificationOptions.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="space-y-2">
                  {(notificationPreferences?.channel_notifications ?? []).slice(0, 6).map((item) => (
                    <div key={item.channel_id} className="rounded-2xl border border-slate-800 bg-slate-900/70 p-3">
                      <p className="text-sm font-medium text-white">{item.channel_name}</p>
                      <select
                        value={item.notification_level}
                        onChange={(event) =>
                          void updateChannelNotification(item.channel_id, event.target.value as "all" | "mentions" | "none")
                        }
                        className="mt-2 w-full rounded-xl border border-slate-700 bg-slate-950/80 px-3 py-2 text-sm text-white outline-none"
                      >
                        {notificationOptions.map((option) => (
                          <option key={option.value} value={option.value}>
                            {option.label}
                          </option>
                        ))}
                      </select>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            <div className="rounded-[1.8rem] border border-slate-800 bg-slate-950/80 p-4">
              <div className="mb-4 flex items-center gap-3">
                <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-2">
                  <BellAlertIcon className="h-5 w-5 text-amber-300" />
                </div>
                <div>
                  <p className="text-xs uppercase tracking-[0.3em] text-slate-500">Screening status</p>
                  <p className="text-sm text-slate-300">{screening?.enabled ? "Enabled" : "Disabled"}</p>
                </div>
              </div>
              <div className="space-y-2 text-sm text-slate-300">
                <p>Rules acceptance: {screening?.require_rules_acceptance ? "required" : "optional"}</p>
                <p>Questions configured: {screening?.screening_questions?.length ?? 0}</p>
                <p className="text-slate-400">{screening?.rules_text ? `${screening.rules_text.slice(0, 140)}${screening.rules_text.length > 140 ? "..." : ""}` : "No screening copy yet."}</p>
              </div>
            </div>

            <div className="rounded-[1.8rem] border border-slate-800 bg-slate-950/80 p-4">
              <p className="text-xs uppercase tracking-[0.3em] text-slate-500">Moderation log</p>
              <div className="mt-3 space-y-2">
                {moderationActions.slice(0, 6).map((action) => (
                  <div key={action.id} className="rounded-2xl border border-slate-800 bg-slate-900/70 p-3">
                    <p className="text-sm font-medium text-white">{action.action_type}</p>
                    <p className="mt-1 text-xs text-slate-400">{action.user_name || action.user}</p>
                    <p className="mt-1 text-xs text-slate-500">{formatDateTime(action.created_at)}</p>
                  </div>
                ))}
                {moderationQuery.isError ? <p className="text-xs text-amber-300">Moderation history requires manager access.</p> : null}
              </div>
            </div>
          </aside>
        </div>
      </section>

      <aside className="glass-card hidden rounded-[2rem] border border-slate-800 bg-slate-950/80 xl:block">
        <div className="border-b border-slate-800 px-5 py-5">
          <p className="text-[11px] uppercase tracking-[0.35em] text-slate-500">Server actions</p>
          <h3 className="mt-2 text-xl font-semibold text-white">Control deck</h3>
        </div>
        <div className="space-y-3 p-4">
          <Link href={`/partners/${partnerId}` as Route} className="block rounded-2xl border border-slate-800 bg-slate-900/70 px-4 py-3 text-sm text-slate-200">
            Server overview
          </Link>
          <button
            onClick={() => setActivePanel("invites")}
            className="w-full rounded-2xl border border-slate-800 bg-slate-900/70 px-4 py-3 text-left text-sm text-slate-200"
          >
            Invite entry point
          </button>
          <button
            onClick={() => setActivePanel("onboarding")}
            className="w-full rounded-2xl border border-slate-800 bg-slate-900/70 px-4 py-3 text-left text-sm text-slate-200"
          >
            Onboarding entry point
          </button>
          <button
            onClick={() => setActivePanel("screening")}
            className="w-full rounded-2xl border border-slate-800 bg-slate-900/70 px-4 py-3 text-left text-sm text-slate-200"
          >
            Screening entry point
          </button>
          <button
            onClick={() => setActivePanel("moderation")}
            className="w-full rounded-2xl border border-slate-800 bg-slate-900/70 px-4 py-3 text-left text-sm text-slate-200"
          >
            Moderation entry point
          </button>
          <Link
            href={`/partners/${partnerId}/hub` as Route}
            className="block rounded-2xl border border-cyan-500/20 bg-cyan-500/10 px-4 py-3 text-sm text-cyan-100"
          >
            Phase 5 differentiator hub
          </Link>
        </div>
      </aside>
    </div>
  );
}
