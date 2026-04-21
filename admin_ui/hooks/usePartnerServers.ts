"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  applyPartnerAutomationRecipe,
  completePartnerOnboarding,
  fetchPartnerAutomationRecipes,
  fetchPartnerDifferentiatorInsights,
  fetchPartnerExperienceTemplates,
  createPartnerInvite,
  disablePartnerInvite,
  fetchPartnerPublicHub,
  fetchPartnerInvites,
  fetchPartnerMembers,
  fetchPartnerModerationActions,
  fetchPartnerNotificationPreferences,
  fetchPartnerOnboarding,
  fetchPartnerScreening,
  fetchPartnerServerShell,
  fetchPartnerServers,
  fetchPartnerTeamStructure,
  moderatePartnerMember,
  updatePartnerNotificationPreferences,
  updatePartnerScreening,
} from "@/lib/api";

export function usePartnerServers() {
  return useQuery({
    queryKey: ["partners", "servers"],
    queryFn: fetchPartnerServers,
    staleTime: 30_000,
  });
}

export function usePartnerServerShell(partnerId: string) {
  return useQuery({
    queryKey: ["partners", "server-shell", partnerId],
    queryFn: () => fetchPartnerServerShell(partnerId),
    enabled: Boolean(partnerId),
    staleTime: 15_000,
  });
}

export function usePartnerInvites(partnerId: string) {
  return useQuery({
    queryKey: ["partners", partnerId, "invites"],
    queryFn: () => fetchPartnerInvites(partnerId),
    enabled: Boolean(partnerId),
    staleTime: 10_000,
  });
}

export function usePartnerOnboarding(partnerId: string) {
  return useQuery({
    queryKey: ["partners", partnerId, "onboarding"],
    queryFn: () => fetchPartnerOnboarding(partnerId),
    enabled: Boolean(partnerId),
    staleTime: 10_000,
  });
}

export function usePartnerMembers(partnerId: string) {
  return useQuery({
    queryKey: ["partners", partnerId, "members"],
    queryFn: () => fetchPartnerMembers(partnerId),
    enabled: Boolean(partnerId),
    staleTime: 10_000,
  });
}

export function usePartnerModerationActions(partnerId: string) {
  return useQuery({
    queryKey: ["partners", partnerId, "moderation-actions"],
    queryFn: () => fetchPartnerModerationActions(partnerId),
    enabled: Boolean(partnerId),
    staleTime: 10_000,
  });
}

export function usePartnerScreening(partnerId: string) {
  return useQuery({
    queryKey: ["partners", partnerId, "screening"],
    queryFn: () => fetchPartnerScreening(partnerId),
    enabled: Boolean(partnerId),
    staleTime: 10_000,
  });
}

export function usePartnerNotificationPreferences(partnerId: string) {
  return useQuery({
    queryKey: ["partners", partnerId, "notification-preferences"],
    queryFn: () => fetchPartnerNotificationPreferences(partnerId),
    enabled: Boolean(partnerId),
    staleTime: 10_000,
  });
}

export function useCreatePartnerInvite(partnerId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: { label?: string; max_uses?: number | null; expires_at?: string | null; membership_role?: string }) =>
      createPartnerInvite(partnerId, payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["partners", partnerId, "invites"] });
    },
  });
}

export function useDisablePartnerInvite(partnerId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (inviteId: string) => disablePartnerInvite(partnerId, inviteId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["partners", partnerId, "invites"] });
    },
  });
}

export function useCompletePartnerOnboarding(partnerId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: { accept_rules?: boolean; role_ids?: string[]; channel_ids?: string[] }) =>
      completePartnerOnboarding(partnerId, payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["partners", partnerId, "onboarding"] });
      void queryClient.invalidateQueries({ queryKey: ["partners", partnerId, "members"] });
      void queryClient.invalidateQueries({ queryKey: ["partners", "server-shell", partnerId] });
    },
  });
}

export function useModeratePartnerMember(partnerId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: {
      memberUserId: string;
      action: "mute" | "unmute" | "timeout" | "kick" | "ban" | "unban";
      reason?: string;
      expires_at?: string | null;
    }) => moderatePartnerMember(partnerId, payload.memberUserId, payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["partners", partnerId, "members"] });
      void queryClient.invalidateQueries({ queryKey: ["partners", partnerId, "moderation-actions"] });
      void queryClient.invalidateQueries({ queryKey: ["partners", "server-shell", partnerId] });
    },
  });
}

export function useUpdatePartnerScreening(partnerId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: { enabled?: boolean; require_rules_acceptance?: boolean; rules_text?: string; screening_questions?: Array<Record<string, unknown> | string> }) =>
      updatePartnerScreening(partnerId, payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["partners", partnerId, "screening"] });
      void queryClient.invalidateQueries({ queryKey: ["partners", partnerId, "onboarding"] });
    },
  });
}

export function useUpdatePartnerNotificationPreferences(partnerId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: {
      server_notification_level?: "all" | "mentions" | "none";
      channel_notifications?: Array<{ channel_id: string; notification_level: "all" | "mentions" | "none" }>;
    }) => updatePartnerNotificationPreferences(partnerId, payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["partners", partnerId, "notification-preferences"] });
      void queryClient.invalidateQueries({ queryKey: ["partners", "server-shell", partnerId] });
    },
  });
}

export function usePartnerPublicHub(partnerId: string) {
  return useQuery({
    queryKey: ["partners", partnerId, "public-hub"],
    queryFn: () => fetchPartnerPublicHub(partnerId),
    enabled: Boolean(partnerId),
    staleTime: 15_000,
  });
}

export function usePartnerDifferentiatorInsights(partnerId: string) {
  return useQuery({
    queryKey: ["partners", partnerId, "differentiator-insights"],
    queryFn: () => fetchPartnerDifferentiatorInsights(partnerId),
    enabled: Boolean(partnerId),
    staleTime: 15_000,
  });
}

export function usePartnerTeamStructure(partnerId: string) {
  return useQuery({
    queryKey: ["partners", partnerId, "team-structure"],
    queryFn: () => fetchPartnerTeamStructure(partnerId),
    enabled: Boolean(partnerId),
    staleTime: 15_000,
  });
}

export function usePartnerAutomationRecipes(partnerId: string) {
  return useQuery({
    queryKey: ["partners", partnerId, "automation-recipes"],
    queryFn: () => fetchPartnerAutomationRecipes(partnerId),
    enabled: Boolean(partnerId),
    staleTime: 15_000,
  });
}

export function usePartnerExperienceTemplates(partnerId: string) {
  return useQuery({
    queryKey: ["partners", partnerId, "experience-templates"],
    queryFn: () => fetchPartnerExperienceTemplates(partnerId),
    enabled: Boolean(partnerId),
    staleTime: 15_000,
  });
}

export function useApplyPartnerAutomationRecipe(partnerId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (recipeKey: string) => applyPartnerAutomationRecipe(partnerId, recipeKey),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["partners", partnerId, "automation-recipes"] });
      void queryClient.invalidateQueries({ queryKey: ["partners", "server-shell", partnerId] });
    },
  });
}
