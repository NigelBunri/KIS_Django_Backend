import axios from "axios";

const ADMIN_API_BASE =
  process.env.NEXT_PUBLIC_ADMIN_API_BASE || "http://localhost:8000/control/admin";

export const apiClient = axios.create({
  baseURL: ADMIN_API_BASE,
  timeout: 10000,
  withCredentials: true,
});

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
