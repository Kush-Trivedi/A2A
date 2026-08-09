# ACE Agent-as-a-Service Platform Plan (A2A Edition)

Status: agreed direction — implementation tracked in `STATE.md`.
Scope: `ACE/` (control plane + chat frontend) + `Agents/` (ODT team agents on the official Google A2A SDK).

---

## 1. Current state (already built and verified)

| Area | State |
|---|---|
| Config | 100% yaml-centralized (`security`, `authorization`, `knowledge`, `agents` sections in all four env yamls). `ApplicationContext` is the only class that touches the environment. `ENV` is the single bootstrap variable. |
| Auth | Entra OAuth code flow + JWT validation + session cookies + CSRF. Legacy compat routes removed. Users, roles, and groups auto-provisioned/stored on login. |
| RBAC | Casbin (RBAC with tenant domains), policy audit log, `require_permission` guards. |
| RAG | Selectable chunking (`simple`/`recursive`/`hierarchical`/`semantic`) per ingestion request; selectable retrieval (`dense`/`sparse`/`hybrid`) per agent. pgvector + FTS + RRF fusion + neighbor expansion. |
| Databricks | `WorkspaceClient` factory, PAT auth for ALL environments (decided — no M2M), yaml-only config. |
| Registry | `odt_teams` + `registered_agents` tables, `/api/v1/admin/agents*` API, Casbin policy auto-seeding on registration, `team_config` JSONB for team-owned Databricks ids, team `contact_email`. |
| Template agent | `Agents/scheduling_agent` on `a2a-sdk` 1.1.2 serving its AgentCard; self-registration script. |

---

## 2. Architecture

```
                        ┌─────────────────────────────────────────────┐
  Chat + Canvas UI ────►│  ACE CONTROL PLANE (ACE/)                   │
  (chat-only, role-     │  Entra OAuth + sessions + CSRF   (exists)   │
   scoped agents)       │  Casbin RBAC + audit             (exists)   │
                        │  Agent Registry (Postgres)       (exists)   │
                        │  Integration health / parity     (planned)  │
                        │  A2A CLIENT LAYER                (planned)  │
                        │  Context Envelope propagation    (planned)  │
                        │  Capability APIs: RAG, Genie     (partial)  │
                        └───────┬──────────────┬──────────────────────┘
                          A2A   │              │  A2A (JSON-RPC / SSE)
                        ┌───────▼─────┐  ┌─────▼────────┐
                        │ clinical_   │  │ pay_ops      │  N teams × N agents
                        │ care /      │──│ insurance    │  each from the
                        │ scheduling  │A2A agent        │  Agents/ template
                        └───────┬─────┘  └─────┬────────┘
                        ┌───────▼──────────────▼──────────────────────┐
                        │ DATA: Databricks UC (PAT, Genie/warehouse), │
                        │ pgvector RAG, SharePoint, Blob              │
                        └─────────────────────────────────────────────┘
```

Ownership split (non-negotiable): **teams own** their agent logic, `agent.yaml`,
their Databricks resources (warehouse/catalog/schema/Genie space), their skills,
and **which roles may access their agent** (declared at registration → policies
auto-seeded). **ACE owns** Entra, sessions, RBAC enforcement, chat, registry,
capability APIs, and audit.

---

## 3. Config parity — real credentials, one code path

The environments must *feel identical*. The user will drop real credentials into
the yaml files later and everything must simply work. Rules:

1. **One code path for every environment.** Business logic never branches on
   `ENV`. Environment differences exist only as *data* in the yaml files.
   (The two sanctioned, yaml-driven exceptions: docs exposure and cookie
   hardening, both explicit config.)
2. **No dummy-cred behavior.** Never special-case placeholder values to "make it
   work locally". If a credential is wrong, the system says so precisely.
3. **Fail informative, not silent.** `SettingsValidator` (class) runs at startup:
   verifies required yaml keys exist and flags values still matching the
   `your_*` placeholder pattern as warnings naming the exact yaml path.
4. **Integration health as a feature.** `IntegrationHealthService` (class) probes
   each configured integration lazily — Postgres, Databricks (PAT), Key Vault,
   Azure OpenAI/Foundry, registered A2A agents — surfaced at
   `GET /api/v1/admin/health/integrations` (admin-guarded). Same probe code in
   every environment. This is the checklist the user runs after swapping creds.
5. **Lazy clients, typed errors.** External clients are created on first use;
   failures raise the existing typed error hierarchy with the yaml key that
   feeds them (e.g. "databricks.token") — never raw tracebacks to callers.
6. **Secrets only via yaml or Key Vault `lookup:` references.** Never in code,
   never logged (log redaction already installed).

---

## 4. The full A2A object model (60 types in `a2a.types`, protocol v0.3)

We code against the installed SDK's protobuf types — never hand-rolled dicts.
Every group has one owning ACE-side class.

### 4.1 Discovery & identity

| A2A type | ACE handling |
|---|---|
| `AgentCard` | Fetched + validated by `AgentCardService` at registration; snapshot stored in `registered_agents`. |
| `AgentSkill` | Skill ids follow the original codebase's dot convention: `<team>.<domain>.<action>` (e.g. `clinical_care.scheduling.schedule_appointment`). Registry indexes skills for capability resolution. |
| `AgentCapabilities` | Streaming required for chat-facing agents. |
| `AgentInterface` | JSONRPC binding + public URL. |
| `AgentProvider` | Set to the ODT team name. |
| `AgentExtension` | Reserved for the ACE context-envelope extension URI. |
| `AgentCardSignature` | Prod hardening phase — tamper-evident cards. |
| `SecurityRequirement`, `SecurityScheme`, `StringList` | See §5 / proto helpers. |

### 4.2 Security schemes (11 types)

`APIKeySecurityScheme`, `HTTPAuthSecurityScheme`, `OAuth2SecurityScheme`,
`OpenIdConnectSecurityScheme`, `MutualTlsSecurityScheme`, `OAuthFlows`,
`AuthorizationCodeOAuthFlow`, `ClientCredentialsOAuthFlow`, `DeviceCodeOAuthFlow`,
`ImplicitOAuthFlow`, `PasswordOAuthFlow`.

Decision: agents declare **`OpenIdConnectSecurityScheme`** pointing at the Entra
tenant OIDC discovery URL. All service-plane calls carry Entra Bearer JWTs
validated with the same JWKS machinery ACE already has.

### 4.3 Core exchange

| A2A type | ACE handling |
|---|---|
| `Message` | Built only by `A2AMessageFactory`. `metadata` carries the ACE **ContextEnvelope** (§6). `contextId` = chat session id. `referenceTaskIds` chain delegations. |
| `Part` (Text/File/Data) | `PartMapper` converts chat messages, uploads, and structured outputs. |
| `Role` | user / agent, set by the factory. |
| `Task`, `TaskStatus`, `TaskState` | 9 states (submitted, working, input-required, completed, canceled, failed, rejected, auth-required, unknown). `TaskLifecycleTracker` persists transitions; `input-required`/`auth-required` surface to the chat UI. |
| `Artifact` | Structured results; `ArtifactMapper` extracts `DataPart` payloads into typed DTOs → rendered on the **canvas** (§7). |
| `SendMessageConfiguration` | Set by the client layer. |

### 4.4 RPC surface (14 request/response types)

`A2ARequest`, `SendMessageRequest/Response`, `StreamResponse`, `GetTaskRequest`,
`ListTasksRequest/Response`, `CancelTaskRequest`, `SubscribeToTaskRequest`,
`GetExtendedAgentCardRequest`, push-config get/list/delete requests.

Handled exclusively inside `A2AClientService` — chat and orchestration code never
touch raw RPC objects. Methods: `message/send`, `message/stream` (SSE proxied to
the existing chat SSE), `tasks/get`, `tasks/cancel`, `tasks/resubscribe`.

### 4.5 Streaming & push (4 types)

`TaskStatusUpdateEvent`, `TaskArtifactUpdateEvent`, `TaskPushNotificationConfig`,
`AuthenticationInfo`. Chat uses SSE streaming. Push webhooks are a later phase
(long-running back-office tasks); the receiver validates `AuthenticationInfo`
with the same Entra validator.

### 4.6 Protocol errors (13 types)

`A2AErrorTranslator` maps every protocol error (`TaskNotFoundError`,
`InvalidAgentResponseError`, `VersionNotSupportedError`, ...) onto the existing
`utils/errors` hierarchy so the chat API keeps its uniform envelope and audit
codes.

---

## 5. Entra auth + session mapping

Two planes, never mixed:

1. **Human plane (exists, unchanged):** browser → Entra OAuth code flow (PKCE +
   state cookie) → `SessionStore` cookie + CSRF. Groups and roles are resolved
   and stored at login (already working). Sessions NEVER cross A2A.
2. **Service plane (new):** every A2A hop authenticates with an Entra
   **client-credentials JWT**:
   - Each ODT team gets an Entra app registration (per-team to start; per-agent
     if scale demands — open decision #1).
   - `ServiceTokenProvider` (ACE, modeled on the `AzurePostgresToken` cache
     pattern) acquires tokens for the target agent's audience and caches to
     expiry.
   - The Agent Kit ships `EntraTokenValidator` middleware (ported
     `JWTValidator`/`JwksCache`) — issuer = tenant, audience = the agent's app id.
   - The service token proves *which service* calls; the **ContextEnvelope**
     says *on whose behalf*. RBAC is enforced at ACE before dispatch and
     re-checked by agents (defense in depth).
   - `auth-required` TaskState → re-auth prompt in chat.

Config ownership: common Entra values live in ACE yaml (`microsoft.entra`);
each agent's own app id/audience is team-owned (`agent.yaml` → registry
`team_config`).

---

## 6. ContextEnvelope (cross-team context passing)

Frozen dataclass shipped by both ACE and the Agent Kit, serialized into
`Message.metadata` under `ace.context/v1`:

`tenant_id, actor_id, user_id, roles, correlation_id, chat_session_id,
delegated_from, delegation_reason, purpose`.

Rules: every hop forwards it; `referenceTaskIds` must include the upstream task;
every dispatch and delegation writes a `policy_audit_log` row.

Acceptance scenario: chat → ACE resolves `clinical_care.scheduling` (RBAC
pre-filtered) → `message/stream` + envelope → scheduling agent books, emits
`Artifact{DataPart: appointment}` → needs insurance → resolves
`pay_ops.insurance.verify` via registry → `message/send` with forwarded envelope
+ `referenceTaskIds` → result artifact merges into the same `contextId` thread →
one chat answer, fully audited chain.

---

## 7. Frontend: single chat screen + canvas

One screen only. No dashboards (those come much later). React + TypeScript +
Vite in `ACE/frontend`, structurally referencing the old AAAS frontend
(ChatWindow / MessageList / MessageBubble / MarkdownContent / ChatInput,
fetch-based SSE with CSRF header) but rebuilt chat-only.

### Layout

```
┌──────────┬──────────────────────────────┬────────────────────┐
│ Sessions │  Chat thread                 │  Canvas            │
│ (list,   │  - streamed markdown         │  (opens when a     │
│  rename, │  - agent chip per answer     │   turn produces    │
│  new)    │  - feedback / edit           │   an artifact)     │
│          │  [input box]                 │  - structured data │
│          │                              │    tables (DataPart)│
│          │                              │  - documents/files │
│          │                              │  - sources panel   │
└──────────┴──────────────────────────────┴────────────────────┘
```

### Behavior

- **Login-gated:** on load → `GET /api/v1/auth/me`; 401 → redirect to
  `/api/v1/auth/login` (Entra). Roles/groups come from the session profile.
- **Role-scoped agents:** `GET /api/v1/chat/agents` returns only the agents the
  logged-in user's roles permit (RBAC pre-filter — the original repo's
  tool_guard pattern; extended to include active registry A2A agents). The UI
  shows these as the user's active agents; the user just asks — routing picks
  the right permitted agent.
- **Canvas:** when a turn yields an `Artifact`, the canvas panel opens and
  renders it by kind — `DataPart` → typed table/card view, `TextPart` →
  formatted document, `FilePart` → preview/download. Sources (RAG citations)
  render beneath. Collapsible; conversation stays primary.
- **Out-of-scope / no-access UX:** if the question maps to an agent the user
  cannot access (or no accessible agent matches), the backend's
  `OutOfScopeResponder` (class) returns a structured refusal —
  `{type: "access_denied", team_key, team_name, contact_email, message}` — built
  from the registry (team `contact_email`). The UI renders a polite card:
  *"This looks like a question for the Pay Ops team. Please contact
  payops@hospital.org to request access."* Never a raw 403, never a hallucinated
  answer.
- **TS code conventions:** API layer is a class (`AceApiClient`) with typed
  methods (sessions, chat, stream, agents, me); SSE handling in a
  `ChatStreamController` class; React components stay idiomatic
  function components — the no-loose-functions rule applies to Python services
  and the TS service/API layer, not to React component definitions.

---

## 8. OOP + DRY conventions (mandatory)

- **No loose functions.** All behavior lives in classes. Only allowed
  module-level callables: thin `get_*()` singleton accessors (established
  pattern) and FastAPI route handlers that immediately delegate to a service.
- Per concern, the canonical trio: frozen **settings dataclass** (fed only by
  `ApplicationContext`) + **service class** + `get_*()` accessor registered in
  the `ServiceContainer`.
- **DRY:** shared behavior extracted once — the Agent Kit package carries
  everything both ACE and team agents need (ContextEnvelope, EntraTokenValidator,
  card building, ACE capability client) so no team copies platform code.
- Entities (SQLModel) vs DTOs (pydantic `StrictBaseModel`) strictly separated;
  mapping in mapper classes.
- Cleanups owed: `build_chunker` → `ChunkerFactory`; template agent's
  `build_agent_card`/`build_app` → `AgentCardBuilder`/`AgentApplicationBuilder`;
  `register.py` → `AceRegistrationClient` class with a `__main__` shim.

New ACE classes: `A2AClientService`, `AgentCardService`, `A2AMessageFactory`,
`PartMapper`, `ArtifactMapper`, `ContextEnvelope`, `ServiceTokenProvider`,
`TaskLifecycleTracker`, `A2AErrorTranslator`, `CapabilityResolver`,
`OutOfScopeResponder`, `SettingsValidator`, `IntegrationHealthService`,
`GenieKnowledgeProvider`.

New Agent Kit / template classes: `AgentCardBuilder`, `AgentApplicationBuilder`,
`EntraTokenValidator`, `ContextEnvelope` (shared), `AceCapabilityClient`,
`AceRegistrationClient`.

---

## 9. Execution phases

| # | Phase | Contents | Acceptance |
|---|---|---|---|
| 0 | Plan + state docs | This file + `STATE.md` | docs exist, user approves |
| 1 | Config parity & integration health | `SettingsValidator`, `IntegrationHealthService`, `/admin/health/integrations`; placeholder detection | health endpoint reports per-integration status; swapping creds in yaml requires zero code changes |
| 2 | A2A client layer | `A2AClientService`, `A2AMessageFactory`, `PartMapper`, `ArtifactMapper`, `TaskLifecycleTracker`, `A2AErrorTranslator`; chat dispatches to registry agents (status=active) via `message/stream` | chat message routed to the running template agent and streamed back through ACE |
| 3 | Card validation + capability resolution | `AgentCardService` (fetch/validate/snapshot on registration), `CapabilityResolver`, `OutOfScopeResponder` | registering an unreachable/invalid card fails cleanly; out-of-scope question returns team-contact refusal |
| 4 | Service-plane Entra auth | `ServiceTokenProvider` (ACE) + `EntraTokenValidator` (kit); cards declare OIDC scheme | authenticated ACE→agent call verified; unauthenticated call rejected |
| 5 | ContextEnvelope + delegation | envelope classes both sides, forwarding + `referenceTaskIds`, audit rows | scheduling→pay-ops chain works end-to-end with full audit trail |
| 6 | Frontend chat + canvas | `ACE/frontend` per §7 | login → role-scoped agents → ask → streamed answer; artifact renders on canvas; refusal card renders |
| 7 | Genie provider | `GenieKnowledgeProvider` behind `KnowledgeProvider`; warehouse SQL path | Genie query answers through a team agent using its `team_config` ids |
| 8 | OOP/DRY cleanup + hardening | §8 cleanups; push notifications; card signing | zero loose functions; template signed cards |

Phases 2–5 are sequential; 6 can start in parallel after 2; 7 after 4.

---

## 10. Knowledge-agent wave (phases 9–12)

Every agent uses the IDENTICAL scaffold (agent.yaml + config/auth/card/main/
executor + ace-agent-kit), registers in the registry (Casbin policies
auto-seeded from `allowed_roles`), and is enforced twice: (a) agent access —
`agent:<key>` permission checked before dispatch, refusal UX on deny; (b) data
access — `KnowledgeGateway` authorizes every `knowledge:<source>` per role
before retrieval. Sources are declared in agent.yaml → registry
`knowledge_sources` — an agent can NEVER answer outside its declared sources.

**Shared prerequisite (phase 9):** `POST /api/v1/knowledge/retrieve` — a
service-plane capability endpoint (Entra bearer validated by ACE) so remote
agents retrieve from pgvector through ACE with the caller's envelope enforced
(roles → source filter). Kit gains `AceCapabilityClient` (retrieve + ingest).

| Phase | Agent | Design |
|---|---|---|
| 9 | **General agent** (`ace_platform/general`) | Safe general Q&A + "what can I access": lists the user's role-scoped agents via AgentCatalogService (through a capability endpoint), explains how to request access (team contacts from registry). No knowledge sources. Also delivers the shared retrieve endpoint + AceCapabilityClient. |
| 10 | **File upload agent** (`ace_platform/file_qa`) | Chat screen upload → existing `/knowledge/ingest/file` (markitdown[all]: docx/pptx/xlsx/pdf...) → session-scoped chunks (`upload` source + session_id). Agent answers STRICTLY from the uploader's session documents: retrieve with session_id only, empty knowledge_sources, strict grounding — no session docs → "please upload a file". |
| 11 | **SharePoint agent** (`<team>/sharepoint_qa`) | Phase A: ACE endpoint `POST /api/v1/knowledge/ingest/sharepoint` — team supplies site/drive/path (their `team_config`), ACE pulls via sharepoint_helpers + markitdown → pgvector under `knowledge_source="sharepoint:<name>"`. Phase B: agent declares those sources in agent.yaml; gateway enforces per-role read policies (`knowledge:sharepoint:<name>`). |
| 12 | **Blob storage agent** (`<team>/blob_qa`) | Same two-phase shape: `POST /api/v1/knowledge/ingest/blob` — team gives storage account/container/prefix (team_config), `AzureStorageBlobClient` lists + reads, markitdown → pgvector under `knowledge_source="blob:<name>"`; agent restricted to those sources. |

**Phase 13 — Agent + prompt versioning (team-owned).** Like API versions
(`/api/v1`): every registration writes an immutable row in `agent_versions`
(tenant + agent_key + version → full snapshot: card, skills, team_config,
allowed_roles, **prompts**). The newest registration becomes `current`, prior
rows become `superseded`; `POST /admin/agents/{key}/versions/{version}/activate`
rolls back by restoring that snapshot. Prompts are TEAM-owned: declared in the
team's `agent.yaml` (`prompts:` → name → {version, content}), used at runtime
from the team's own manifest via the kit `PromptStore` — ACE never authors
prompts, it only records their versions for governance/audit. One agent version
carries multiple named prompts, each independently versioned.

**As built — agent inventory (all live, all E2E-verified):**

| Agent | Team / key | Port | Skills | Scope & notes |
|---|---|---|---|---|
| Scheduling v0.2.0 | clinical_care/scheduling | 3100 | schedule_appointment, check_availability | Delegates insurance to Pay Ops via kit `AgentDelegator` (envelope + referenceTaskIds); versioned prompts booking_ack/delegation_reason |
| Insurance | pay_ops/insurance | 3200 | verify_insurance, appeal_claim | Echoes received ContextEnvelope — proves identity crosses team boundary |
| General Assistant | ace_platform/general | 3300 | general_help, access_overview | "What can I access?" answered from live role-scoped capability catalog |
| File Q&A | ace_platform/file_qa | 3400 | file_question_answering | Session-strict: only the caller's chat-session uploads; no docs → upload prompt |
| Policy Library | clinical_care/sharepoint_qa | 3500 | policy_question_answering | Sources `sharepoint:policies` (phase-A `/knowledge/ingest/sharepoint`); double enforcement verified |
| Claims Archive | pay_ops/blob_qa | 3600 | claims_document_answering | Sources `blob:claims` (phase-A `/knowledge/ingest/blob`); double enforcement verified |

All six share the identical scaffold + `ace-agent-kit`; sparse retrieval by
default (yaml flip to hybrid once LLM creds live); auth off locally by explicit
`auth.enabled` switch (flip + fill tenant/audience to go live).

Casbin matrix per agent (seeded at registration + admin-managed):
`(role, tenant, agent:<key>, chat)` for access; `(role, tenant,
knowledge:<source>, read)` for each declared source; ingestion endpoints
require `knowledge:<source> write`. All existing patterns — no new mechanisms.

## 11. Open decisions

1. Entra app registration granularity: per team (recommended start) vs per agent.
2. Agent→agent: direct (recommended, with mandatory envelope + audit) vs
   brokered through ACE.
3. Deployment target for team agents (Azure Container Apps / AKS) — affects
   card URLs and networking.
4. Card signing timing — prod hardening phase unless compliance wants it sooner.
