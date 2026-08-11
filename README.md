# ACE — Agent-as-a-Service Platform (Healthcare)

ACE is the hospital's agent platform: teams (ODTs) build and own their agents
as independent **Google A2A protocol** services; ACE owns identity (Entra),
RBAC (Casbin), the **channels** (web chat + canvas UI, Twilio SMS, Microsoft
Teams), RAG/graph-RAG over pgvector, Databricks (PAT), SharePoint/Blob
ingestion, the LLM gateway (per-team Foundry deployments), the agent registry
with versioning, rate limiting, and the audit trail. The channel machinery is
ACE's, the answer is the team's — and which channels an agent appears on is
the team's choice, by config alone (`data.channels` in their agent.yaml).

Design: `PLATFORM_PLAN.md` · Build status: `STATE.md` (18 phases ✅) ·
Run & team deploy guide: `DEPLOY.md`

## Layout

Platform code lives in three folders: `ACE/` (control plane), `Agents/`
(independent team agents), and `agent_kit/` (the platform-owned shared
package both sides install). Everything else at the root is docs, container
files, and optional reference infra.

```
A2A/
├── ACE/                        # CONTROL PLANE — one uv project
│   ├── backend/app/
│   │   ├── api/                # routers (v1): chat, admin, capability, channels, knowledge
│   │   ├── services/           # a2a client/gateway, agent registry, conversation,
│   │   │                       #   sms + teams channels, knowledge/ingestion, ...
│   │   ├── security/           # Entra auth, sessions, Casbin, field encryption
│   │   ├── entity/ · dto/      # SQLModel tables vs strict API models
│   │   ├── config/env/         # <ENV>.yaml — ALL config lives here (only ENV env var)
│   │   └── database/ llm/ prompts/ observability/ scripts/ utils/
│   ├── frontend/               # chat + canvas SPA (Vite :5173, proxies /api → :3000)
│   └── pyproject.toml · uv.lock
├── agent_kit/                  # PLATFORM-OWNED shared ace-agent-kit package:
│                               #   ContextEnvelope, AgentDelegator, AceCapabilityClient,
│                               #   PromptStore — editable path dep of ACE + every agent
├── Agents/                     # TEAM-OWNED, independent A2A services (uv project each)
│   ├── scheduling_agent/       # :3100 clinical_care ─┐ identical scaffold:
│   ├── insurance_agent/        # :3200 pay_ops        │ agent.yaml (prompts, roles,
│   ├── general_agent/          # :3300 ace_platform   │ deployment, channel opt-in)
│   ├── file_upload_agent/      # :3400 ace_platform   │ + app/{main,card,config,auth,
│   ├── sharepoint_agent/       # :3500 clinical_care  │ agent_executor,register}.py
│   ├── blob_agent/             # :3600 pay_ops        │ + pyproject.toml
│   ├── sms_outreach_agent/     # :3700 clinical_care ─┘
│   ├── benefits_agent/         # :3800 hr_benefits — Teams channel opt-in example
│   └── Dockerfile              # one shared agent image; ARG AGENT selects the agent
├── infra/                      # reference Bicep (main.bicep, agent.bicep) — OPTIONAL;
│                               #   bring your own IaC, model deployments already exist
├── docker-compose.yml          # local stack: Postgres/pgvector + ACE + all agents
├── Dockerfile.ace              # control-plane image
├── teams-app-manifest.json     # Microsoft Teams app manifest template
├── README.md · DEPLOY.md · PLATFORM_PLAN.md · STATE.md
└── AAAS/ · AAAS.zip            # original source extract — unused now, deletable
```

## The agents (7 — routed by question, no supervisor)

| Agent (team/key) | Port | Character |
|---|---|---|
| **General Assistant** (ace_platform/general) | 3300 | Explains ACE + role-scoped "what can I access". The router's terminal fallback — never answers other teams' domains, never re-routes |
| **File Q&A** (ace_platform/file_qa) | 3400 | STRICT session-scoped: answers only from files uploaded in the caller's chat session (📎 in composer) — nothing else, ever |
| **Policy & Procedure** (clinical_care/policy_procedure) | 3500 | Retrieval agent over `sharepoint:policies` (ingested via Data Onboarding) |
| **SMS** (clinical_care/sms) | 3700 | CHANNEL-ONLY (not on chat UI): replies to inbound patient texts + sends outreach via ACE capability |
| **Benefits** (hr_benefits/benefits) | 3800 | Retrieval agent over benefits docs; Teams opt-in — reachable at `/channels/teams/benefits/messages` with the team's webhook secret |
| **eConsult** (clinical_care/econsult) | 3900 | Retrieval agent over the eConsult knowledge base |
| **GDA** (data_analytics/gda) | 4000 | LIVE Databricks answers via Genie capability (team's connection + space) — data never leaves Databricks |

Retired: scheduling+insurance live on as `Agents/_template_delegation/` (the
delegation reference); blob agent superseded by parameterized ingestion.
`Agents/_template/` is the scaffold every new team copies.

Every agent: identical scaffold — env-invariant `agent.yaml` (identity,
skills + routing examples, versioned prompts) + `config/env/{local,dev,uat,
prd}.yaml` (same keys everywhere; local hardcodes, cloud uses `lookup:` Key
Vault) + app/{config,auth,card,main,executor}. On startup each agent
**registers itself** with ACE (kit `AgentRegistrar` + its team's token):
card validated, Casbin policies seeded, version snapshotted, and its skill
examples embedded into the **route index** — so the question router knows it
immediately, with cross-agent overlap warnings at registration. Enforcement
is layered: `agent:<key> chat` to reach it, `knowledge:<source> read` per
role, AND the source registry's agent binding (only agents the owning team
bound to a source can retrieve from it). From the UI, questions route
directly by similarity (one embedding + one pgvector lookup — no supervisor,
no LLM hop); ambiguous questions get one-tap disambiguation chips; questions
matching an inaccessible agent get the polite contact-the-team refusal.

## Setup with uv — full install commands

Prereqs: Python 3.13+, [uv](https://docs.astral.sh/uv/), Node 22, Postgres 17
with pgvector. Every library the platform uses, unpinned — uv resolves the
latest releases. Each `uv add` is one line: copy, paste, done.

```powershell
# ── ACE control plane ──────────────────────────────────────────  (A2A\ACE)
uv init --python 3.13
uv add --editable ..\agent_kit
uv add a2a-sdk asyncpg azure-core azure-identity azure-keyvault-secrets azure-storage-blob casbin casbin-async-sqlalchemy-adapter databricks-sdk fastapi httpx itsdangerous "markitdown[all]" openai "pyjwt[crypto]" python-json-logger python-multipart pyyaml requests rich "sqlalchemy[asyncio]" sqlmodel tiktoken twilio "uvicorn[standard]"

# ── Any team agent ─────────────────────────────  (A2A\Agents\<your_agent>)
uv init --python 3.13
uv add --editable ..\..\agent_kit
uv add "a2a-sdk[http-server]" httpx "pyjwt[crypto]" pyyaml uvicorn

# ── Shared kit (only if rebuilding it) ─────────────────  (A2A\agent_kit)
uv init --lib --python 3.13
uv add a2a-sdk httpx

# ── Frontend ───────────────────────────────────────────  (A2A\ACE\frontend)
npm install
```

`ace-agent-kit` is always an **editable local path dependency** (never
published) — one shared implementation of ContextEnvelope, AgentDelegator,
AceCapabilityClient, and PromptStore for ACE and every agent. After
installing, run with `uv run python -m ...` as shown below.

## Run guide (CLI)

Run each block in its own terminal, from the folder shown. Order: ACE first,
then agents, then frontend.

```powershell
# 1. ACE control plane — auth, RBAC, router, registry, channels   (A2A\ACE)
$env:ACE_DB_POSTGRES_USER="postgres"; $env:ACE_DB_POSTGRES_PASSWORD="12345678"; $env:ACE_DB_POSTGRES_DBNAME="postgres"
uv run python -m backend.app.app                    # :3000  (Swagger at /docs)

# 2. ONE-TIME (admin): register teams + issue registration tokens
#    POST /api/v1/admin/agents/teams  {"key":"clinical_care", ...}
#    POST /api/v1/admin/agents/teams/clinical_care/tokens  -> shown ONCE
#    put each token in the team's agents' config/env/local.yaml (ace.registration_token)

# 3. Agents — same command in each folder; each SELF-REGISTERS on startup
uv run python -m app.main
#   general_agent          :3300     file_upload_agent   :3400
#   policy_procedure_agent :3500     sms_agent           :3700
#   benefits_agent         :3800     econsult_agent      :3900
#   gda_agent              :4000
# then activate once (admin): PATCH /api/v1/admin/agents/{key}/status {"status":"active"}

# 4. Chat + canvas UI   (A2A\ACE\frontend)
npm run dev                                         # :5173 → Entra login → leave
                                                    # assistant on "Auto" — questions
                                                    # route directly to the right agent

# Health check after credential changes (admin session required)
# GET http://localhost:3000/api/v1/admin/health/integrations
```

**Channel webhooks** (separate from UI chat, both authenticated):
Twilio SMS → `POST /api/v1/channels/sms/inbound` (X-Twilio-Signature) —
inbound texts are answered by the channel-only **sms** agent; delivery
updates at `/status`. Microsoft Teams is **per-agent ONLY**: a team sets
`channels.teams.{enabled, webhook_secret}` in their env config and points
their Teams Outgoing Webhook at
`POST /api/v1/channels/teams/<agent_key>/messages` (HMAC with THEIR secret;
non-opted-in agents 404; there is no platform-wide Teams route). Teams
users' AAD object id adds their Entra-provisioned roles; bodies and
numbers/ids are AES-GCM encrypted at rest (`security.field_encryption_key`).

First run in any folder: `uv sync` (agents) / `npm install` (frontend).
Config lives in `ACE/backend/app/config/env/<ENV>.yaml` — one code path for
all environments; drop real credentials in and nothing else changes. Full
cred-swap checklist in `STATE.md`.
