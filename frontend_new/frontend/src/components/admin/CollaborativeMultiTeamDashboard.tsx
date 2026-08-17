import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import { API_ENDPOINTS, ApiError, apiRequest } from "../../api/client";

// ════════════════════════════════════════════════════════════════════════════
// Team Operations — single-file admin dashboard.
//
// The signed-in user's team comes from GET /admin/my-team (Entra group mapped
// server-side); there is no team selector. "My Team" manages registration,
// service tokens, the shared agent fleet, and data connections. "Global
// Directory" is a searchable, read-only view across every registered team.
// ════════════════════════════════════════════════════════════════════════════

// ─── Types (local by design — types/admin.ts is intentionally empty) ─────────

interface ApiEnvelope<T> {
  data: T;
  message?: string;
}

interface TeamModel {
  id: string;
  key: string;
  name: string;
  description: string;
  contact_email: string;
  created_at: string;
  updated_at: string;
}

type AgentStatus = "registered" | "active" | "disabled";

interface AgentModel {
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

interface ConnectionModel {
  id: string;
  team_key: string;
  connection_key: string;
  source_type: string;
  description: string;
  config: Record<string, string>;
  created_at: string;
  updated_at: string;
}

interface TeamTokenModel {
  id: string;
  label: string | null;
  masked: string;
  revoked: boolean;
  created_at: string;
  last_used_at: string | null;
}

interface IssuedTeamToken {
  team_key: string;
  token: string;
}

interface MyTeamEntry {
  team_key: string;
  registered: boolean;
  team: TeamModel | null;
}

interface MyTeamSummary {
  admin_teams: string[];
  is_global_admin: boolean;
  teams: MyTeamEntry[];
}

interface SectionErrors {
  myTeam?: string;
  teams?: string;
  agents?: string;
  tokens?: string;
  connections?: string;
}

type TabKey = "my-team" | "directory";

type ToastTone = "success" | "error";

interface ToastState {
  id: number;
  text: string;
  tone: ToastTone;
}

interface CollaborativeMultiTeamDashboardProps {
  /** Optional hint only — the real value is derived from GET /admin/my-team. */
  canManage?: boolean;
}

// ─── API (envelope unwrap over the shared client) ────────────────────────────

async function unwrap<T>(path: string, init?: RequestInit): Promise<T> {
  const body = await apiRequest<ApiEnvelope<T>>(path, init);
  return body.data;
}

const api = {
  myTeam: () => unwrap<MyTeamSummary>(API_ENDPOINTS.adminMyTeam),
  teams: () => unwrap<TeamModel[]>(API_ENDPOINTS.adminTeams),
  registerTeam: (body: {
    key: string;
    name: string;
    description: string;
    contact_email: string;
  }) =>
    unwrap<TeamModel>(API_ENDPOINTS.adminTeams, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  tokens: (teamKey: string) =>
    unwrap<TeamTokenModel[]>(API_ENDPOINTS.adminTeamTokens(teamKey)),
  issueToken: (teamKey: string) =>
    unwrap<IssuedTeamToken>(API_ENDPOINTS.adminTeamTokens(teamKey), {
      method: "POST",
    }),
  revokeTokens: (teamKey: string) =>
    unwrap<{ revoked: number }>(API_ENDPOINTS.adminTeamTokens(teamKey), {
      method: "DELETE",
    }),
  agents: () => unwrap<AgentModel[]>(API_ENDPOINTS.adminAgents),
  setAgentStatus: (agentKey: string, status: AgentStatus) =>
    unwrap<AgentModel>(API_ENDPOINTS.adminAgentStatus(agentKey), {
      method: "PATCH",
      body: JSON.stringify({ status }),
    }),
  connections: (teamKey?: string) =>
    unwrap<ConnectionModel[]>(
      `${API_ENDPOINTS.adminConnections}${
        teamKey ? `?team_key=${encodeURIComponent(teamKey)}` : ""
      }`,
    ),
};

function errorMessage(caught: unknown, fallback: string): string {
  if (caught instanceof ApiError) return caught.message;
  if (caught instanceof Error) return caught.message;
  return fallback;
}

// ─── Constants ───────────────────────────────────────────────────────────────

const GRADIENT_PLUM = "bg-[linear-gradient(135deg,#533A71_0%,#7D5BA6_100%)]";
const GRADIENT_BLUE = "bg-[linear-gradient(135deg,#1F5BA8_0%,#4A90D9_100%)]";
const GRADIENT_TEAL = "bg-[linear-gradient(135deg,#008080_0%,#30D5C8_100%)]";
const GRADIENT_AMBER = "bg-[linear-gradient(135deg,#F28500_0%,#FF7F50_100%)]";

const CARD_SHADOW = "shadow-[0_4px_24px_rgba(75,28,90,0.12)]";

const STATUS_BADGE: Record<AgentStatus, string> = {
  active: "border border-[#0F7A4F] bg-[#E6F4EE] text-[#0A4F33]",
  registered: "border border-[#A26408] bg-[#FCF1DC] text-[#6B4205]",
  disabled: "border border-[#B0202A] bg-[#FBE9E9] text-[#75151C]",
};

const STATUS_LABEL: Record<AgentStatus, string> = {
  active: "Active",
  registered: "Registered",
  disabled: "Disabled",
};

const STATUS_COLOR: Record<AgentStatus, string> = {
  active: "#0F7A4F",
  registered: "#A26408",
  disabled: "#B0202A",
};

const SOURCE_CHIP: Record<string, string> = {
  blob: "border-[#1F5BA8] bg-[#E8F0FB] text-[#143E73]",
  sharepoint: "border-[#008080] bg-[#E0F5F5] text-[#005555]",
};

const INPUT_CLASS =
  "w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink placeholder:text-subtle outline-none transition focus:border-brandMid";

const BUTTON_PRIMARY =
  "rounded-lg bg-brand px-3 py-1.5 text-xs font-semibold text-white shadow-sm transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50";

const BUTTON_QUIET =
  "rounded-lg border border-line bg-panel px-3 py-1.5 text-xs font-semibold text-ink transition hover:bg-surface disabled:cursor-not-allowed disabled:opacity-50";

const BUTTON_DANGER =
  "rounded-lg border border-dangerLine bg-dangerBg px-3 py-1.5 text-xs font-semibold text-dangerText transition hover:opacity-85 disabled:cursor-not-allowed disabled:opacity-50";

// ─── Utilities ───────────────────────────────────────────────────────────────

function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

// ─── Inline icons (stroke inherits currentColor; no icon library) ────────────

function svgProps(className: string) {
  return {
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 2,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    className,
    "aria-hidden": true,
  };
}

function IconRefresh({ className = "h-4 w-4" }: { className?: string }) {
  return (
    <svg {...svgProps(className)}>
      <path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8" />
      <path d="M21 3v5h-5" />
      <path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16" />
      <path d="M3 21v-5h5" />
    </svg>
  );
}

function IconSearch({ className = "h-4 w-4" }: { className?: string }) {
  return (
    <svg {...svgProps(className)}>
      <circle cx="11" cy="11" r="8" />
      <path d="m21 21-4.35-4.35" />
    </svg>
  );
}

function IconEye({ className = "h-4 w-4" }: { className?: string }) {
  return (
    <svg {...svgProps(className)}>
      <path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

function IconEyeOff({ className = "h-4 w-4" }: { className?: string }) {
  return (
    <svg {...svgProps(className)}>
      <path d="M9.88 9.88a3 3 0 1 0 4.24 4.24" />
      <path d="M10.73 5.08A10.43 10.43 0 0 1 12 5c7 0 10 7 10 7a13.16 13.16 0 0 1-1.67 2.68" />
      <path d="M6.61 6.61A13.526 13.526 0 0 0 2 12s3 7 10 7a9.74 9.74 0 0 0 5.39-1.61" />
      <path d="m2 2 20 20" />
    </svg>
  );
}

function IconCopy({ className = "h-4 w-4" }: { className?: string }) {
  return (
    <svg {...svgProps(className)}>
      <rect x="9" y="9" width="13" height="13" rx="2" />
      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
    </svg>
  );
}

function IconCheck({ className = "h-4 w-4" }: { className?: string }) {
  return (
    <svg {...svgProps(className)}>
      <path d="M20 6 9 17l-5-5" />
    </svg>
  );
}

function IconAlert({ className = "h-4 w-4" }: { className?: string }) {
  return (
    <svg {...svgProps(className)}>
      <path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z" />
      <path d="M12 9v4" />
      <path d="M12 17h.01" />
    </svg>
  );
}

function IconKey({ className = "h-5 w-5" }: { className?: string }) {
  return (
    <svg {...svgProps(className)}>
      <path d="m21 2-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777Zm0 0L15.5 7.5m0 0 3 3L22 7l-3-3m-3.5 3.5L19 4" />
    </svg>
  );
}

function IconBot({ className = "h-5 w-5" }: { className?: string }) {
  return (
    <svg {...svgProps(className)}>
      <rect x="3" y="11" width="18" height="10" rx="2" />
      <circle cx="12" cy="5" r="2" />
      <path d="M12 7v4" />
      <path d="M8 16h.01" />
      <path d="M16 16h.01" />
    </svg>
  );
}

function IconPlug({ className = "h-5 w-5" }: { className?: string }) {
  return (
    <svg {...svgProps(className)}>
      <path d="M12 22v-5" />
      <path d="M9 8V2" />
      <path d="M15 8V2" />
      <path d="M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z" />
    </svg>
  );
}

function IconUsers({ className = "h-5 w-5" }: { className?: string }) {
  return (
    <svg {...svgProps(className)}>
      <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
      <circle cx="9" cy="7" r="4" />
      <path d="M22 21v-2a4 4 0 0 0-3-3.87" />
      <path d="M16 3.13a4 4 0 0 1 0 7.75" />
    </svg>
  );
}

function IconShield({ className = "h-5 w-5" }: { className?: string }) {
  return (
    <svg {...svgProps(className)}>
      <path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1 1 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1Z" />
    </svg>
  );
}

function IconMail({ className = "h-4 w-4" }: { className?: string }) {
  return (
    <svg {...svgProps(className)}>
      <rect x="2" y="4" width="20" height="16" rx="2" />
      <path d="m22 7-10 5L2 7" />
    </svg>
  );
}

// ─── Primitives ──────────────────────────────────────────────────────────────

function ProgressRing({ value }: { value: number }) {
  const safeValue = Math.max(
    0,
    Math.min(100, Number.isFinite(value) ? value : 0),
  );
  const r = 28;
  const circ = 2 * Math.PI * r;
  const dash = (safeValue / 100) * circ;
  return (
    <div className="relative h-20 w-20 shrink-0">
      <svg
        className="h-20 w-20 -rotate-90"
        viewBox="0 0 72 72"
        aria-hidden="true"
      >
        <circle
          cx="36"
          cy="36"
          r={r}
          fill="none"
          stroke="rgba(255,255,255,0.20)"
          strokeWidth="7"
        />
        <circle
          cx="36"
          cy="36"
          r={r}
          fill="none"
          stroke="rgba(255,255,255,0.85)"
          strokeWidth="7"
          strokeLinecap="round"
          strokeDasharray={`${dash} ${circ}`}
        />
      </svg>
      <span className="absolute inset-0 flex items-center justify-center text-sm font-semibold">
        {Math.round(safeValue)}%
      </span>
    </div>
  );
}

function MetricCard({
  label,
  value,
  caption,
  gradient,
  icon,
  percent,
}: {
  label: string;
  value: string;
  caption: string;
  gradient: string;
  icon: ReactNode;
  percent?: number;
}) {
  return (
    <article
      className={`relative min-h-[9.5rem] overflow-hidden rounded-xl p-5 text-white shadow-[0_-6px_20px_rgba(0,0,0,0.15),0_2px_4px_rgba(0,0,0,0.08)] ${gradient}`}
    >
      <div className="absolute inset-0 bg-[linear-gradient(120deg,rgba(255,255,255,0.16),transparent_42%)]" />
      <div className="relative flex h-full items-center justify-between gap-4">
        <div className="min-w-0">
          <div className="mb-5 flex h-9 w-9 items-center justify-center rounded-lg bg-white/18 ring-1 ring-white/20">
            {icon}
          </div>
          <p className="text-sm font-medium text-white/82">{label}</p>
          <p className="mt-1 text-3xl font-semibold tracking-tight">{value}</p>
          <p className="mt-1 text-xs text-white/72">{caption}</p>
        </div>
        {percent != null ? <ProgressRing value={percent} /> : null}
      </div>
    </article>
  );
}

function ChartCard({
  title,
  subtitle,
  action,
  children,
}: {
  title: string;
  subtitle: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section
      className={`rounded-xl border border-line bg-panel p-4 ${CARD_SHADOW}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-ink">{title}</p>
          <p className="mt-0.5 text-[12px] text-muted">{subtitle}</p>
        </div>
        {action}
      </div>
      <div className="mt-3">{children}</div>
    </section>
  );
}

function StatusBadge({ status }: { status: AgentStatus }) {
  const cls = STATUS_BADGE[status] ?? STATUS_BADGE.registered;
  const label = STATUS_LABEL[status] ?? status;
  return (
    <span
      className={`inline-flex h-6 items-center justify-center rounded-full px-2.5 text-[11px] font-semibold leading-none ${cls}`}
    >
      {label}
    </span>
  );
}

function SourceChip({ type }: { type: string }) {
  const cls =
    SOURCE_CHIP[type.toLowerCase()] ??
    "border-[#CACAD0] bg-[#F2F2F4] text-[#3A3A42]";
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-semibold capitalize ${cls}`}
    >
      {type}
    </span>
  );
}

function EmptyState({ title, hint }: { title: string; hint: string }) {
  return (
    <div className="rounded-lg border border-dashed border-line bg-surface px-4 py-8 text-center">
      <p className="text-sm font-semibold text-ink">{title}</p>
      <p className="mt-1 text-xs text-muted">{hint}</p>
    </div>
  );
}

function InlineError({ text }: { text: string }) {
  return (
    <div className="flex items-center gap-2 rounded-lg border border-dangerLine bg-dangerBg px-3 py-2 text-xs text-dangerText">
      <IconAlert className="h-3.5 w-3.5 shrink-0" />
      <span className="min-w-0">{text}</span>
    </div>
  );
}

function SkeletonBar({ className }: { className: string }) {
  return (
    <div
      className={`animate-pulse rounded-lg border border-line bg-surface ${className}`}
      aria-hidden="true"
    />
  );
}

// ─── Donut chart (hand-rolled, stroke-dasharray segments) ────────────────────

interface DonutSegment {
  label: string;
  color: string;
  count: number;
}

function DonutChart({
  segments,
  centerLabel,
}: {
  segments: DonutSegment[];
  centerLabel: string;
}) {
  const total = segments.reduce((sum, seg) => sum + seg.count, 0);
  const r = 44;
  const circ = 2 * Math.PI * r;
  const visible = segments.filter((seg) => seg.count > 0);
  // 2px surface gap between adjacent segments so hue is never the only cue.
  const gap = visible.length > 1 ? 2.5 : 0;
  let cumulative = 0;

  return (
    <div className="flex items-center gap-4">
      <div className="relative h-32 w-32 shrink-0">
        <svg
          viewBox="0 0 120 120"
          className="h-32 w-32"
          role="img"
          aria-label={segments
            .map((seg) => `${seg.label}: ${seg.count}`)
            .join(", ")}
        >
          {total === 0 ? (
            <circle
              cx="60"
              cy="60"
              r={r}
              fill="none"
              stroke="currentColor"
              strokeOpacity="0.15"
              strokeWidth="14"
              className="text-muted"
            />
          ) : (
            <g transform="rotate(-90 60 60)">
              {visible.map((seg) => {
                const fraction = seg.count / total;
                const arc = Math.max(0.5, fraction * circ - gap);
                const offset = -(cumulative * circ + gap / 2);
                cumulative += fraction;
                return (
                  <circle
                    key={seg.label}
                    cx="60"
                    cy="60"
                    r={r}
                    fill="none"
                    stroke={seg.color}
                    strokeWidth="14"
                    strokeDasharray={`${arc} ${circ - arc}`}
                    strokeDashoffset={offset}
                  />
                );
              })}
            </g>
          )}
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className="text-xl font-semibold text-ink">{total}</span>
          <span className="text-[10px] font-semibold uppercase tracking-wide text-subtle">
            {centerLabel}
          </span>
        </div>
      </div>

      <ul className="min-w-0 flex-1 space-y-2">
        {segments.map((seg) => {
          const pct = total > 0 ? Math.round((seg.count / total) * 100) : 0;
          return (
            <li
              key={seg.label}
              className="flex items-center gap-2 text-[12px]"
            >
              <span
                className="h-2.5 w-2.5 shrink-0 rounded-full"
                style={{ backgroundColor: seg.color }}
              />
              <span className="min-w-0 flex-1 truncate text-muted">
                {seg.label}
              </span>
              <span className="font-semibold text-ink">{seg.count}</span>
              <span className="w-9 text-right text-subtle">{pct}%</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

// ─── Token row ───────────────────────────────────────────────────────────────

function TokenRow({ token }: { token: TeamTokenModel }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-line bg-surface p-3">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <p className="truncate text-sm font-medium text-ink">
            {token.label || "Service token"}
          </p>
          {token.revoked ? (
            <span className="inline-flex items-center rounded-full border border-dangerLine bg-dangerBg px-2 py-0.5 text-[11px] font-semibold text-dangerText">
              Revoked
            </span>
          ) : null}
        </div>
        <p className="mt-0.5 font-mono text-xs text-muted">{token.masked}</p>
      </div>
      <div className="shrink-0 text-right text-[11px] text-subtle">
        <p>Created {formatDate(token.created_at)}</p>
        <p className="mt-0.5">
          {token.last_used_at
            ? `Last used ${formatDate(token.last_used_at)}`
            : "Never used"}
        </p>
      </div>
    </div>
  );
}

// ─── Form field ──────────────────────────────────────────────────────────────

function Field({
  label,
  full,
  children,
}: {
  label: string;
  full?: boolean;
  children: ReactNode;
}) {
  return (
    <label className={`flex flex-col gap-1 ${full ? "sm:col-span-2" : ""}`}>
      <span className="text-xs font-semibold uppercase tracking-wide text-subtle">
        {label}
      </span>
      {children}
    </label>
  );
}

// ─── Onboarding pipeline step ────────────────────────────────────────────────

interface PipelineStep {
  label: string;
  hint: string;
  done: boolean;
}

function OnboardingPipeline({ steps }: { steps: PipelineStep[] }) {
  return (
    <ol className="space-y-0">
      {steps.map((step, index) => {
        const isLast = index === steps.length - 1;
        return (
          <li key={step.label} className="relative flex gap-3 pb-5 last:pb-0">
            {!isLast ? (
              <span
                className="absolute left-[11px] top-7 h-[calc(100%-1.75rem)] w-px bg-line"
                aria-hidden="true"
              />
            ) : null}
            <span
              className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold ${
                step.done
                  ? "bg-[#0F7A4F] text-white"
                  : "border border-line bg-surface text-muted"
              }`}
            >
              {step.done ? <IconCheck className="h-3.5 w-3.5" /> : index + 1}
            </span>
            <div className="min-w-0">
              <p
                className={`text-sm font-medium ${
                  step.done ? "text-ink" : "text-muted"
                }`}
              >
                {step.label}
              </p>
              <p className="mt-0.5 text-[12px] text-subtle">{step.hint}</p>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

// ═══ Main component ══════════════════════════════════════════════════════════

export default function CollaborativeMultiTeamDashboard({
  canManage: canManageHint,
}: CollaborativeMultiTeamDashboardProps = {}) {
  // ── Server state ──
  const [myTeam, setMyTeam] = useState<MyTeamSummary | null>(null);
  const [teams, setTeams] = useState<TeamModel[]>([]);
  const [agents, setAgents] = useState<AgentModel[]>([]);
  const [tokens, setTokens] = useState<TeamTokenModel[]>([]);
  const [myConnections, setMyConnections] = useState<ConnectionModel[]>([]);
  const [allConnections, setAllConnections] = useState<ConnectionModel[]>([]);
  const [sectionErrors, setSectionErrors] = useState<SectionErrors>({});

  // ── UI state ──
  const [isLoading, setIsLoading] = useState(true);
  const [hasLoaded, setHasLoaded] = useState(false);
  const [tab, setTab] = useState<TabKey>("my-team");
  const [search, setSearch] = useState("");
  const [toast, setToast] = useState<ToastState | null>(null);

  // ── Action state ──
  const [issuedToken, setIssuedToken] = useState<string | null>(null);
  const [showRawToken, setShowRawToken] = useState(false);
  const [copied, setCopied] = useState(false);
  const [isIssuing, setIsIssuing] = useState(false);
  const [isRevoking, setIsRevoking] = useState(false);
  const [revokeArmed, setRevokeArmed] = useState(false);
  const [busyAgentKey, setBusyAgentKey] = useState<string | null>(null);
  const [isRegistering, setIsRegistering] = useState(false);
  const [regName, setRegName] = useState("");
  const [regDescription, setRegDescription] = useState("");
  const [regEmail, setRegEmail] = useState("");

  const pushToast = useCallback((text: string, tone: ToastTone = "success") => {
    setToast({ id: Date.now(), text, tone });
  }, []);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), 4200);
    return () => window.clearTimeout(timer);
  }, [toast]);

  // ── Load everything in one pass; each source fails independently ──
  const refresh = useCallback(async () => {
    setIsLoading(true);
    const errors: SectionErrors = {};

    const myTeamPromise = api.myTeam();
    const tokensPromise = myTeamPromise.then((summary) => {
      const primary = summary.teams[0];
      return primary?.registered ? api.tokens(primary.team_key) : [];
    });
    const myConnectionsPromise = myTeamPromise.then((summary) => {
      const primary = summary.teams[0];
      return primary ? api.connections(primary.team_key) : [];
    });

    const [myTeamR, teamsR, agentsR, allConnR, tokensR, myConnR] =
      await Promise.allSettled([
        myTeamPromise,
        api.teams(),
        api.agents(),
        api.connections(),
        tokensPromise,
        myConnectionsPromise,
      ]);

    if (myTeamR.status === "fulfilled") {
      setMyTeam(myTeamR.value);
    } else {
      setMyTeam(null);
      errors.myTeam = errorMessage(
        myTeamR.reason,
        "Unable to resolve your team mapping.",
      );
    }

    if (teamsR.status === "fulfilled") {
      setTeams(teamsR.value);
    } else {
      errors.teams = errorMessage(
        teamsR.reason,
        "Unable to load the team directory.",
      );
    }

    if (agentsR.status === "fulfilled") {
      setAgents(agentsR.value);
    } else {
      errors.agents = errorMessage(
        agentsR.reason,
        "Unable to load the agent fleet.",
      );
    }

    if (allConnR.status === "fulfilled") {
      setAllConnections(allConnR.value);
    }

    // Only surface token/connection errors when they failed on their own —
    // if my-team itself failed, those chained fetches rejected for the same
    // reason and the banner already covers it.
    if (tokensR.status === "fulfilled") {
      setTokens(tokensR.value);
    } else if (myTeamR.status === "fulfilled") {
      errors.tokens = errorMessage(
        tokensR.reason,
        "Unable to load service tokens.",
      );
    }

    if (myConnR.status === "fulfilled") {
      setMyConnections(myConnR.value);
    } else if (myTeamR.status === "fulfilled") {
      errors.connections = errorMessage(
        myConnR.reason,
        "Unable to load connections.",
      );
    }

    setSectionErrors(errors);
    setHasLoaded(true);
    setIsLoading(false);
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // ── Derived ──
  const canManage = useMemo(() => {
    if (myTeam) return myTeam.admin_teams.length > 0 || myTeam.is_global_admin;
    return canManageHint ?? false;
  }, [myTeam, canManageHint]);

  useEffect(() => {
    if (hasLoaded && !canManage) setTab("directory");
  }, [hasLoaded, canManage]);

  const primaryEntry = myTeam?.teams[0];
  const primaryTeamKey = primaryEntry?.team_key ?? "";
  const primaryRegistered = primaryEntry?.registered ?? false;
  const primaryTeamName =
    primaryEntry?.team?.name ?? primaryEntry?.team_key ?? "";

  const subtitle = useMemo(() => {
    if (!hasLoaded) return "Resolving team mapping…";
    if (!myTeam) return "Team mapping unavailable";
    const names = myTeam.teams.map(
      (entry) => entry.team?.name ?? entry.team_key,
    );
    if (names.length === 0) {
      return myTeam.is_global_admin
        ? "Global administrator · directory-wide access"
        : "No team admin group mapped to your account";
    }
    const joined = names.join(" · ");
    return myTeam.is_global_admin
      ? `Operating as ${joined} · global admin`
      : `Operating as ${joined}`;
  }, [hasLoaded, myTeam]);

  const activeAgentCount = useMemo(
    () => agents.filter((agent) => agent.status === "active").length,
    [agents],
  );
  const activeTokenCount = useMemo(
    () => tokens.filter((token) => !token.revoked).length,
    [tokens],
  );

  const fleetSegments = useMemo<DonutSegment[]>(
    () =>
      (["active", "registered", "disabled"] as const).map((status) => ({
        label: STATUS_LABEL[status],
        color: STATUS_COLOR[status],
        count: agents.filter((agent) => agent.status === status).length,
      })),
    [agents],
  );

  const pipelineSteps = useMemo<PipelineStep[]>(
    () => [
      {
        label: "Team registered",
        done: primaryRegistered,
        hint: primaryRegistered
          ? `${primaryTeamName} is live in the directory`
          : "Submit the registration form to claim your team key",
      },
      {
        label: "Token issued",
        done: activeTokenCount > 0,
        hint:
          activeTokenCount > 0
            ? `${activeTokenCount} active service token${
                activeTokenCount === 1 ? "" : "s"
              }`
            : "Issue a service token so agents can authenticate",
      },
      {
        label: "Agents active",
        done: activeAgentCount > 0,
        hint:
          activeAgentCount > 0
            ? `${activeAgentCount} of ${agents.length} agents serving traffic`
            : "Activate at least one agent in the fleet",
      },
    ],
    [
      primaryRegistered,
      primaryTeamName,
      activeTokenCount,
      activeAgentCount,
      agents.length,
    ],
  );

  // ── Directory search (client-side across teams, agents, connections) ──
  const query = search.trim().toLowerCase();

  const directory = useMemo(() => {
    const matches = (...values: (string | null | undefined)[]) =>
      query === "" ||
      values.some((value) => (value ?? "").toLowerCase().includes(query));

    const teamCards = teams
      .map((team) => {
        const teamConnections = allConnections.filter(
          (connection) => connection.team_key === team.key,
        );
        const matchingConnections =
          query === ""
            ? teamConnections
            : teamConnections.filter((connection) =>
                matches(
                  connection.connection_key,
                  connection.source_type,
                  connection.description,
                ),
              );
        const teamMatches = matches(
          team.name,
          team.key,
          team.contact_email,
          team.description,
        );
        return {
          team,
          connectionCount: teamConnections.length,
          matchingConnections,
          visible: query === "" || teamMatches || matchingConnections.length > 0,
        };
      })
      .filter((card) => card.visible);

    const matchingAgents =
      query === ""
        ? agents
        : agents.filter((agent) =>
            matches(
              agent.display_name,
              agent.agent_key,
              agent.description,
              agent.version,
            ),
          );

    return { teamCards, matchingAgents };
  }, [teams, agents, allConnections, query]);

  // ── Actions ──
  const reloadTokens = useCallback(async (teamKey: string) => {
    try {
      setTokens(await api.tokens(teamKey));
    } catch {
      // Non-fatal: the action toast already reported the outcome.
    }
  }, []);

  const handleIssueToken = useCallback(async () => {
    if (!primaryTeamKey || isIssuing) return;
    setIsIssuing(true);
    try {
      const issued = await api.issueToken(primaryTeamKey);
      setIssuedToken(issued.token);
      setShowRawToken(false);
      setCopied(false);
      await reloadTokens(primaryTeamKey);
      pushToast("Token issued. Copy it now — it will not be shown again.");
    } catch (caught) {
      pushToast(errorMessage(caught, "Unable to issue a token."), "error");
    } finally {
      setIsIssuing(false);
    }
  }, [primaryTeamKey, isIssuing, reloadTokens, pushToast]);

  const handleRevokeAll = useCallback(async () => {
    if (!primaryTeamKey || isRevoking) return;
    setIsRevoking(true);
    try {
      const result = await api.revokeTokens(primaryTeamKey);
      setIssuedToken(null);
      await reloadTokens(primaryTeamKey);
      pushToast(
        `Revoked ${result.revoked} token${result.revoked === 1 ? "" : "s"}.`,
      );
    } catch (caught) {
      pushToast(errorMessage(caught, "Unable to revoke tokens."), "error");
    } finally {
      setIsRevoking(false);
      setRevokeArmed(false);
    }
  }, [primaryTeamKey, isRevoking, reloadTokens, pushToast]);

  const handleCopyToken = useCallback(async () => {
    if (!issuedToken) return;
    try {
      await navigator.clipboard.writeText(issuedToken);
      setCopied(true);
    } catch {
      pushToast(
        "Clipboard unavailable — reveal the token and copy it manually.",
        "error",
      );
    }
  }, [issuedToken, pushToast]);

  const handleAgentToggle = useCallback(
    async (agent: AgentModel) => {
      if (busyAgentKey) return;
      const nextStatus: AgentStatus =
        agent.status === "active" ? "disabled" : "active";
      setBusyAgentKey(agent.agent_key);
      try {
        const updated = await api.setAgentStatus(agent.agent_key, nextStatus);
        setAgents((prev) =>
          prev.map((item) =>
            item.agent_key === updated.agent_key ? updated : item,
          ),
        );
        pushToast(
          nextStatus === "active"
            ? `Agent '${updated.agent_key}' activated.`
            : `Agent '${updated.agent_key}' paused.`,
        );
      } catch (caught) {
        pushToast(
          errorMessage(caught, "Unable to update agent status."),
          "error",
        );
      } finally {
        setBusyAgentKey(null);
      }
    },
    [busyAgentKey, pushToast],
  );

  const handleRegister = useCallback(
    async (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      if (!primaryTeamKey || isRegistering) return;
      setIsRegistering(true);
      try {
        const created = await api.registerTeam({
          key: primaryTeamKey,
          name: regName.trim(),
          description: regDescription.trim(),
          contact_email: regEmail.trim(),
        });
        pushToast(`Team '${created.name}' registered.`);
        setRegName("");
        setRegDescription("");
        setRegEmail("");
        await refresh();
      } catch (caught) {
        pushToast(
          errorMessage(caught, "Unable to register the team."),
          "error",
        );
      } finally {
        setIsRegistering(false);
      }
    },
    [
      primaryTeamKey,
      isRegistering,
      regName,
      regDescription,
      regEmail,
      pushToast,
      refresh,
    ],
  );

  // ── Initial skeleton ──
  if (!hasLoaded) {
    return (
      <div className="min-h-full bg-canvas px-4 py-5 text-ink sm:px-6 lg:px-8">
        <div className="mx-auto flex max-w-[1440px] flex-col gap-5">
          <div>
            <SkeletonBar className="h-3 w-36" />
            <SkeletonBar className="mt-2 h-8 w-64" />
            <SkeletonBar className="mt-2 h-4 w-80" />
          </div>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            {[0, 1, 2, 3].map((index) => (
              <SkeletonBar key={index} className="min-h-[9.5rem] rounded-xl" />
            ))}
          </div>
          <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_22rem]">
            <SkeletonBar className="h-96 rounded-xl" />
            <SkeletonBar className="h-96 rounded-xl" />
          </div>
        </div>
      </div>
    );
  }

  const tabs: { key: TabKey; label: string }[] = canManage
    ? [
        { key: "my-team", label: "My Team" },
        { key: "directory", label: "Global Directory" },
      ]
    : [{ key: "directory", label: "Global Directory" }];

  return (
    <div className="min-h-full bg-canvas px-4 py-5 text-ink sm:px-6 lg:px-8">
      <div className="mx-auto flex max-w-[1440px] flex-col gap-5">
        {/* ── Header ── */}
        <header className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0">
            <p className="text-[12px] font-semibold uppercase tracking-[0.18em] text-brandMid">
              Admin Workspace
            </p>
            <h1 className="mt-1 text-2xl font-semibold tracking-tight text-ink sm:text-3xl">
              Team Operations
            </h1>
            <p className="mt-1 text-sm text-muted">{subtitle}</p>
          </div>
          <div className="flex shrink-0 items-center gap-3">
            <div
              className="flex rounded-lg border border-line bg-panel p-0.5"
              role="tablist"
              aria-label="Dashboard views"
            >
              {tabs.map((item) => (
                <button
                  key={item.key}
                  role="tab"
                  aria-selected={tab === item.key}
                  onClick={() => setTab(item.key)}
                  className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
                    tab === item.key
                      ? "bg-brand text-white shadow-sm"
                      : "text-muted hover:text-ink"
                  }`}
                  type="button"
                >
                  {item.label}
                </button>
              ))}
            </div>
            <button
              aria-label="Refresh dashboard data"
              onClick={() => void refresh()}
              className="flex h-8 w-8 items-center justify-center rounded-lg border border-line bg-panel text-muted transition-colors hover:bg-surface hover:text-ink"
              type="button"
            >
              <IconRefresh
                className={`h-3.5 w-3.5 ${isLoading ? "animate-spin" : ""}`}
              />
            </button>
          </div>
        </header>

        {/* ── Fatal-ish banner: team mapping failed ── */}
        {sectionErrors.myTeam ? (
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-dangerLine bg-dangerBg px-4 py-3 text-sm text-dangerText">
            <div className="flex min-w-0 items-center gap-2">
              <IconAlert className="h-4 w-4 shrink-0" />
              <p className="min-w-0">{sectionErrors.myTeam}</p>
            </div>
            <button
              onClick={() => void refresh()}
              className="rounded-lg border border-dangerLine bg-panel px-3 py-1.5 text-xs font-semibold text-dangerText transition hover:opacity-80"
              type="button"
            >
              Retry
            </button>
          </div>
        ) : null}

        {/* ── Metric row ── */}
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <MetricCard
            label="Active Agents"
            value={`${activeAgentCount}/${agents.length}`}
            caption="fleet currently serving"
            gradient={GRADIENT_PLUM}
            icon={<IconBot />}
            percent={
              agents.length > 0 ? (activeAgentCount / agents.length) * 100 : 0
            }
          />
          <MetricCard
            label="Active Tokens"
            value={String(activeTokenCount)}
            caption={
              primaryRegistered
                ? `${tokens.length} issued in total`
                : "register your team to issue tokens"
            }
            gradient={GRADIENT_BLUE}
            icon={<IconKey />}
          />
          <MetricCard
            label="Connections"
            value={String(
              primaryTeamKey ? myConnections.length : allConnections.length,
            )}
            caption={
              primaryTeamKey
                ? `data sources linked to ${primaryTeamKey}`
                : "data sources across all teams"
            }
            gradient={GRADIENT_TEAL}
            icon={<IconPlug />}
          />
          <MetricCard
            label="Teams in Directory"
            value={String(teams.length)}
            caption="registered across the organization"
            gradient={GRADIENT_AMBER}
            icon={<IconUsers />}
          />
        </div>

        {/* ── Tab content ── */}
        {tab === "my-team" && canManage ? (
          <div
            role="tabpanel"
            className="grid min-w-0 animate-fade-up items-start gap-5 xl:grid-cols-[minmax(0,1fr)_22rem]"
          >
            {/* ── Main column ── */}
            <div className="flex min-w-0 flex-col gap-5">
              {/* Welcome / registration (unregistered mapped team) */}
              {primaryTeamKey && !primaryRegistered ? (
                <section
                  className={`rounded-xl border border-line bg-panel p-5 ${CARD_SHADOW}`}
                >
                  <p className="text-[12px] font-semibold uppercase tracking-[0.18em] text-brandMid">
                    Welcome
                  </p>
                  <h2 className="mt-1 text-lg font-semibold text-ink">
                    Register your team
                  </h2>
                  <p className="mt-1 text-sm text-muted">
                    Your Entra group maps to{" "}
                    <span className="font-mono text-ink">{primaryTeamKey}</span>
                    , but it is not registered yet. Register it to issue service
                    tokens and link data sources.
                  </p>
                  <form
                    className="mt-4 grid gap-3 sm:grid-cols-2"
                    onSubmit={(event) => void handleRegister(event)}
                  >
                    <Field label="Team key">
                      <input
                        className={`${INPUT_CLASS} font-mono text-muted`}
                        value={primaryTeamKey}
                        readOnly
                        aria-label="Mapped team key (read-only)"
                      />
                    </Field>
                    <Field label="Team name">
                      <input
                        className={INPUT_CLASS}
                        value={regName}
                        onChange={(event) => setRegName(event.target.value)}
                        placeholder="e.g. Benefits Platform"
                        required
                      />
                    </Field>
                    <Field label="Contact email">
                      <input
                        className={INPUT_CLASS}
                        type="email"
                        value={regEmail}
                        onChange={(event) => setRegEmail(event.target.value)}
                        placeholder="team-dl@company.com"
                        required
                      />
                    </Field>
                    <Field label="Description" full>
                      <textarea
                        className={`${INPUT_CLASS} resize-none`}
                        rows={2}
                        value={regDescription}
                        onChange={(event) =>
                          setRegDescription(event.target.value)
                        }
                        placeholder="What this team owns and operates"
                      />
                    </Field>
                    <div className="sm:col-span-2">
                      <button
                        className={BUTTON_PRIMARY}
                        type="submit"
                        disabled={isRegistering}
                      >
                        {isRegistering ? "Registering…" : "Register team"}
                      </button>
                    </div>
                  </form>
                </section>
              ) : null}

              {!primaryTeamKey ? (
                <section
                  className={`flex items-start gap-3 rounded-xl border border-line bg-panel p-5 ${CARD_SHADOW}`}
                >
                  <IconShield className="mt-0.5 h-5 w-5 shrink-0 text-brandMid" />
                  <div>
                    <p className="text-sm font-semibold text-ink">
                      No team group mapped
                    </p>
                    <p className="mt-1 text-sm text-muted">
                      You have global admin access but no Entra team group of
                      your own. Team-scoped panels below stay empty; use the
                      Global Directory to browse every registered team.
                    </p>
                  </div>
                </section>
              ) : null}

              {/* ── Tokens ── */}
              <ChartCard
                title="Service Tokens"
                subtitle={
                  primaryRegistered
                    ? `Bearer credentials for ${primaryTeamName}`
                    : "Available once your team is registered"
                }
                action={
                  primaryRegistered ? (
                    <div className="flex shrink-0 flex-wrap items-center gap-2">
                      {revokeArmed ? (
                        <>
                          <span className="text-[11px] font-semibold text-dangerText">
                            Revoke all {activeTokenCount} active?
                          </span>
                          <button
                            className={BUTTON_DANGER}
                            onClick={() => void handleRevokeAll()}
                            disabled={isRevoking}
                            type="button"
                          >
                            {isRevoking ? "Revoking…" : "Confirm"}
                          </button>
                          <button
                            className={BUTTON_QUIET}
                            onClick={() => setRevokeArmed(false)}
                            disabled={isRevoking}
                            type="button"
                          >
                            Cancel
                          </button>
                        </>
                      ) : (
                        <>
                          {activeTokenCount > 0 ? (
                            <button
                              className={BUTTON_DANGER}
                              onClick={() => setRevokeArmed(true)}
                              type="button"
                            >
                              Revoke all
                            </button>
                          ) : null}
                          <button
                            className={BUTTON_PRIMARY}
                            onClick={() => void handleIssueToken()}
                            disabled={isIssuing}
                            type="button"
                          >
                            {isIssuing ? "Issuing…" : "Issue token"}
                          </button>
                        </>
                      )}
                    </div>
                  ) : undefined
                }
              >
                <div className="space-y-3">
                  {sectionErrors.tokens ? (
                    <InlineError text={sectionErrors.tokens} />
                  ) : null}

                  {/* One-time raw token panel */}
                  {issuedToken ? (
                    <div className="rounded-xl border border-[#A26408] bg-[#FCF1DC] p-4">
                      <div className="flex items-center gap-2 text-[#6B4205]">
                        <IconAlert className="h-4 w-4 shrink-0" />
                        <p className="text-sm font-semibold">
                          One-time token — copy it now
                        </p>
                      </div>
                      <div className="mt-3 flex items-center gap-2">
                        <code className="min-w-0 flex-1 truncate rounded-lg border border-[#A26408]/40 bg-panel px-3 py-2 font-mono text-[13px] text-ink">
                          {showRawToken ? issuedToken : "•".repeat(28)}
                        </code>
                        <button
                          aria-label={
                            showRawToken ? "Hide token" : "Show token"
                          }
                          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-[#A26408]/40 bg-panel text-[#6B4205] transition hover:opacity-80"
                          onClick={() => setShowRawToken((value) => !value)}
                          type="button"
                        >
                          {showRawToken ? <IconEyeOff /> : <IconEye />}
                        </button>
                        <button
                          aria-label="Copy token to clipboard"
                          className="flex h-9 shrink-0 items-center gap-1.5 rounded-lg border border-[#A26408]/40 bg-panel px-3 text-xs font-semibold text-[#6B4205] transition hover:opacity-80"
                          onClick={() => void handleCopyToken()}
                          type="button"
                        >
                          {copied ? (
                            <IconCheck className="h-3.5 w-3.5" />
                          ) : (
                            <IconCopy className="h-3.5 w-3.5" />
                          )}
                          {copied ? "Copied" : "Copy"}
                        </button>
                      </div>
                      <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
                        <p className="text-[12px] text-[#6B4205]">
                          Shown once. Store it now — only a hash is kept.
                        </p>
                        <button
                          className="text-[12px] font-semibold text-[#6B4205] underline-offset-2 hover:underline"
                          onClick={() => setIssuedToken(null)}
                          type="button"
                        >
                          I stored it — dismiss
                        </button>
                      </div>
                    </div>
                  ) : null}

                  {!primaryRegistered ? (
                    <EmptyState
                      title="No tokens yet"
                      hint={
                        primaryTeamKey
                          ? "Register your team above, then issue a service token for your agents."
                          : "Tokens are scoped to a team; your account has no team mapping."
                      }
                    />
                  ) : tokens.length === 0 ? (
                    <EmptyState
                      title="No tokens issued"
                      hint="Issue a token to let your agents authenticate against the platform."
                    />
                  ) : (
                    tokens.map((token) => (
                      <TokenRow key={token.id} token={token} />
                    ))
                  )}
                </div>
              </ChartCard>

              {/* ── Agent fleet (platform-wide registry) ── */}
              <ChartCard
                title="Agent Fleet"
                subtitle="Agents are registered platform-wide and shared across teams"
              >
                <div className="space-y-3">
                  {sectionErrors.agents ? (
                    <InlineError text={sectionErrors.agents} />
                  ) : null}
                  {agents.length === 0 && !sectionErrors.agents ? (
                    <EmptyState
                      title="No agents registered"
                      hint="Register an agent through the platform CLI to see it here."
                    />
                  ) : (
                    agents.map((agent) => (
                      <div
                        key={agent.id}
                        className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-line bg-surface p-3"
                      >
                        <div className="min-w-0">
                          <div className="flex flex-wrap items-center gap-2">
                            <p className="text-sm font-medium text-ink">
                              {agent.display_name}
                            </p>
                            <StatusBadge status={agent.status} />
                          </div>
                          <p className="mt-0.5 font-mono text-xs text-muted">
                            {agent.agent_key} · v{agent.version}
                          </p>
                        </div>
                        <button
                          aria-label={
                            agent.status === "active"
                              ? `Pause agent ${agent.display_name}`
                              : `Activate agent ${agent.display_name}`
                          }
                          className={
                            agent.status === "active"
                              ? BUTTON_QUIET
                              : BUTTON_PRIMARY
                          }
                          onClick={() => void handleAgentToggle(agent)}
                          disabled={busyAgentKey !== null}
                          type="button"
                        >
                          {busyAgentKey === agent.agent_key
                            ? "Working…"
                            : agent.status === "active"
                              ? "Pause"
                              : "Activate"}
                        </button>
                      </div>
                    ))
                  )}
                </div>
              </ChartCard>

              {/* ── Connections ── */}
              <ChartCard
                title="Connections"
                subtitle={
                  primaryTeamKey
                    ? `Data sources linked to ${primaryTeamKey}`
                    : "Data sources are scoped per team"
                }
              >
                <div className="space-y-3">
                  {sectionErrors.connections ? (
                    <InlineError text={sectionErrors.connections} />
                  ) : null}
                  {myConnections.length === 0 && !sectionErrors.connections ? (
                    <EmptyState
                      title="No connections linked"
                      hint={
                        primaryRegistered
                          ? "Link a Blob or SharePoint source from Data Onboarding to ground your agents."
                          : "Register your team first, then link data sources."
                      }
                    />
                  ) : (
                    myConnections.map((connection) => (
                      <div
                        key={connection.id}
                        className="rounded-lg border border-line bg-surface p-3"
                      >
                        <div className="flex flex-wrap items-center gap-2">
                          <p className="font-mono text-sm font-medium text-ink">
                            {connection.connection_key}
                          </p>
                          <SourceChip type={connection.source_type} />
                        </div>
                        {connection.description ? (
                          <p className="mt-1 text-xs text-muted">
                            {connection.description}
                          </p>
                        ) : null}
                      </div>
                    ))
                  )}
                </div>
              </ChartCard>
            </div>

            {/* ── Right rail ── */}
            <aside className="flex min-w-0 flex-col gap-5">
              <ChartCard
                title="Fleet Status"
                subtitle="Agent registry by status"
              >
                <DonutChart segments={fleetSegments} centerLabel="agents" />
              </ChartCard>

              <ChartCard
                title="Onboarding Pipeline"
                subtitle="Where your team is on the path to live traffic"
              >
                <OnboardingPipeline steps={pipelineSteps} />
              </ChartCard>
            </aside>
          </div>
        ) : (
          /* ── Global Directory ── */
          <div
            role="tabpanel"
            className="flex animate-fade-up flex-col gap-5"
          >
            {!canManage ? (
              <div
                className={`flex items-start gap-3 rounded-xl border border-line bg-panel p-5 ${CARD_SHADOW}`}
              >
                <IconShield className="mt-0.5 h-5 w-5 shrink-0 text-brandMid" />
                <div>
                  <p className="text-sm font-semibold text-ink">
                    Your account has no team admin group mapped.
                  </p>
                  <p className="mt-1 text-sm text-muted">
                    You can browse the directory below. To manage a team, ask a
                    platform admin to add you to that team&apos;s Entra admin
                    group.
                  </p>
                </div>
              </div>
            ) : null}

            <div
              className={`rounded-xl border border-line bg-panel p-4 ${CARD_SHADOW}`}
            >
              <div className="relative">
                <IconSearch className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted" />
                <input
                  aria-label="Search the global directory"
                  className={`${INPUT_CLASS} pl-9`}
                  placeholder="Search teams, agents, connections"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                />
              </div>
            </div>

            {sectionErrors.teams ? (
              <InlineError text={sectionErrors.teams} />
            ) : null}

            {directory.teamCards.length === 0 &&
            directory.matchingAgents.length === 0 ? (
              <EmptyState
                title="No matches"
                hint={
                  query
                    ? "Try a broader term — search covers team names, keys, contacts, agent keys, and connection keys."
                    : "No teams have registered yet. Yours could be the first."
                }
              />
            ) : (
              <>
                {directory.teamCards.length > 0 ? (
                  <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                    {directory.teamCards.map(
                      ({ team, connectionCount, matchingConnections }) => (
                        <article
                          key={team.id}
                          className={`rounded-xl border border-line bg-panel p-4 ${CARD_SHADOW}`}
                        >
                          <div className="flex items-start justify-between gap-2">
                            <p className="min-w-0 truncate text-sm font-semibold text-ink">
                              {team.name}
                            </p>
                            <span className="shrink-0 rounded-full border border-line bg-surface px-2 py-0.5 font-mono text-[11px] text-muted">
                              {team.key}
                            </span>
                          </div>
                          {team.description ? (
                            <p className="mt-1 text-xs text-muted">
                              {team.description}
                            </p>
                          ) : null}
                          {team.contact_email ? (
                            <a
                              href={`mailto:${team.contact_email}`}
                              className="mt-2 inline-flex items-center gap-1.5 text-xs font-medium text-brandMid hover:underline"
                            >
                              <IconMail className="h-3.5 w-3.5" />
                              {team.contact_email}
                            </a>
                          ) : null}
                          {query && matchingConnections.length > 0 ? (
                            <div className="mt-2 flex flex-wrap gap-1.5">
                              {matchingConnections.slice(0, 3).map((connection) => (
                                <span
                                  key={connection.id}
                                  className="rounded-full border border-line bg-surface px-2 py-0.5 font-mono text-[11px] text-muted"
                                >
                                  {connection.connection_key}
                                </span>
                              ))}
                            </div>
                          ) : null}
                          <div className="mt-3 flex items-center justify-between border-t border-line pt-3 text-[11px] text-subtle">
                            <span>
                              <span className="font-semibold text-ink">
                                {connectionCount}
                              </span>{" "}
                              connection{connectionCount === 1 ? "" : "s"}
                            </span>
                            <span>Since {formatDate(team.created_at)}</span>
                          </div>
                        </article>
                      ),
                    )}
                  </div>
                ) : null}

                <ChartCard
                  title="Agent Registry"
                  subtitle={`${directory.matchingAgents.length} of ${agents.length} platform agents${
                    query ? " matching your search" : ""
                  }`}
                >
                  <div className="space-y-3">
                    {sectionErrors.agents ? (
                      <InlineError text={sectionErrors.agents} />
                    ) : null}
                    {directory.matchingAgents.length === 0 &&
                    !sectionErrors.agents ? (
                      <EmptyState
                        title="No agents match"
                        hint="Search covers display names, agent keys, descriptions, and versions."
                      />
                    ) : (
                      directory.matchingAgents.map((agent) => (
                        <div
                          key={agent.id}
                          className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-line bg-surface p-3"
                        >
                          <div className="min-w-0">
                            <p className="text-sm font-medium text-ink">
                              {agent.display_name}
                            </p>
                            <p className="mt-0.5 font-mono text-xs text-muted">
                              {agent.agent_key} · v{agent.version}
                            </p>
                          </div>
                          <StatusBadge status={agent.status} />
                        </div>
                      ))
                    )}
                  </div>
                </ChartCard>
              </>
            )}
          </div>
        )}
      </div>

      {/* ── Toast ── */}
      {toast ? (
        <div
          key={toast.id}
          role="status"
          aria-live="polite"
          className={`fixed bottom-6 right-6 z-50 max-w-sm animate-fade-up rounded-xl border px-4 py-3 text-sm shadow-card ${
            toast.tone === "error"
              ? "border-dangerLine bg-dangerBg text-dangerText"
              : "border-line bg-panel text-ink"
          }`}
        >
          {toast.text}
        </div>
      ) : null}
    </div>
  );
}
