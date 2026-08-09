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

All platform code lives in two folders: `ACE/` (control plane) and `Agents/`
(independent team agents). Everything else at the root is docs, container
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
├── Agents/                     # TEAM-OWNED, independent A2A services (uv project each)
│   ├── agent_kit/              # shared ace-agent-kit: ContextEnvelope, AgentDelegator,
│   │                           #   AceCapabilityClient, PromptStore, auth helpers
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

## The agents

| Agent (team/key) | Port | Skills | Data scope |
|---|---|---|---|
| **Scheduling** (clinical_care/scheduling) v0.2.0 | 3100 | schedule_appointment, check_availability | none — delegates insurance checks to Pay Ops (envelope + referenceTaskIds); versioned prompts `booking_ack`, `delegation_reason` |
| **Insurance** (pay_ops/insurance) | 3200 | verify_insurance, appeal_claim | none — proves received envelope (actor/tenant/delegated_from) |
| **General Assistant** (ace_platform/general) | 3300 | general_help, access_overview | none — lists the caller's role-scoped agents via the capability catalog |
| **File Q&A** (ace_platform/file_qa) | 3400 | file_question_answering | session-strict: ONLY files uploaded in the caller's chat session (📎 in composer → markitdown[all]) |
| **Policy Library** (clinical_care/sharepoint_qa) | 3500 | policy_question_answering | `sharepoint:policies` — loaded via `POST /api/v1/knowledge/ingest/sharepoint` |
| **Claims Archive** (pay_ops/blob_qa) | 3600 | claims_document_answering | `blob:claims` — loaded via `POST /api/v1/knowledge/ingest/blob` |
| **SMS Outreach** (clinical_care/sms_outreach) | 3700 | send_sms_notification | one-way patient SMS via ACE Twilio capability (creds stay in ACE, opt-outs enforced centrally) |
| **Benefits** (hr_benefits/benefits) | 3800 | benefits_question_answering | none yet — Teams-channel opt-in example: `data.channels.teams.{enabled, webhook_secret}` in its agent.yaml → reachable at `/channels/teams/benefits/messages`; signed-in Teams users' AAD id drives RBAC |

Every agent: identical scaffold (`agent.yaml` + config/auth/card/main/executor +
`ace-agent-kit`), serves its AgentCard at `/.well-known/agent-card.json`,
registers with ACE (card validated, Casbin policies auto-seeded from
`allowed_roles`, immutable version snapshot recorded), and is enforced twice —
`agent:<key> chat` to reach it, `knowledge:<source> read` per source per role.
Prompts are team-authored in `agent.yaml` (`prompts:` name → version+content),
used at runtime via the kit `PromptStore`; ACE records them per agent version
(`GET /api/v1/admin/agents/{key}/versions`, `POST .../versions/{v}/activate`
to roll back).

## Run guide (CLI)

Run each block in its own terminal, from the folder shown. Order: ACE first,
then agents, then frontend.

```powershell
# 1. ACE control plane — auth, RBAC, chat API, registry, audit   (A2A\ACE)
$env:ACE_DB_POSTGRES_USER="postgres"; $env:ACE_DB_POSTGRES_PASSWORD="12345678"; $env:ACE_DB_POSTGRES_DBNAME="postgres"
uv run python -m backend.app.app                    # :3000  (Swagger at /docs)

# 2. Scheduling agent — books appointments, delegates to Insurance   (A2A\Agents\scheduling_agent)
uv run python -m app.main                           # :3100

# 3. Insurance agent — coverage checks for delegations   (A2A\Agents\insurance_agent)
uv run python -m app.main                           # :3200

# 4. General assistant — safe Q&A + "what can I access"   (A2A\Agents\general_agent)
uv run python -m app.main                           # :3300

# 5. File Q&A agent — answers only from your session uploads   (A2A\Agents\file_upload_agent)
uv run python -m app.main                           # :3400

# 6. Policy Library agent — SharePoint-sourced answers   (A2A\Agents\sharepoint_agent)
uv run python -m app.main                           # :3500

# 7. Claims Archive agent — blob-sourced answers   (A2A\Agents\blob_agent)
uv run python -m app.main                           # :3600

# 7b. SMS Outreach agent — one-way patient notifications   (A2A\Agents\sms_outreach_agent)
uv run python -m app.main                           # :3700

# 7c. Benefits agent — Teams-enabled HR benefits Q&A   (A2A\Agents\benefits_agent)
uv run python -m app.main                           # :3800

# 8. Chat + canvas UI   (A2A\ACE\frontend)
npm run dev                                         # :5173 → open in browser, Entra login

# One-time per agent (after it's running): register + activate with ACE
uv run python -m app.register --cookie "<ace_session>" --csrf "<csrf_token>"
# then: PATCH /api/v1/admin/agents/{agent_key}/status {"status":"active"}

# Health check after credential changes (admin session required)
# GET http://localhost:3000/api/v1/admin/health/integrations
```

**Channel webhooks** (separate from UI chat, both authenticated):
Twilio SMS → `POST /api/v1/channels/sms/inbound` (X-Twilio-Signature) with
delivery updates at `/status`. Microsoft Teams is **opt-in per agent**: a team
declares `data.channels.teams.{enabled, webhook_secret}` in their agent.yaml
and points their Teams Outgoing Webhook at
`POST /api/v1/channels/teams/<agent_key>/messages` (HMAC over raw body with
their secret, inline reply; agents without the block 404 on that channel;
manifest template: `teams-app-manifest.json`). The platform default route
`/channels/teams/messages` uses the yaml secret. Inbound messages route
through the same agents with channel roles (`sms_patient`, `teams_user` —
Teams users' AAD object id adds their Entra-provisioned roles); bodies and
numbers/ids are AES-GCM encrypted at rest (`security.field_encryption_key`).

First run in any folder: `uv sync` (agents) / `npm install` (frontend).
Config lives in `ACE/backend/app/config/env/<ENV>.yaml` — one code path for
all environments; drop real credentials in and nothing else changes. Full
cred-swap checklist in `STATE.md`.
