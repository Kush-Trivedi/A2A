export interface UserProfile {
  tenant_id: string;
  user_id: string;
  actor_id: string;
  email: string;
  display_name: string;
  roles: string[];
}

export interface AgentSummary {
  id: string;
  display_name: string;
  description: string;
}

export interface SessionSummary {
  id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
}

export interface Refusal {
  type: string;
  message: string;
  agent_key: string;
  team_key: string;
  team_name: string;
  contact_email: string;
}

export interface ArtifactPart {
  kind: string;
  text?: string;
  data?: Record<string, unknown>;
  url?: string;
  filename?: string;
  media_type?: string;
}

export interface Artifact {
  artifact_id: string;
  name: string;
  description: string;
  parts: ArtifactPart[];
}

export interface ChatMessage {
  id: string;
  role: string;
  content: string;
  created_at: string;
  metadata: Record<string, unknown>;
  feedback: string | null;
}

export type StreamEvent =
  | { event: "meta"; data: { session_id: string; agent_id: string; sources: string[] } }
  | { event: "token"; data: { text: string } }
  | { event: "artifact"; data: Artifact }
  | { event: "state"; data: { state: string } }
  | { event: "refusal"; data: Refusal }
  | { event: "error"; data: { code: string; message: string } }
  | { event: "done"; data: { session_id: string; message_id: string } };
