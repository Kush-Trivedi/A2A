# ACE — Run & Deploy Guide

Three ways to run: **local dev** (uv/npm, fastest loop), **docker compose**
(cloud parity on one machine), **Azure Container Apps** (production shape).
Plus: how an ODT team ships a new agent end to end.

## 0. Where everything lives

Platform code is in **three folders** — `ACE/` (control plane), `Agents/`
(independent team agents), `agent_kit/` (platform-owned shared package).
The rest is docs + container files.

```
A2A/
├── ACE/                     # control plane: backend/app (api, services, security,
│                            #   entity, dto, config/env/<ENV>.yaml) + frontend SPA
├── agent_kit/               # shared ace-agent-kit package — editable path dep
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

# 2) Agents — same command in each agent folder   (A2A\Agents\<agent>)
uv run python -m app.main
#   scheduling_agent :3100   insurance_agent :3200   general_agent    :3300
#   file_upload_agent:3400   sharepoint_agent:3500   blob_agent       :3600
#   sms_outreach_agent:3700  benefits_agent  :3800

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

1. **Copy the scaffold** — duplicate any agent folder (e.g. `scheduling_agent`)
   under `Agents/<your_agent>`. Everything is yours: logic, config, prompts.
2. **Edit `agent.yaml`** (the only contract with ACE):
   - `agent:` team_key, agent_key, display_name, **version**
   - `skills:` what you can do (id/name/description/tags)
   - `prompts:` your versioned prompts (name → version + content)
   - `llm:` your Foundry **deployment names** (base URL + key stay in ACE)
   - `data:` your Databricks/SharePoint/Blob resources (warehouse, catalog,
     genie space, site, container...)
   - `data.channels:` OPTIONAL channel opt-ins — e.g. `teams: {enabled: true,
     webhook_secret: ...}` if you want your agent in Microsoft Teams (§4b);
     omit for chat-UI only
   - `ace:` permission + **allowed_roles** (who may use your agent) +
     knowledge_sources (which `sharepoint:*`/`blob:*` sources you answer from)
   - `auth:` enabled + your Entra app audience (when going live)
3. **Write your logic** in `app/agent_executor.py` — retrieval via
   `AceCapabilityClient.retrieve`, LLM via `.llm_chat(deployment=...)`,
   cross-team calls via `AgentDelegator` (always forward the envelope).
4. **Test locally** — `uv sync && uv run python -m app.main`, then card check:
   `GET http://localhost:<port>/.well-known/agent-card.json`.
5. **Register with ACE** (running agent required — ACE validates the card):
   ```powershell
   uv run python -m app.register --cookie "<ace_session>" --csrf "<csrf>"
   # then activate (admin):
   # PATCH /api/v1/admin/agents/<agent_key>/status   {"status":"active"}
   ```
   Registration auto-seeds Casbin policies from `allowed_roles`, snapshots the
   version (prompts included), and the agent appears ONLY for permitted roles.
6. **Load your knowledge (if any)** — `POST /api/v1/knowledge/ingest/sharepoint`
   or `/ingest/blob` with your source_name + location → returns a job_id;
   poll `GET /api/v1/knowledge/ingest/jobs/{job_id}`.
7. **Ship to cloud** — add one line to the `agents` array in `infra/main.bicep`
   (name/team/port), `az acr build` with your AGENT arg, redeploy, then
   re-register with the internal FQDN as card_url.
8. **Iterate** — bump `version` in agent.yaml, re-register (new snapshot),
   roll back anytime: `POST /api/v1/admin/agents/<key>/versions/<v>/activate`.

## 4b. Channels (SMS + Teams webhooks)

Both are standalone authenticated webhooks — separate from UI chat — and
route inbound messages through the same agents (channel roles `sms_patient` /
`teams_user`; bodies and numbers/ids AES-GCM encrypted at rest).

```text
Twilio SMS  : point the Twilio number's webhook at
              https://<ace>/api/v1/channels/sms/inbound   (X-Twilio-Signature)
              delivery callbacks -> /api/v1/channels/sms/status
              config: twilio.* incl. messaging.default_agent, opt keywords
Teams       : OPT-IN PER AGENT — a team that wants their agent in Microsoft
              Teams declares it in their agent.yaml:
                data:
                  channels:
                    teams:
                      enabled: true
                      webhook_secret: "<token Teams generated>"
              then creates an Outgoing Webhook in Teams with callback
              https://<ace>/api/v1/channels/teams/<agent_key>/messages
              (HMAC over raw body with THEIR secret). Agents without the
              block are NOT reachable over Teams (404). Signed-in Teams
              users' aadObjectId drives RBAC via user_role_assignments.
              Platform default route /channels/teams/messages uses yaml
              microsoft.microsoft_teams.outgoing_webhook_secret.
              app manifest template: teams-app-manifest.json
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

Fill in `ACE/backend/app/config/env/<ENV>.yaml` (or Key Vault `lookup:` refs):
`microsoft.entra.*` (+ redirect URI in the app registration),
`database.postgres.*`, `microsoft.azure.azure_foundry.*` (then flip agents'
`retrieval_mode` to `hybrid`), `databricks.host/token` (PAT),
`microsoft.sharepoint.*`, `microsoft.azure.storage_account.*`,
`twilio.*` (+ webhook_base_url), `microsoft.microsoft_teams.
outgoing_webhook_secret`, `security.field_encryption_key` +
`security.identity_hash_pepper` (PHI at-rest encryption / phone hashing),
and each agent's `auth:` section. Zero code changes — run the checklist in
`STATE.md`.
