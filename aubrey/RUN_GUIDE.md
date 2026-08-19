# Aubrey — Local Run Guide

End-to-end steps to bring up the platform, register teams and agents, ingest
documents, and get streamed answers. Admin operations (teams, tokens,
activation, connections, ingestion) are done in **Swagger** (`/docs`); answer
streaming is consumed by the **frontend UI** (curl fallback included for
debugging).

## Topology

| Process | Where | Port | Start command |
|---|---|---|---|
| aubrey backend | `aubrey/` | 3000 | `uv run python -m backend.app.app` |
| frontend UI | your frontend project | 5173 | your dev command (e.g. `npm run dev`) |
| general agent | `aubrey/agents/` | 8100 | `uv run python -m general_agent.app.main` |
| benefit agent | `aubrey/agents/` | 8101 | `uv run python -m benefit_agent.app.main` |
| policy & procedure agent | `aubrey/agents/` | 8102 | `uv run python -m policy_procedure_agent.app.main` |
| file agent | `aubrey/agents/` | 8103 | `uv run python -m file_agent.app.main` |

Data pairing (who is grounded on what):

| Agent | Team | Knowledge source |
|---|---|---|
| `general` | `platform` | live agent catalog (no documents) |
| `file_agent` | `platform` | **session uploads** — answers only from files uploaded into the current conversation |
| `benefit_agent` | `hr-team` | **SharePoint** ingestion |
| `policy_procedure_agent` | `hr-team` | **Blob storage** ingestion |

The pairing is created at **ingest time** (`agent_key` on the ingest request
creates the document grants) — agent code is identical either way.

## 0. Prerequisites (one-time)

- Postgres with the **pgvector** extension, reachable with the creds in
  `backend/app/config/env/local.yaml` → `database.postgres`. Tables and
  indexes are created automatically at backend startup — no migration step.
- Real values in `local.yaml` for:
  - `microsoft.entra.*` — browser login (OAuth).
  - `microsoft.azure.azure_foundry.*` with a `text_completion` deployment —
    every agent streams its answer through `/capability/llm/chat/stream`.
  - `microsoft.sharepoint.*` — only for the SharePoint ingest (benefit agent).
  - Blob connections use `account_url` + `container` per connection (below);
    auth per your Azure setup.
  - NOT needed locally: embedding creds — `knowledge.retrieval.mode` and
    `agents.router.mode` are `sparse` (full-text), graph disabled.
- Install both uv projects (separate venvs):

  ```powershell
  cd aubrey;        uv sync
  cd aubrey\agents; uv sync
  ```

- Frontend origin: `http://localhost:5173` is already in
  `security.cors.allowed_origins`; add yours if it differs.
- If the backend loads the wrong env file, set `$env:ENV = "local"` first.

## 1. Start the aubrey backend (terminal 1)

```powershell
cd aubrey
uv run python -m backend.app.app
```

Port 3000. The startup validator prints the exact yaml path of any
placeholder credential — fix those before continuing.

## 2. Log in (browser)

Open `http://localhost:3000/api/v1/auth/login` → Entra sign-in → you are
redirected to `/docs` with the `aubrey_session` and `aubrey_session_csrf`
cookies set. The frontend shares the same cookies (same origin rules), so
logging in once covers both.

## 3. Authorize Swagger

In `/docs`, call **GET `/api/v1/auth/me`** — the response includes
`csrf_token`. Click **Authorize** and paste it (header `X-CSRF-Token`).
Authorization persists across page refreshes.

## 4. Register the teams

**POST `/api/v1/admin/teams`** — twice (keys must match each `agent.yaml`
manifest exactly). Two teams, two agents each:

```json
{"key": "platform", "name": "Platform Team", "description": "Owns the general and file agents",                "contact_email": "you@example.com"}
{"key": "hr-team",  "name": "HR Team",       "description": "Benefits coverage and policies & procedures",     "contact_email": "you@example.com"}
```

## 5. Issue team tokens (shown exactly once — copy each immediately)

- **POST `/api/v1/admin/teams/platform/tokens`** — for `general` + `file_agent`
- **POST `/api/v1/admin/teams/hr-team/tokens`** — for `benefit_agent` + `policy_procedure_agent`

## 6. Start the agents (terminals 2–4)

Agents **self-register on boot** (retry every 3 s for ~2 min, idempotent) —
you never register them by hand. Each terminal:

```powershell
# terminal 2 — general agent (8100)
cd aubrey\agents
$env:AGENT_TEAM_TOKEN = "<platform token>"
uv run python -m general_agent.app.main

# terminal 3 — file agent (8103; platform-owned, same token as general)
cd aubrey\agents
$env:AGENT_TEAM_TOKEN = "<platform token>"
uv run python -m file_agent.app.main

# terminal 4 — benefit agent (8101)
cd aubrey\agents
$env:AGENT_TEAM_TOKEN = "<hr-team token>"
uv run python -m benefit_agent.app.main

# terminal 5 — policy & procedure agent (8102)
cd aubrey\agents
$env:AGENT_TEAM_TOKEN = "<hr-team token>"
uv run python -m policy_procedure_agent.app.main
```

Watch for `[<agent_key>] registered with aubrey (attempt N)` in each.

## 7. Activate the agents

Registration leaves agents inactive on purpose (activation is an explicit
admin gate). **GET `/api/v1/admin/agents`** to confirm all three rows, then
**PATCH `/api/v1/admin/agents/{agent_key}/status`** with
`{"status": "active"}` for:

- `general`
- `benefit_agent`
- `policy_procedure_agent`
- `file_agent`

## 8. Register connections (one per team, once)

**POST `/api/v1/admin/connections`**:

Both belong to the HR team — one per source type:

```json
{
  "team_key": "hr-team",
  "connection_key": "benefits-sharepoint",
  "source_type": "sharepoint",
  "description": "Benefits document library",
  "config": {"site_path": "/sites/YourSite", "drive_name": "Documents"}
}
```

```json
{
  "team_key": "hr-team",
  "connection_key": "policy-blob",
  "source_type": "blob",
  "description": "Policy library container",
  "config": {"account_url": "https://<account>.blob.core.windows.net", "container": "policies"}
}
```

(The SharePoint hostname is tenant-wide and comes from
`microsoft.sharepoint.hostname` in the yaml — connections only differ by
site + drive.)

## 9. Ingest documents

The `agent_key` on the request is what grants that agent retrieval access
to the ingested chunks.

**POST `/api/v1/documents/ingest/sharepoint`** → feeds the benefit agent:

```json
{
  "team_key": "hr-team",
  "agent_key": "benefit_agent",
  "connection_key": "benefits-sharepoint",
  "folder_path": "Benefits",
  "file_name": null,
  "chunking_strategy": "recursive",
  "build_graph": false
}
```

**POST `/api/v1/documents/ingest/blob`** → feeds the policy agent:

```json
{
  "team_key": "hr-team",
  "agent_key": "policy_procedure_agent",
  "connection_key": "policy-blob",
  "prefix": "",
  "file_name": null,
  "blob_url": null,
  "chunking_strategy": "recursive",
  "build_graph": false
}
```

Keep `build_graph: false` locally (graph expansion needs the embedding
endpoint; local ingestion runs sparse/full-text). The response reports
`processed / linked / skipped / failed` for the batch.

## 10. Chat

**Primary — the frontend UI.** Log in through it (same Entra flow), open a
chat, ask away. The UI consumes `POST /api/v1/chat/stream` (SSE). Events it
must handle: `meta` (chosen agent + session id), `token` (answer text),
`artifact`, `state`, `disambiguation` (chips — user picks an agent),
`refusal`, `error`, `done`.

Routing checks:

- "What can you do?" → **general** (live catalog answer).
- A coverage question matching your SharePoint docs → **benefit_agent**,
  cited answer.
- A policy question matching your blob docs → **policy_procedure_agent**,
  cited answer.
- Upload a file into the session, then "Summarize the file I uploaded" →
  **file_agent**, answer strictly from the upload (see the file agent
  section below).
- Reusing the same session id = the token-budgeted memory window kicks in
  (multi-turn follow-ups work).

**Fallback — curl** (Swagger buffers SSE; use this to debug the stream).
Copy the `aubrey_session` cookie from browser dev tools:

```powershell
curl.exe -N http://localhost:3000/api/v1/chat/stream -H "Content-Type: application/json" -H "X-CSRF-Token: <csrf_token from /auth/me>" -H "Cookie: aubrey_session=<cookie value>" -d "{\"question\": \"What is the hand hygiene policy?\"}"
```

Body fields: `question` (required), `session_id` (omit to start a new
conversation), `agent_key` (pin an agent; omit to let the router decide).

## File upload → file agent

Whatever the user uploads into a conversation, `file_agent` answers from
that content **only** — its prompt hard-scopes it, and the storage model
makes cross-session leakage impossible (documents are keyed by tenant +
user + session and deleted with the session).

Flow:

1. **Upload into the session.** `POST /api/v1/files/upload` (multipart)
   with the extra form field `session_id=<chat session id>`. The prepared
   text is persisted against that session (`stored` in the response says
   how many documents landed; identical re-uploads are skipped). Without
   `session_id` the endpoint stays convert-and-return — nothing persisted.
   The UI flow: create/know the session id, upload with it, then ask.
2. **Ask in the same session.** The router reaches `file_agent` via its
   skill examples ("summarize the file I uploaded", ...); when the user
   attaches a file the UI should **pin it** with `"agent_key": "file_agent"`
   on `/chat/stream` — no routing ambiguity.
3. **The agent fetches server-side.** `file_agent` calls
   `POST /api/v1/capability/files/context` with its envelope; aubrey
   re-enforces roles and returns only that session's documents for that
   user. Newest uploads win the manifest's `max_context_chars` budget; a
   document cut mid-way gets an explicit truncation marker.
4. **No file yet** → the manifest-owned `no_file` answer, telling the user
   to attach a document. Not an error, never a guess.

Upload test without the UI (add the form field to Swagger's form, or):

```powershell
curl.exe http://localhost:3000/api/v1/files/upload -H "X-CSRF-Token: <csrf_token>" -H "Cookie: aubrey_session=<cookie value>" -F "file=@C:\path\to\document.pdf" -F "session_id=<session id>"
```

## SMS channel (Twilio)

The platform owns the Twilio number and all telephony; teams just register
a prompt-driven **campaign agent** (blood pressure outreach, payment
reminders, ...) and bind it to a campaign. Two campaign modes:
`outreach` (we send; replies are stored for the record, never answered)
and `bidirectional` (replies continue the conversation with the agent).

**Consent is the first gate, always.** No recorded opt-in → no outbound,
ever (TCPA: the burden of proving consent is on the sender). STOP /
STOPALL / UNSUBSCRIBE / CANCEL / END / QUIT / REVOKE / OPTOUT are honored
before anything else touches an inbound message; START / UNSTOP / YES
opt back in; HELP is recorded. Twilio's own filtering also auto-blocks and
auto-replies to these — aubrey records the transition and enforces it on
every future send (and a send rejected with error 21610 syncs the ledger).

Setup:

1. Fill `twilio:` in the env yaml — `account_sid`, `auth_token`,
   `phone_number` (or `messaging_service_sid`), and **`tenant_id` must be
   your Entra tenant id** (what `/auth/me` shows) or campaigns registered
   in Swagger won't resolve. `webhook_base_url` = the public https base
   Twilio calls (ngrok locally), used for both webhook config and
   signature validation.
2. In the Twilio console, point the number's inbound webhook to
   `<webhook_base_url>/api/v1/sms/webhooks/inbound` (POST). Status
   callbacks are requested per message automatically.
3. Start the campaign agent (same kit pattern — it self-registers):
   `$env:AGENT_TEAM_TOKEN = "<its team token>"; uv run python -m bp_outreach_agent.app.main`
   (port 8110, `permission: "sms"`, `allowed_roles: ["sms_user"]` — off
   the chat UI by the same Casbin rule that puts other agents on it).
   Activate it in Swagger.
4. Register the campaign: `POST /api/v1/admin/sms/campaigns`
   `{"key": "bp-outreach", "agent_key": "bp_outreach_agent", "mode": "bidirectional", "description": "..."}`
5. Record consent (from your signed intake form / portal checkbox):
   `POST /api/v1/admin/sms/consent`
   `{"phone": "+15551234567", "status": "opted_in", "note": "intake form #123"}`
6. Send outreach: `POST /api/v1/admin/sms/outreach`
   `{"campaign_key": "bp-outreach", "recipients": [{"phone": "+15551234567", "context": {"first_name": "Sam", "last_reading_days_ago": "14"}}]}`
   The agent's manifest prompt writes the message from those facts; the
   platform caps length (`twilio.sms.max_length`), sends, and records.
7. Audit everything: `GET /api/v1/admin/sms/messages?phone=+1...` shows
   each message's status journey (queued → sent → delivered/undelivered/
   failed) with error codes explained (30003 unreachable, 30007 carrier
   filtered, 30034 A2P 10DLC unregistered, ...), and
   `GET /api/v1/admin/sms/consent/{phone}` shows the full consent history.

Replies on a `bidirectional` campaign flow: webhook → signature check →
idempotency (Twilio retries can't double-reply) → keyword gate (local
keywords AND Twilio's forwarded `OptOutType`, both honored) → media gate
(MMS gets the yaml-owned `media_reply`, attachments aren't processed) →
**per-phone rate limit**
(`rate_limit_per_minute`, default 6 — floods are stored, not dispatched)
→ thread (phone+campaign = one chat session, channel "sms", same memory
window) → agent over A2A → capped reply via REST. The webhook itself
always returns empty TwiML immediately; the LLM turn runs in the
background. Inbound rows also keep the raw webhook details
(SmsStatus, To/FromCountry, AccountSid) in `vendor_details`.

The Twilio integration itself lives in `backend/app/utils/telephony/`
(generic client: send, fetch_message for callback backfill, signature
validation); `services/sms/twilio_gateway.py` binds it to the yaml.

PHI note: the example agent's prompt forbids diagnoses, results and
medication names in message bodies (SMS is not a secure channel) — keep
that rule in every campaign manifest.

## Databricks data plane (Genie + direct SQL)

Natural-language questions over Unity Catalog data, per team, with zero
hardcoding: the platform yaml holds workspace credentials
(`databricks.workspaces.<key>.{host,token}`, PAT in every env); WHICH
space/warehouse a query touches is a **team connection**.

1. Fill `databricks.workspaces.primary` in the env yaml. **Use a
   serverless SQL warehouse for the Genie space** — 2-6s start vs ~4 min
   classic cold start; that cold start is what makes answers take minutes.
2. Register the team's connections (`POST /api/v1/admin/connections`):

   ```json
   {"team_key": "data-analytics", "connection_key": "gda-genie",
    "source_type": "genie",
    "config": {"workspace": "primary", "space_id": "<genie space id>"}}
   ```
   ```json
   {"team_key": "data-analytics", "connection_key": "gda-sql",
    "source_type": "databricks_sql",
    "config": {"workspace": "primary", "warehouse_id": "<id>",
               "catalog": "main", "schema": "claims"}}
   ```
   A team can register as many as it needs (multiple warehouses/catalogs
   = multiple connections; the agent manifest picks its default).
3. Start + activate `gda_agent` (port 8111, team `data-analytics` — register
   the team + token first; the manifest's `settings.data.genie_connection`
   names the connection it queries).
4. Ask from the UI: "How many claims were denied last month?" → routed to
   gda_agent → `/capability/data/genie` (conversation continuity is
   platform-managed per chat session, so follow-ups like "break that down
   by payer" stay in the same Genie thread) → streamed grounded answer
   with the generated SQL and a result table.
5. Draft appeals: same agent, same LLM endpoint — "Generate a draft appeal
   for claim CLM-10023", or the dashboard pins `agent_key: "gda_agent"`
   and sends the selected row's identifiers as the question. The manifest's
   `draft_appeal` prompt owns the letter structure; missing fields come
   back as [MISSING], never fabricated.

Capability surface: `POST /capability/data/genie` {connection_key,
question} and `POST /capability/data/sql` {connection_key, statement} —
team-token auth; a connection must belong to the calling team. Operational
knobs in yaml `databricks.data`: poll backoff (1s→5s), `max_wait_seconds`
(90), `statement_wait_timeout` (50s sync), `max_result_rows` (100).

**The fast lane — `POST /capability/data/ask` (text-to-SQL, ~5-8s):**
both data agents now default to `mode: "ask"` in their manifests: aubrey
introspects the connection's schema via `information_schema` (cached,
TTL 600s — column COMMENTs in Databricks directly improve accuracy),
your own LLM writes ONE SELECT statement guided by the manifest's
few-shot `examples`, and the statement API executes it. Unanswerable
questions come back as `{answerable: false, reason}` → the manifest's
`not_answerable` prompt answers conversationally — never a hard failure.
Guards: single SELECT/WITH only (DML/DDL rejected), one repair attempt on
SQL errors, row caps; keep the workspace PAT read-only. This lane works
against `hive_metastore` TODAY (no UC migration needed) and costs your
already-metered LLM tokens instead of the shared Genie DBU allowance.
Flip any agent back with `mode: "genie"`. Prompt template + knobs:
yaml `databricks.data.text2sql`.

**Second data agent — the pattern repeated:** `contract_negotiation_agent`
(port 8112, team `supply-chain`) answers natural-language questions over
the supply chain team's contract data in Unity Catalog. Onboarding is
identical and fully independent of gda: register team `supply-chain` +
token → register its Genie connection (`connection_key: "contracts-genie"`,
`source_type: "genie"`, config `{workspace, space_id}` for a space built
on the contracts tables) → start the agent with the supply-chain token →
activate. Every new data domain is this same recipe: team + space +
connection + manifest — zero platform code.

## Vendor gateway (MCP) — piece 1

Third-party vendor tools plug in as connections; agents consume them
through the capability plane with platform-held credentials and full
audit. Register: `POST /api/v1/admin/connections` with
`source_type: "mcp"`, config `{server_url, auth_header_name?,
auth_header_value?}`. Agents then call `POST /capability/mcp/tools`
(discover: name/description/schema) and `POST /capability/mcp/call`
({tool, arguments} → {is_error, text, structured}) via the kit's
`mcp_tools()` / `mcp_call()`. Vendor URLs and secrets never reach agents;
every call logs team, agent, connection, and tool. Piece 2 — **MCP bridge agent** (`agents/mcp_bridge_agent`, port 8120):
copy the template, set `settings.mcp.connection` to the team's mcp
connection, replace the manifest skills with the vendor's real
capability, register + activate. The vendor becomes a routable agent:
the LLM picks the right vendor tool, the platform executes it with
stored credentials, the answer streams back grounded on the result.
Swap vendor for an internal build later without touching anything else.

Piece 3 — **Aubrey as an MCP server** (`POST /api/v1/mcp`): any MCP
client authenticates with a TEAM SERVICE TOKEN (Bearer) and gets three
governed tools: `ask_agent` {agent_key, question}, `retrieve_knowledge`
{agent_key, query, top_k?}, `query_data` {connection_key, agent_key,
question}. Stateless 2026-07-28 dialect is primary; legacy `initialize`
is answered for older clients. A token reaches only its own team's
agents and connections; every call is logged.

Remaining: external A2A vendor agents with outbound auth (lands with
M5's authenticated hops).

## Troubleshooting

- **403 on admin/chat endpoints** — CSRF missing/stale: re-run
  `/api/v1/auth/me`, re-Authorize. RBAC also applies: your login roles must
  pass Casbin for `/api/v1/admin` (see `authorization.full_access_roles`).
- **Agent prints registration failures** — aubrey not up yet (it retries),
  wrong/revoked token, or `team_key` mismatch between token and manifest.
- **Router never picks an agent** — agent not `active`, or your question
  doesn't overlap its skill utterances (sparse router locally = lexical
  matching; use words from the skill examples).
- **Benefit/policy agent answers "could not find anything"** — ingestion
  batch failed or grants went to a different `agent_key`; re-check the
  ingest response counters and the `agent_key` you sent.
- **File agent says no file was uploaded** — the upload went up without
  `session_id`, or with a different session's id than the chat is using.
  Re-upload with the current session id (check `stored` > 0 in the upload
  response).
- **LLM stream errors** — `azure_foundry` chat deployment creds missing:
  the startup validator names the exact yaml key to fill.
- **Token lost** — team tokens are shown once; issue a new one and restart
  the agent with the new `AGENT_TEAM_TOKEN`.
