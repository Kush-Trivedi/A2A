import { API_ENDPOINTS, apiRequest } from "./client";

// ── Shared envelope ──────────────────────────────────────────────────────────
// Every admin endpoint wraps its payload as { data, message? }.
// NOTE: types/admin.ts is intentionally empty; the admin models are declared
// and exported here so this module is self-contained.

export interface ApiEnvelope<T> {
  data: T;
  message?: string;
}

// ── Models ───────────────────────────────────────────────────────────────────

export interface TeamModel {
  id: string;
  key: string;
  name: string;
  description: string;
  contact_email: string;
  created_at: string;
  updated_at: string;
}

export type AgentStatus = "registered" | "active" | "disabled";

export interface AgentModel {
  id: string;
  agent_key: string;
  display_name: string;
  description: string;
  card_url: string;
  version: string;
  status: AgentStatus;
  permission: string;
  allowed_roles: string[];
  created_at: string;
  updated_at: string;
}

export interface ConnectionModel {
  id: string;
  team_key: string;
  connection_key: string;
  source_type: string;
  description: string;
  config: Record<string, string>;
  created_at: string;
  updated_at: string;
}

export interface TeamTokenModel {
  id: string;
  label: string | null;
  masked: string;
  revoked: boolean;
  created_at: string;
  last_used_at: string | null;
}

export interface IssuedTeamToken {
  team_key: string;
  /** Raw secret — returned exactly once at issue time; never retrievable again. */
  token: string;
}

export interface MyTeamEntry {
  team_key: string;
  registered: boolean;
  team: TeamModel | null;
}

export interface MyTeamSummary {
  admin_teams: string[];
  is_global_admin: boolean;
  teams: MyTeamEntry[];
}

export interface RegisterTeamBody {
  key: string;
  name: string;
  description: string;
  contact_email: string;
}

// ── Requests ─────────────────────────────────────────────────────────────────

export async function getMyTeam(): Promise<MyTeamSummary> {
  const body = await apiRequest<ApiEnvelope<MyTeamSummary>>(
    API_ENDPOINTS.adminMyTeam,
  );
  return body.data;
}

export async function listTeams(): Promise<TeamModel[]> {
  const body = await apiRequest<ApiEnvelope<TeamModel[]>>(
    API_ENDPOINTS.adminTeams,
  );
  return body.data;
}

export async function registerTeam(body: RegisterTeamBody): Promise<TeamModel> {
  const response = await apiRequest<ApiEnvelope<TeamModel>>(
    API_ENDPOINTS.adminTeams,
    {
      method: "POST",
      body: JSON.stringify(body),
    },
  );
  return response.data;
}

export async function listTeamTokens(
  teamKey: string,
): Promise<TeamTokenModel[]> {
  const body = await apiRequest<ApiEnvelope<TeamTokenModel[]>>(
    API_ENDPOINTS.adminTeamTokens(teamKey),
  );
  return body.data;
}

export async function issueTeamToken(teamKey: string): Promise<IssuedTeamToken> {
  const body = await apiRequest<ApiEnvelope<IssuedTeamToken>>(
    API_ENDPOINTS.adminTeamTokens(teamKey),
    { method: "POST" },
  );
  return body.data;
}

export async function revokeTeamTokens(
  teamKey: string,
): Promise<{ revoked: number }> {
  const body = await apiRequest<ApiEnvelope<{ revoked: number }>>(
    API_ENDPOINTS.adminTeamTokens(teamKey),
    { method: "DELETE" },
  );
  return body.data;
}

export async function listAgents(): Promise<AgentModel[]> {
  const body = await apiRequest<ApiEnvelope<AgentModel[]>>(
    API_ENDPOINTS.adminAgents,
  );
  return body.data;
}

export async function setAgentStatus(
  agentKey: string,
  status: string,
): Promise<AgentModel> {
  const body = await apiRequest<ApiEnvelope<AgentModel>>(
    API_ENDPOINTS.adminAgentStatus(agentKey),
    {
      method: "PATCH",
      body: JSON.stringify({ status }),
    },
  );
  return body.data;
}

export async function listConnections(
  teamKey?: string,
): Promise<ConnectionModel[]> {
  const query = teamKey ? `?team_key=${encodeURIComponent(teamKey)}` : "";
  const body = await apiRequest<ApiEnvelope<ConnectionModel[]>>(
    `${API_ENDPOINTS.adminConnections}${query}`,
  );
  return body.data;
}
