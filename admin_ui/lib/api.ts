import axios from "axios";

const ADMIN_API_BASE =
  process.env.NEXT_PUBLIC_ADMIN_API_BASE || "http://10.14.20.99:8000/control/admin";
const PLATFORM_API_BASE =
  process.env.NEXT_PUBLIC_PLATFORM_API_BASE || "http://10.14.20.99:8000/api/v1";

export const apiClient = axios.create({
  baseURL: ADMIN_API_BASE,
  timeout: 10000,
  withCredentials: true,
});

export const platformApiClient = axios.create({
  baseURL: PLATFORM_API_BASE,
  timeout: 10000,
  withCredentials: true,
});

type PaginatedResponse<T> = {
  results?: T[];
  meta?: {
    count?: number;
    total_pages?: number;
    current?: number;
    page_size?: number;
  };
};

function extractList<T>(payload: T[] | PaginatedResponse<T> | { [key: string]: T[] }, key?: string): T[] {
  if (Array.isArray(payload)) {
    return payload;
  }
  const keyedPayload = payload as Record<string, T[] | undefined>;
  if (key && Array.isArray(keyedPayload[key])) {
    return keyedPayload[key] as T[];
  }
  if (Array.isArray(payload.results)) {
    return payload.results;
  }
  return [];
}

export type PartnerSummary = {
  id: string;
  name: string;
  slug: string;
  avatar_url?: string;
  member_role?: string | null;
  is_active: boolean;
};

export type PartnerAdminSummary = {
  id: string;
  name: string;
  initials: string;
  position: string;
  avatarUrl?: string | null;
};

export type PartnerDetail = {
  id: string;
  name: string;
  slug: string;
  description?: string;
  avatar_url?: string;
  owner: string;
  member_role?: string | null;
  admins?: PartnerAdminSummary[];
  is_active: boolean;
};

export type PartnerServerCategory = {
  id: string;
  name: string;
  slug: string;
  order: number;
  is_private: boolean;
};

export type PartnerServerChannel = {
  id: string;
  name: string;
  slug: string;
  description?: string;
  channel_type: "text" | "announcement" | "private";
  order: number;
  category?: number | string | null;
  category_id?: string | null;
  category_name?: string | null;
  can_post?: boolean;
  is_subscribed?: boolean;
  member_role?: string | null;
};

export type PartnerOrganizationApp = {
  id: string;
  name: string;
  slug: string;
  type: string;
  description?: string;
  module?: string;
  badge_label?: string;
  group?: string;
  icon?: string;
  link?: string;
  is_active: boolean;
};

export type PartnerOrganizationProfile = {
  id: number;
  tagline?: string;
  summary?: string;
  location?: string;
  website?: string;
  public_fields?: Record<string, unknown>;
};

export type PartnerRoleAssignment = {
  id: number;
  user: string | number;
  scope_type: string;
  scope_id: string;
  role_detail?: {
    id: number;
    name: string;
    description?: string;
  };
};

export type PartnerInvite = {
  id: string;
  code: string;
  label?: string;
  created_by_name?: string | null;
  max_uses?: number | null;
  use_count: number;
  expires_at?: string | null;
  is_active: boolean;
  membership_role: string;
  is_expired?: boolean;
  has_uses_remaining?: boolean;
  is_redeemable?: boolean;
  created_at: string;
};

export type PartnerOnboardingConfig = {
  rules_text?: string;
  welcome_message?: string;
  default_channel_ids: string[];
  role_options: Array<{ id: string; name: string; description?: string }>;
};

export type PartnerOnboardingProgress = {
  id: number;
  invite?: string | null;
  invite_code?: string | null;
  rules_accepted_at?: string | null;
  selected_role_ids: string[];
  selected_channel_ids: string[];
  onboarding_snapshot?: Record<string, unknown>;
  completed_at?: string | null;
};

export type PartnerOnboardingState = {
  config: PartnerOnboardingConfig;
  progress: PartnerOnboardingProgress;
};

export type PartnerMemberDirectoryEntry = {
  user_id: string;
  display_name?: string | null;
  username?: string | null;
  avatar_url?: string | null;
  membership_status: string;
  membership_role: string;
  role_names: string[];
  is_muted: boolean;
  is_banned: boolean;
  timed_out_until?: string | null;
  joined_at?: string | null;
};

export type PartnerModerationAction = {
  id: number;
  user: string | number;
  user_name?: string | null;
  actor?: string | number | null;
  actor_name?: string | null;
  action_type: string;
  reason?: string;
  expires_at?: string | null;
  created_at: string;
};

export type PartnerScreeningSettings = {
  enabled: boolean;
  require_rules_acceptance: boolean;
  rules_text?: string;
  screening_questions: Array<Record<string, unknown> | string>;
};

export type PartnerNotificationPreferences = {
  server_notification_level: "all" | "mentions" | "none";
  channel_notifications: Array<{
    channel_id: string;
    channel_name: string;
    notification_level: "all" | "mentions" | "none";
  }>;
};

export type PartnerPublicHubData = {
  partner_id: string;
  name: string;
  slug: string;
  description?: string;
  avatar_url?: string;
  profile: Record<string, unknown>;
  landing_builder: Record<string, unknown>;
  public_metrics: {
    active_members: number;
    channels: number;
    apps: number;
    categories: number;
  };
  apps: Array<{
    id: string;
    name: string;
    type: string;
    description?: string;
    module?: string;
    badge_label?: string;
    link?: string;
  }>;
};

export type PartnerDifferentiatorInsights = {
  onboarding_funnel: {
    started: number;
    completed: number;
    completion_rate: number;
  };
  team_activation: {
    active_members: number;
    assigned_members: number;
    assignment_rate: number;
  };
  role_health: Array<{ role: string; count: number }>;
  app_adoption: Array<{ app: string; count: number }>;
};

export type PartnerTeamStructure = {
  owner_id: string;
  lanes: Array<{
    key: string;
    members: Array<{
      user_id: string;
      display_name?: string | null;
      status: string;
      role_assignments: string[];
      is_banned: boolean;
    }>;
  }>;
};

export type PartnerAutomationRecipe = {
  key: string;
  title: string;
  description: string;
  trigger: string;
  conditions: Record<string, unknown>;
  actions: Array<Record<string, unknown>>;
  fits: boolean;
};

export type PartnerExperienceTemplate = {
  key: string;
  title: string;
  description: string;
  fits: boolean;
  accent: string;
};

export type PartnerServerShellData = {
  partner: PartnerDetail;
  categories: PartnerServerCategory[];
  channels: PartnerServerChannel[];
  apps: PartnerOrganizationApp[];
  organizationProfile: PartnerOrganizationProfile | null;
  roleAssignments: PartnerRoleAssignment[];
};

export async function fetchDashboardOverview() {
  const { data } = await apiClient.get("/dashboard/overview/");
  return data;
}

export async function fetchLiveMetrics() {
  const { data } = await apiClient.get("/live/metrics/");
  return data;
}

export async function fetchMicroAnalytics() {
  const { data } = await apiClient.get("/micro/apps/");
  return data.micro_apps ?? [];
}

export async function fetchActivityFeed(params?: Record<string, string | number>) {
  const { data } = await apiClient.get("/activity/stream/", { params });
  return data;
}

export async function fetchModelRegistry() {
  const { data } = await apiClient.get("/registry/models/");
  return data;
}

export type ModelListParams = {
  page?: number;
  per_page?: number;
  search?: string;
  ordering?: string;
  include_deleted?: boolean;
  filters?: Record<string, string | number>;
};

export async function fetchModelData(
  app_label: string,
  model_name: string,
  params: ModelListParams = {}
) {
  const queryParams = {
    page: params.page ?? 1,
    per_page: params.per_page ?? 25,
    search: params.search,
    ordering: params.ordering,
    include_deleted: params.include_deleted ? "true" : "false",
    ...params.filters,
  };
  const { data } = await apiClient.get(`/crud/${app_label}/${model_name}/`, {
    params: queryParams,
  });
  return data;
}

export async function performBulkAction(
  app_label: string,
  model_name: string,
  action: "soft_delete" | "restore" | "hard_delete",
  ids: (string | number)[]
) {
  const { data } = await apiClient.post(`/crud/${app_label}/${model_name}/`, {
    action,
    ids,
  });
  return data;
}

export async function fetchMonitoringAlerts() {
  const { data } = await apiClient.get("/monitoring/alerts/");
  return data.alerts ?? [];
}

export async function fetchPerformanceInsights() {
  const { data } = await apiClient.get("/monitoring/performance/");
  return data.insights ?? {};
}

export async function updateModelInstance(
  app_label: string,
  model_name: string,
  pk: string | number,
  payload: Record<string, unknown>
) {
  const { data } = await apiClient.patch(`/crud/${app_label}/${model_name}/${pk}/`, payload);
  return data;
}

export async function deleteModelInstance(
  app_label: string,
  model_name: string,
  pk: string | number,
  confirm = false
) {
  const { data } = await apiClient.delete(`/crud/${app_label}/${model_name}/${pk}/`, {
    params: { confirm },
  });
  return data;
}

export async function fetchAccessOverview() {
  const { data } = await apiClient.get("/access/overview/");
  return data;
}

export async function fetchRoles() {
  const { data } = await apiClient.get("/roles/");
  return data;
}

export async function fetchRoleAssignments() {
  const { data } = await apiClient.get("/roles/assignments/");
  return data;
}

export async function createRoleAssignment(payload: {
  user: number;
  role: number;
  is_active?: boolean;
}) {
  const { data } = await apiClient.post("/roles/assignments/", payload);
  return data;
}

export async function updateRoleAssignment(id: number, payload: { is_active?: boolean }) {
  const { data } = await apiClient.patch(`/roles/assignments/${id}/`, payload);
  return data;
}

export async function fetchSuspiciousFlags(params?: Record<string, string>) {
  const { data } = await apiClient.get("/activity/flags/", { params });
  return data;
}

export async function patchSuspiciousFlag(id: number, resolved: boolean) {
  const { data } = await apiClient.patch("/activity/flags/", { id, resolved });
  return data;
}

export async function logAdminAction(payload: {
  action_type: string;
  target_app?: string;
  target_model?: string;
  target_pk?: string;
  severity?: string;
  metadata?: Record<string, unknown>;
}) {
  const { data } = await apiClient.post("/audit/actions/", payload);
  return data;
}

export async function fetchPartnerServers() {
  const { data } = await platformApiClient.get<PaginatedResponse<PartnerSummary> | PartnerSummary[]>("/partners/");
  return extractList<PartnerSummary>(data);
}

export async function fetchPartnerServerShell(partnerId: string) {
  const [partnerResponse, layoutResponse, appsResponse, profileResponse, roleAssignmentsResponse] =
    await Promise.all([
      platformApiClient.get<PartnerDetail>(`/partners/${partnerId}/`),
      platformApiClient.get<{ categories?: PartnerServerCategory[]; channels?: PartnerServerChannel[] }>(
        `/partners/${partnerId}/server-layout/`
      ),
      platformApiClient.get<{ apps?: PartnerOrganizationApp[] }>(`/partners/${partnerId}/organization-apps/`),
      platformApiClient
        .get<PartnerOrganizationProfile>(`/partners/${partnerId}/organization-profile/`)
        .catch((error) => {
          if (error?.response?.status === 403 || error?.response?.status === 404) {
            return { data: null };
          }
          throw error;
        }),
      platformApiClient
        .get<PartnerRoleAssignment[] | PaginatedResponse<PartnerRoleAssignment>>(
          `/partners/${partnerId}/role-assignments/`
        )
        .catch((error) => {
          if (error?.response?.status === 403 || error?.response?.status === 404) {
            return { data: [] };
          }
          throw error;
        }),
    ]);

  return {
    partner: partnerResponse.data,
    categories: layoutResponse.data.categories ?? [],
    channels: layoutResponse.data.channels ?? [],
    apps: appsResponse.data.apps ?? [],
    organizationProfile: profileResponse.data,
    roleAssignments: extractList<PartnerRoleAssignment>(roleAssignmentsResponse.data),
  } satisfies PartnerServerShellData;
}

export async function fetchPartnerInvites(partnerId: string) {
  const { data } = await platformApiClient.get<PartnerInvite[]>(`/partners/${partnerId}/invites/`);
  return extractList<PartnerInvite>(data as PartnerInvite[]);
}

export async function createPartnerInvite(
  partnerId: string,
  payload: {
    label?: string;
    max_uses?: number | null;
    expires_at?: string | null;
    membership_role?: string;
  }
) {
  const { data } = await platformApiClient.post<PartnerInvite>(`/partners/${partnerId}/invites/`, payload);
  return data;
}

export async function updatePartnerInvite(
  partnerId: string,
  inviteId: string,
  payload: Record<string, unknown>
) {
  const { data } = await platformApiClient.patch<PartnerInvite>(`/partners/${partnerId}/invites/${inviteId}/`, payload);
  return data;
}

export async function disablePartnerInvite(partnerId: string, inviteId: string) {
  await platformApiClient.delete(`/partners/${partnerId}/invites/${inviteId}/`);
}

export async function fetchPartnerOnboarding(partnerId: string) {
  const { data } = await platformApiClient.get<PartnerOnboardingState>(`/partners/${partnerId}/onboarding/`);
  return data;
}

export async function completePartnerOnboarding(
  partnerId: string,
  payload: {
    accept_rules?: boolean;
    role_ids?: string[];
    channel_ids?: string[];
  }
) {
  const { data } = await platformApiClient.post<PartnerOnboardingProgress>(
    `/partners/${partnerId}/onboarding/complete/`,
    payload
  );
  return data;
}

export async function fetchPartnerMembers(partnerId: string) {
  const { data } = await platformApiClient.get<{ members?: PartnerMemberDirectoryEntry[] }>(`/partners/${partnerId}/members/`);
  return data.members ?? [];
}

export async function fetchPartnerModerationActions(partnerId: string) {
  const { data } = await platformApiClient.get<PartnerModerationAction[]>(`/partners/${partnerId}/moderation-actions/`);
  return extractList<PartnerModerationAction>(data as PartnerModerationAction[]);
}

export async function moderatePartnerMember(
  partnerId: string,
  memberUserId: string,
  payload: {
    action: "mute" | "unmute" | "timeout" | "kick" | "ban" | "unban";
    reason?: string;
    expires_at?: string | null;
  }
) {
  const { data } = await platformApiClient.post<PartnerModerationAction>(
    `/partners/${partnerId}/members/${memberUserId}/moderate/`,
    payload
  );
  return data;
}

export async function fetchPartnerScreening(partnerId: string) {
  const { data } = await platformApiClient.get<PartnerScreeningSettings>(`/partners/${partnerId}/screening/`);
  return data;
}

export async function updatePartnerScreening(
  partnerId: string,
  payload: Partial<PartnerScreeningSettings>
) {
  const { data } = await platformApiClient.patch<PartnerScreeningSettings>(`/partners/${partnerId}/screening/`, payload);
  return data;
}

export async function fetchPartnerNotificationPreferences(partnerId: string) {
  const { data } = await platformApiClient.get<PartnerNotificationPreferences>(
    `/partners/${partnerId}/notification-preferences/`
  );
  return data;
}

export async function updatePartnerNotificationPreferences(
  partnerId: string,
  payload: Partial<PartnerNotificationPreferences>
) {
  const { data } = await platformApiClient.patch<PartnerNotificationPreferences>(
    `/partners/${partnerId}/notification-preferences/`,
    payload
  );
  return data;
}

export async function fetchPartnerPublicHub(partnerId: string) {
  const { data } = await platformApiClient.get<PartnerPublicHubData>(`/partners/${partnerId}/public-hub/`);
  return data;
}

export async function fetchPartnerDifferentiatorInsights(partnerId: string) {
  const { data } = await platformApiClient.get<PartnerDifferentiatorInsights>(
    `/partners/${partnerId}/differentiator-insights/`
  );
  return data;
}

export async function fetchPartnerTeamStructure(partnerId: string) {
  const { data } = await platformApiClient.get<PartnerTeamStructure>(`/partners/${partnerId}/team-structure/`);
  return data;
}

export async function fetchPartnerAutomationRecipes(partnerId: string) {
  const { data } = await platformApiClient.get<{ recipes?: PartnerAutomationRecipe[] }>(
    `/partners/${partnerId}/automation-recipes/`
  );
  return data.recipes ?? [];
}

export async function applyPartnerAutomationRecipe(partnerId: string, recipeKey: string) {
  const { data } = await platformApiClient.post(`/partners/${partnerId}/automation-recipes/apply/`, {
    recipe_key: recipeKey,
  });
  return data;
}

export async function fetchPartnerExperienceTemplates(partnerId: string) {
  const { data } = await platformApiClient.get<{ templates?: PartnerExperienceTemplate[] }>(
    `/partners/${partnerId}/experience-templates/`
  );
  return data.templates ?? [];
}
