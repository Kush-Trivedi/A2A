# ACE — Run & Deploy Guide

Three ways to run: **local dev** (uv/npm, fastest loop), **docker compose**
(cloud parity on one machine), **Azure Container Apps** (production shape).
Plus: how an ODT team ships a new agent end to end.

## 0. Where everything lives

Platform code is in **three folders** — `ACE/` (control plane), `Agents/`
(independent team agents), `AgentKit/` (platform-owned shared package).
The rest is docs + container files.

```
A2A/
├── ACE/                     # control plane: backend/app (api, services, security,
│                            #   entity, dto, config/env/<ENV>.yaml) + frontend SPA
├── AgentKit/               # shared ace-agent-kit package — editable path dep
│                            #   installed by ACE and every agent (platform-owned)
├── Agents/                  # one uv project per team agent, all owned by the team:
│   ├── scheduling_agent/    #   :3100   insurance_agent/   :3200   general_agent/ :3300
│   ├── file_upload_agent/   #   :3400   sharepoint_agent/  :3500   blob_agent/    :3600
│   ├── sms_outreach_agent/  #   :3700   benefits_agent/    :3800 (Teams opt-in)
│   └── Dockerfile           #   ONE shared agent image — ARG AGENT picks the folder
│                            #   (build context = repo root, so the kit is included)
├── infra/                   # reference Bicep (main/agent) — OPTIONAL: bring your
│                            #   own IaC; Foundry model deployments already exist
├── docker-compose.yml       # pgvector + ACE + all 8 agents + UI
├── Dockerfile.ace           # control-plane image
├── teams-app-manifest.json  # Teams app manifest template
└── README.md · PLATFORM_PLAN.md · STATE.md   (AAAS/ + AAAS.zip = old extract, deletable)
```

Deploy-relevant paths: config is ONLY `ACE/backend/app/config/env/<ENV>.yaml`
(+ each agent's `agent.yaml`); images build from the two Dockerfiles; nothing
else needs touching to go from laptop to cloud.

---

## 1. Local dev run (daily work)

One terminal per block, in order.

```powershell
# 1) ACE control plane                        (A2A\ACE)
$env:ACE_DB_POSTGRES_USER="postgres"; $env:ACE_DB_POSTGRES_PASSWORD="12345678"; $env:ACE_DB_POSTGRES_DBNAME="postgres"
uv run python -m backend.app.app             # :3000 — Swagger at /docs

# 2) Agents — same command in each agent folder; each SELF-REGISTERS on boot
uv run python -m app.main
#   general_agent :3300          file_upload_agent :3400
#   policy_procedure_agent :3500 sms_agent :3700
#   benefits_agent :3800         econsult_agent :3900   gda_agent :4000

# 3) Chat + canvas UI                         (A2A\ACE\frontend)
npm run dev                                  # :5173 → browser → Entra login
```

First time in any folder: `uv sync` (agents/ACE) or `npm install` (frontend).
Prereqs: Python 3.13, uv, Node 22, Postgres 17 + pgvector on :5432.

## 2. Docker compose run (cloud parity)

```powershell
# From A2A\  (Docker Desktop running)
docker compose up --build                    # pgvector + ACE + all 8 agents + UI
```

Same images that ship to Azure. In this mode register agents with **service
DNS card URLs** (`http://scheduling:3100/...`), not localhost — that's the
cloud behavior rehearsed locally.

## 3. Azure deploy (Container Apps)

One shared environment, one app per service. Ports disappear — the registry
`card_url` (internal DNS name) is the discovery.

> The `infra/` Bicep below is a **reference implementation** — if you already
> have your own IaC pipeline and model deployments, skip the Bicep steps and
> just point your pipeline at `Dockerfile.ace` and `Agents/Dockerfile`
> (`--build-arg AGENT=<agent_folder>`); everything else is unchanged.

```powershell
# 0) One-time infra
az group create -n ace-rg -l eastus2
az acr create -g ace-rg -n <acr> --sku Basic

# 1) Build + push images (from A2A\)
az acr build -r <acr> -f Dockerfile.ace -t ace/control-plane:latest .
az acr build -r <acr> -f Agents/Dockerfile --build-arg AGENT=scheduling_agent -t ace/scheduling-agent:latest .
#   ... repeat the agent line per agent (only AGENT and tag change)
az acr build -r <acr> -t ace/frontend:latest ACE/frontend

# 2) Deploy everything (shared CAE + ACE external + all agents internal)
az deployment group create -g ace-rg -f infra/main.bicep -p registry=<acr>.azurecr.io envName=dev
#   outputs: aceUrl (public) + one internal FQDN per agent

# 3) Point config at reality
#    - ENV=dev is set in the images; fill ACE dev.yaml (or Key Vault lookups)
#      with real Entra/DB/Foundry/PAT values BEFORE building, or mount config.
#    - Entra app redirect URI: https://<aceUrl>/api/v1/auth/callback

# 4) Register each agent with its cloud card_url (see §4 step 5)
```

Scaling knobs live in `infra/agent.bicep` per agent: cpu/memory,
min/max replicas (min 0 = scale-to-zero), team cost tags. AKS later: same
images, namespaces per team — migration = updating card_urls in the registry.

## 4. How a TEAM ships a new agent (the onboarding path)

Prerequisite (once per team, by an ACE admin): register the team and issue
its **registration token** (`POST /api/v1/admin/agents/teams` then
`POST /api/v1/admin/agents/teams/<key>/tokens` — shown once; store it in
the team's Key Vault).

1. **Copy the scaffold** — `Agents/_template/` (in the monorepo now; your own
   Azure DevOps repo later — swap the kit path dep for the Artifacts feed).
2. **Register your connections** (once): `POST /api/v1/connections` with your
   SharePoint site / storage account / Databricks workspace / Twilio number —
   secrets encrypted at rest, referenced by NAME from then on.
3. **Ingest your data** (if you answer from documents): Data Onboarding UI,
   `POST /api/v1/knowledge/ingest/source`, or the kit CLI
   (`python -m ace_agent_kit.ingest --config ingest.yaml`) — you choose
   connection, location, chunking, embedding, and `access.{agents, roles}`
   (which agents may read the source, which user roles may see results).
4. **Edit `agent.yaml`** (env-invariant manifest): identity + version,
   `skills` with realistic **examples** (they ARE your routing — ACE warns at
   registration if they overlap another agent), versioned `prompts`.
5. **Edit `config/env/*.yaml`** (same keys all four): ports/URLs, your
   registration token (`lookup:` in cloud), LLM **deployment names**,
   `retrieval.knowledge_sources`, `connections.*` refs, `channels.*` opt-ins
   (ui/teams/sms — unused stay `enabled: false`).
6. **Run it** — `uv sync && uv run python -m app.main`. The agent
   SELF-REGISTERS with ACE on startup (idempotent, retries until ACE is up —
   same flow local and cloud). Then an admin activates:
   `PATCH /api/v1/admin/agents/<key>/status {"status":"active"}`.
7. **Ship to cloud** — build the shared agent image with your AGENT arg,
   deploy to your Container App; only `config/env/<ENV>.yaml` values differ.
8. **Iterate** — bump `version`, redeploy (startup re-registers → new
   snapshot); roll back anytime:
   `POST /api/v1/admin/agents/<key>/versions/<v>/activate`.

## 4b. Channels (SMS + Teams webhooks)

Both are standalone authenticated webhooks — separate from UI chat — and
route inbound messages through the same agents (channel roles `sms_patient` /
`teams_user`; bodies and numbers/ids AES-GCM encrypted at rest).

```text
Twilio SMS  : point the platform number's webhook at
              https://<ace>/api/v1/channels/sms/inbound   (X-Twilio-Signature)
              delivery callbacks -> /api/v1/channels/sms/status
              inbound texts are answered by the channel-only SMS agent
              (twilio.messaging.default_agent: sms). twilio.* stays in ACE
              yaml DELIBERATELY: the inbound number is platform channel
              infra, like Entra — per-team numbers become connections later.
Teams       : PER-AGENT ONLY (no platform-wide route). The team sets in
              config/env/<ENV>.yaml:
                channels:
                  teams:
                    enabled: true
                    webhook_secret: "<token Teams generated>"   # lookup: in cloud
              then creates an Outgoing Webhook in Teams with callback
              https://<ace>/api/v1/channels/teams/<agent_key>/messages
              (HMAC over raw body with THEIR secret; non-opted-in agents
              404). Signed-in Teams users' aadObjectId drives RBAC via
              user_role_assignments. Manifest template: teams-app-manifest.json
Outreach    : scheduled/one-way sends (Service Bus trigger later) call
              POST /api/v1/capability/sms/send (envelope + agent_key)
```

Behind APIM: put both webhook paths in their own APIM product with IP
restrictions (Twilio/Microsoft ranges) + rate policies.

## 5. Operations quick reference

| What | How |
|---|---|
| Integration health | `GET /api/v1/admin/health/integrations` (postgres, databricks, keyvault, llm, every agent card) |
| Startup config check | boot logs — validator names every placeholder yaml path |
| Agent inventory | `GET /api/v1/admin/agents` · versions: `GET /api/v1/admin/agents/{key}/versions` |
| Enable/disable agent | `PATCH /api/v1/admin/agents/{key}/status` |
| Rollback | `POST /api/v1/admin/agents/{key}/versions/{v}/activate` |
| Policies | `GET/POST /api/v1/admin/policies` · reload: `POST /api/v1/admin/policies/reload` (auto-reload every 300s too) |
| Ingestion jobs | `GET /api/v1/knowledge/ingest/jobs/{job_id}` |
| Rate limits / pools / TTLs | yaml: `security.rate_limit`, `database.postgres.pool_size`, `agents.card_cache_ttl_seconds`, `authorization.policy_reload_seconds` |
| Capability API (agents → ACE) | `POST /api/v1/capability/knowledge/retrieve` · `/capability/llm/chat` (team-registered deployments only) · `/capability/sms/send` · `/capability/agents/catalog` |
| SMS/Teams conversation data | tables `sms_conversations`/`sms_messages`, `teams_conversations`/`teams_messages` (encrypted; delivery status persisted) + full transcripts in `chat_sessions`/`chat_messages` |

## 6. Credential swap (before first real login)

ACE yaml is INFRA-ONLY now. Fill in `ACE/backend/app/config/env/<ENV>.yaml`
(or Key Vault `lookup:` refs): `microsoft.entra.*` (+ redirect URI),
`database.postgres.*`, `microsoft.azure.azure_foundry.*` (base + key — then
flip agents' `retrieval.mode` to `hybrid`), `twilio.*` (platform inbound
number), `security.field_encryption_key` + `security.identity_hash_pepper`.

Everything team-owned moved OUT of ACE yaml: SharePoint / storage / Databricks
workspaces are **connections** (`POST /api/v1/connections`, secrets encrypted
at rest), Teams webhook secrets and LLM deployment names live in each agent's
`config/env/<ENV>.yaml` (`lookup:` against the TEAM's Key Vault), and
registration tokens are issued per team. Zero code changes anywhere — run
`TESTING.md` end to end after the swap.
