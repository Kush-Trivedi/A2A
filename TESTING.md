# TESTING.md — Phase 19 handoff runbook (macOS)

Audience: an AI assistant helping test this platform on a Mac. Everything
below was already executed and verified on a Windows dev box — commands here
are translated to macOS (zsh/bash). **No code changes should be needed.**
Config VALUES (yaml) are the only thing you edit. The single untested piece
is Entra login from the UI — see §9.

## 0. What is already verified (do not re-debug these)

Full local run passed with placeholder creds (no Entra, no Foundry, no real
SharePoint/Twilio):

- ACE :3000 + 7 agents (general :3300, file_qa :3400, policy_procedure :3500,
  sms :3700, benefits :3800, econsult :3900, gda :4000) all boot clean.
- Teams + registration tokens seeded → all 7 agents SELF-REGISTERED over HTTP
  on startup (kit registrar, idempotent, retries 30×5s).
- Question router (sparse FTS mode, no LLM creds): policy→policy_procedure,
  enrollment→benefits, econsult→econsult, admissions→gda, nonsense→general
  fallback, inaccessible-match→refusal naming the agent/team.
- Two full auto-routed chat turns answered by LIVE A2A agents.
- Dynamic agent-to-agent consult: econsult (empty sources) resolved
  policy_procedure via `POST /api/v1/capability/agents/resolve` (user RBAC
  enforced on the hop) and returned its answer attributed:
  `[eConsult Agent → consulted Policy & Procedure Agent] ...`.
- Agents stream LLM tokens end-to-end (llm_chat_stream → A2A chunks → SSE).
  With placeholder Foundry creds the grounded-snippet fallback answers — that
  is DESIGNED degraded mode, not a bug.

## 1. Setup

Prereqs: Python 3.13, uv, Node 22, Postgres 17 + pgvector running locally.

```bash
cd A2A/AgentKit && uv sync
cd ../ACE && uv sync
for a in general_agent file_upload_agent policy_procedure_agent \
         econsult_agent sms_agent benefits_agent gda_agent; do
  (cd ../Agents/$a && uv sync)
done
cd ../ACE/frontend && npm install
```

DB tables auto-create on ACE startup (entities are the schema source of
truth; there is no migration list — a stale dev DB from older code gets
DROPPED, not patched).

## 2. Start ACE

```bash
cd A2A/ACE
export ACE_DB_POSTGRES_USER=postgres ACE_DB_POSTGRES_PASSWORD=<pw> ACE_DB_POSTGRES_DBNAME=postgres
uv run python -m backend.app.app        # :3000
curl -s http://localhost:3000/api/healthcheck   # expect 200
```

Expected: startup validator lists remaining `your_*` placeholders by exact
yaml path. No databricks/sharepoint/storage/teams sections exist in ACE yaml
anymore (that's correct — connections own them now).

## 3. Seed teams + issue registration tokens

Entra login is not live yet, so use the service-level scripts in
`A2A/testing/` — these are the EXACT scripts the verification ran (fake
admin SessionContext, real services):

```bash
cd A2A/ACE
export PYTHONPATH=$PWD
uv run python ../testing/seed_teams_tokens.py tokens.json   # 4 teams + tokens
```

(Once Entra works, the same is `POST /api/v1/admin/agents/teams` +
`POST .../teams/<key>/tokens` with an admin session.)

Put each token into the team's agents' `config/env/local.yaml` →
`ace.registration_token`. Mapping: ace_platform → general, file_upload;
clinical_care → policy_procedure, econsult, sms; hr_benefits → benefits;
data_analytics → gda.

## 4. Start the 7 agents — they register THEMSELVES

```bash
cd A2A/Agents/<agent_folder> && uv run python -m app.main   # one terminal each
```

**Do NOT judge registration by agent logs** — the kit logs at INFO with no
handler, so success lines are invisible; only warnings print. Verify + 
activate in one step instead:

```bash
uv run python ../testing/check_registrations.py
```

Expected: 7 rows, routes 6–9 each (skill examples ARE the route
utterances), ends `ACTIVATED_ALL_PRESENT`.

## 5. Router + chat E2E (how it was tested)

```bash
uv run python ../testing/router_chat_e2e.py    # requires ACE + agents running
```

What it does (fake `SessionContext(roles=("developer",))`, real services):
routes 5 questions and asserts the §0 dispatch targets; routes a benefits
question as `sms_patient` → `refusal_inaccessible`; then TWO real chat turns
— auto-routed "What can I access?" answered by the live general agent via a
full A2A round trip (agent calls back into ACE's capability catalog), and an
explicit benefits turn. Ends `E2E_DONE`.

## 6. Dynamic cross-agent consult E2E (how it was tested)

```bash
uv run python ../testing/consult_e2e.py        # requires ACE + agents running
```

What it does: registers `sharepoint:policies` in the source registry (owner
clinical_care, bound agent policy_procedure, roles developer/nurse); plants
one policy doc directly (embeddings unconfigured → real ingestion would
fail; the chunk row MUST carry metadata `{"knowledge_source":
"sharepoint:policies"}` — retrieval scopes on chunk metadata, not the
documents column); then asks ECONSULT the policy question. Expected: econsult
finds nothing in its own sources → resolves policy_procedure through ACE
(user RBAC enforced) → real A2A delegation → answer attributed
`[eConsult Agent → consulted Policy & Procedure Agent]` containing the
Hand Hygiene Policy text. Ends `CONSULT_E2E_DONE`.

## 7. Channels

- Teams: per-agent ONLY (`/api/v1/channels/teams/<agent_key>/messages`);
  the platform-wide route was removed. Benefits opts in via
  `channels.teams.enabled` in its env yaml. HMAC with the team's secret.
- SMS: `POST /api/v1/channels/sms/inbound` → answered by the channel-only
  `sms` agent (`twilio.messaging.default_agent: sms`). Twilio yaml stays in
  ACE deliberately (platform inbound number = channel infra).

## 8. Troubleshooting — errors ACTUALLY HIT during verification

| Symptom | Cause | Fix |
|---|---|---|
| `ImportError: oauth_compact_router` on ACE start | export chain missed the new auth router | FIXED in code (api/__init__ + routers/__init__). Should not recur. |
| `RENAME COLUMN IF EXISTS` SQL error at startup | invalid Postgres syntax in old upgrade list; only fired on DBs still carrying the old column | Upgrade list DELETED. If a stale dev DB errors on schema: drop its tables (or the DB) and restart — create_all rebuilds everything. |
| `TypeError: Starlette.__init__() got 'on_startup'` | newer Starlette removed on_startup | FIXED (lifespan context in agent main.py). Should not recur. |
| Agents show no "Registered with ACE" log | kit INFO logs have no handler | Not an error — verify via the SQL in §4. |
| An agent missing from registry right after startup | ACE fetches the agent's card during registration; agent was still booting | Registrar retries every 5s ×30 — wait ~1 min or restart the agent. |
| Duplicate agent rows / one agent failing to register | stale rows from older test runs (old team ids) | Wipe registry state: DELETE FROM agent_versions, agent_routes, registered_agents, team_tokens, team_connections, knowledge_sources, odt_teams; DELETE casbin_rule p-rows where v2 LIKE 'agent:%' or 'knowledge:%'. Reseed §3, restart agents. |
| Router sends domain questions to general fallback | route rows missing skill examples (old bug) | FIXED (card service keeps `examples`). If seen: restart agents (re-registration rebuilds routes), confirm routes count ≥6 in §4 SQL. |
| Retrieval returns 0 chunks though doc exists | chunk metadata lacks `knowledge_source` key | Real ingestion always sets it. Hand-planted docs must include it (§6.2). |
| `EmbeddingError` when ingesting text/files | Foundry embedding creds are placeholders | Expected pre-creds. Use sparse retrieval + planted docs, or wait for creds. |
| Policies added by a script not honored by running ACE | server's Casbin loaded at startup | `POST /api/v1/admin/policies/reload` or restart ACE (auto-reload every 300s). |
| Port busy on restart | old process holding it | `lsof -ti:3000 \| xargs kill -9` (any port). |

## 9. Credential swap — local.yaml FIRST, then lookup: for dev/uat/prd

The parity rule: real creds go into `ACE/backend/app/config/env/local.yaml`
(and the agents' `config/env/local.yaml`) as PLAIN VALUES and everything is
tested on the laptop. dev/uat/prd carry the SAME KEYS with `lookup:<secret-
name>` values resolved from Key Vault. One code path — never a code change.

### 9.1 ACE `config/env/local.yaml` — keys to fill

| Key | Value |
|---|---|
| `microsoft.entra.tenant_id / client_id / client_secret` | from the Entra app registration |
| `microsoft.entra.redirect_uri` | `http://localhost:3000/api/v1/auth/callback` (must ALSO be registered on the Entra app) |
| `microsoft.entra.post_login_redirect_uri` | `http://localhost:5173` |
| `microsoft.entra.post_logout_redirect_uri` | `http://localhost:5173` |
| `microsoft.entra.scopes / api_audience` | per the app registration |
| `microsoft.azure.azure_foundry.base_endpoint / api_key` | the ONE shared Foundry credential |
| `microsoft.azure.azure_foundry.embedding.deployment_name / api_version` | real embedding deployment |
| `microsoft.azure.azure_foundry.text_completion.deployment_name / api_version` | real chat deployment |
| `security.field_encryption_key` | generate: `python -c "import secrets;print(secrets.token_urlsafe(32))"` |
| `security.identity_hash_pepper` | generate likewise |
| `twilio.*` | only if testing SMS (platform inbound number) |

NOT in ACE yaml anymore (by design): databricks, sharepoint, storage — those
are team CONNECTIONS (`POST /api/v1/connections`, §7 of DEPLOY.md).

### 9.2 Each agent's `config/env/local.yaml` — keys to fill

| Key | Value |
|---|---|
| `ace.registration_token` | reissue via `testing/seed_teams_tokens.py` (tokens in git are placeholders) |
| `llm.deployments.chat` | the team's REAL Foundry deployment name (names only — key stays in ACE) |
| `retrieval.mode` | flip `sparse` → `hybrid` once embeddings work |
| `channels.teams.webhook_secret` | only for Teams-enabled agents (benefits) |
| `auth.*` | keep `enabled: false` locally until service-plane Entra apps exist per team |

### 9.3 What CHANGES once creds are in (verify these)

- Startup validator placeholder warnings disappear for the filled keys.
- UI login works: :5173 → "Sign in with Microsoft" → Entra → chat screen.
- Agents answer via STREAMED LLM tokens (the grounded-snippet fallback
  stops appearing) — same questions as §5, through the UI, picker on "Auto".
- Router switches sparse → dense automatically (`agents.router.mode: auto`).
- Real ingestion works (no more `EmbeddingError`): register a connection,
  ingest via Data Onboarding, re-run §6 without the planted doc.
- `GET /api/v1/admin/health/integrations` reports the filled integrations ok.

### 9.4 dev / uat / prd — same keys, lookup: values

In each env yaml the SAME keys hold `lookup:<secret-name>` instead of
values, e.g. `client_secret: "lookup:entra-client-secret"`. Resolution needs
`microsoft.azure.keyvault.keyvault_url` (+ optional `keyvault_secret_prefix`)
and, in cloud, `microsoft.azure.managed_identity_client_id`. Agents use the
SAME mechanism against the TEAM's own Key Vault (their env yamls already
carry `lookup:` refs and `azure.keyvault.*` placeholders). Env-var override
always wins (`ACE_*` for ACE, `AGENT_<SECTION>_<KEY>` for agents). Nothing
else differs between environments.

### 9.5 If something fails after the swap

**Yaml values only — suspect config before code.** The single place a code
fix may be justified is the Entra UI login flow itself (redirect loop, token
rejection, session cookie not set) — it is implemented but was never
exercised with live creds. If it fails, collect: browser network trace of
`/api/v1/auth/login` → `/api/v1/auth/callback`, ACE log lines, and the exact
Entra app registration settings (redirect URI, scopes, audience).

## 10. Adding a NEW agent, step by step

Example: the Pharmacy team wants a **pharmacy** agent.

**Step 0 — Team + token (once per team, admin, ~1 minute)**
Only if the team is new: register it and issue its registration token.
`POST /api/v1/admin/agents/teams {"key":"pharmacy_ops", ...}` →
`POST /api/v1/admin/agents/teams/pharmacy_ops/tokens` → token shown **once**,
team stores it in their Key Vault. If the team already exists with a token,
skip this entirely.

**Step 1 — Copy the scaffold**
`Agents/_template/` → `Agents/pharmacy_agent/` (later: their own Azure DevOps
repo — identical content, just swap the kit path dep for the Artifacts feed).
Rename `name` in pyproject.toml.

**Step 2 — Edit `agent.yaml`** (the manifest — never varies by environment)
Identity (`team_key: pharmacy_ops`, `agent_key: pharmacy`, display name,
version), **skills with realistic examples** — spend the most care here,
because the examples ARE the routing ("Is amoxicillin in stock?", "What's the
formulary alternative for X?"), versioned prompts, and optionally
`delegations.consult: [policy_procedure]` if it should consult a peer when
its own sources come up empty.

**Step 3 — Edit `config/env/local.yaml`** (and dev/uat/prd — same keys,
different values)
A unique port (:4100), `ace.base_url` + `public_url`, `allowed_roles` (who
may use it), **`registration_token`** (paste the team token; `lookup:` ref in
cloud envs), the LLM deployment name, `retrieval.knowledge_sources` if it
answers from documents, and `channels` opt-ins (Teams/SMS stay
`enabled: false` unless wanted).

**Step 4 — Data first, if it's a retrieval agent**
Register the team's connection once (`POST /api/v1/connections`), then ingest
via Data Onboarding / kit CLI with `access: {agents: ["pharmacy"],
roles: [...]}` — that binds the source to the new agent and seeds read
policies before it ever answers.

**Step 5 — Run it**
```bash
uv sync && uv run python -m app.main
```
That's the registration. On startup the agent announces itself to ACE: card
validated, Casbin policies seeded from `allowed_roles`, version + prompts
snapshotted, skill examples embedded into the route index, and
**route-overlap warnings** returned if its examples collide with an existing
agent (sharpen them if so). Start order doesn't matter — it retries until
ACE is up.

**Step 6 — Admin activates (the one deliberate human gate)**
`PATCH /api/v1/admin/agents/pharmacy/status {"status":"active"}`. Nothing is
user-reachable until an admin approves it.

**Done.** From that moment the router dispatches pharmacy questions to it
automatically, it appears in the catalog for permitted roles, peers can
consult it dynamically — **with zero ACE changes: no config edit, no restart,
no deploy, no code anywhere.** The platform never knew "pharmacy" existed
until the agent introduced itself.

**And tomorrow's tomorrow — updating it:** edit prompts/skills in agent.yaml,
bump `version`, redeploy/restart. The boot-time registration is an idempotent
upsert, so every restart re-registers harmlessly; a version bump creates a
new immutable snapshot (prompts included), the route index rebuilds from the
new examples, and `POST /admin/agents/pharmacy/versions/<v>/activate` rolls
back if the new prompts misbehave. Same flow on your laptop and in Container
Apps — only the yaml values differ.
