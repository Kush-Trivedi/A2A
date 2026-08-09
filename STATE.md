# STATE — ACE Platform Build Tracker

Living document. Update on every working session: flip phase statuses, append to
the changelog, record decisions. The full design is in `PLATFORM_PLAN.md`.

Last updated: 2026-08-09

---

## Phase tracker

| # | Phase | Status | Acceptance check |
|---|---|---|---|
| 0 | Plan + state docs | ✅ done | `PLATFORM_PLAN.md` + `STATE.md` exist |
| 1 | Config parity & integration health | ✅ done | `/api/v1/admin/health/integrations` live (admin-guarded); startup validator names exact yaml paths of placeholders; probes: postgres ok, databricks/keyvault/llm/a2a_agents report not_configured with the yaml key to fill |
| 2 | A2A client layer in ACE | ✅ done | E2E verified: `send()` + `stream()` (alias resolution, session continuity) routed to the template agent on :3100, answers persisted in chat history; health probe shows `agent:scheduling: ok` |
| 3 | Card validation + capability resolution + out-of-scope UX | ✅ done | E2E verified: unreachable/invalid card fails registration (`agent_card_unreachable`); skills+snapshot stored from validated card; capability resolves by skill id/tag/alias; denied user gets refusal naming Clinical Care + contact_email (send `refusal` field + stream `refusal` event); permitted user unaffected |
| 4 | Service-plane Entra auth (OIDC scheme, bearer both ways) | ✅ done | E2E verified with local JWKS + minted RS256 JWT: unauthenticated call → 401 rejected; garbage token → 401; valid token (issuer+audience validated) → answer streamed. Live Entra round-trip pending real creds in yaml + Entra app registrations |
| 5 | ContextEnvelope + cross-team delegation | ✅ done | E2E verified: one chat turn → scheduling agent → delegated to pay_ops insurance agent (:3200) with envelope forwarded (actor, tenant, delegated_from, reason all echoed back) + referenceTaskIds; combined answer in one thread; `a2a_dispatch` audit row with correlation id and `completed` outcome |
| 6 | Frontend: chat + canvas (`ACE/frontend`) | ✅ done | Build passes (strict tsc + vite). API-contract E2E with real fingerprint-bound sessions: anonymous → 401 sign-in gate; developer catalog = 5 agents, nurse = 3 (registered agents RBAC-filtered); streamed turn shows cross-team verification; nurse gets `refusal` event (RefusalCard). Browser login flow itself awaits real Entra creds |
| 7 | Genie provider (Databricks) | ✅ done (live round-trip pending real PAT) | `GenieService.ask(space_id, question)` → GenieAnswer(text, sql, columns/rows, canvas-ready `to_artifact_data()`); guards verified: placeholder space id → ValidationError naming team_config key; empty question rejected; workspace failures → typed `genie_query_failed`. Live answer awaits real databricks.host/token + a team's genie_space_id |
| 8 | OOP/DRY cleanup + hardening | ✅ done (backlog logged) | reload bug fixed (200, loaded:5); ChunkerFactory classed; push/card-signing/Alembic/capability-endpoint in explicit backlog |
| 9 | General agent + capability API | ✅ done | E2E: nurse asks "what can I access?" → live role-scoped list (her 4 agents, NO scheduling/insurance); developer sees all; safe general answers. **Security fix: casbin matcher keyMatch2→keyMatch — `agent:X` policies were matching ALL agents (colon = named param). Verified corrected.** Capability endpoints `/capability/knowledge/retrieve` + `/capability/agents/catalog` (bearer via `security.capability_auth_enabled`); `AceCapabilityClient` in kit; general agent on :3300 |
| 10 | File upload agent (session-strict Q&A) | ✅ done | E2E: no session docs → "please upload a file"; with planted session doc → answer grounded strictly in `discharge_summary.docx` quoting the chunk. Upload button in chat composer (📎, session-gated) → `/knowledge/ingest/file` (markitdown[all]). Agent on :3400, sparse retrieval by default (works pre-LLM-creds; switch to hybrid in agent.yaml after). Retrieval-quality fix: FTS OR-semantics |
| 11 | SharePoint agent (two-phase) | ✅ done | E2E: unconfigured SharePoint rejected naming yaml key; developer → grounded answer from `sharepoint:policies` doc; nurse reaches agent (no refusal) but source-level Casbin yields "no documents you can access". Endpoint `/knowledge/ingest/sharepoint` live; agent on :3500. Live Graph pull awaits real microsoft.sharepoint creds |
| 12 | Blob storage agent (two-phase) | ✅ done | E2E: unconfigured storage rejected naming yaml key; developer → grounded answer from `blob:claims` doc (`appeal_guidelines.pdf`); nurse reaches agent but source-level Casbin filters to zero. `/knowledge/ingest/blob` live; agent on :3600. Live blob pull awaits real storage_account creds |

| 13 | Agent + prompt versioning (team-owned) | ✅ done | E2E: registered 0.2.0 → 0.3.0 (history: current/superseded), prompt versions recorded per agent version, `activate_version` rollback to 0.2.0 restored its prompt set, chat turn prompt-driven. Endpoints: GET `/admin/agents/{key}/versions`, POST `.../versions/{v}/activate`. Teams author prompts in agent.yaml `prompts:` (name → version+content), used via kit `PromptStore` — ACE only records |

| 14 | Per-agent LLM deployment choice | ✅ done (live call pending Foundry creds) — E2E: unregistered deployment rejected with registered list; registered deployment passes governance and stops at the exact yaml key; unknown agent rejected; `/capability/llm/chat` live. Cloud topology decision recorded: shared Container Apps Environment, one app per agent (own scale rules), internal DNS + registry card_url discovery, AKS later = registry update. Original note: | ACE yaml keeps shared Foundry base_endpoint/api_key; teams declare deployments in agent.yaml `llm:` → registry team_config; capability endpoint `/capability/llm/chat` validates the deployment belongs to the team, calls Foundry, meters per team. Deploy: one shared Container Apps Environment, one app per agent (own scale rules = noisy-neighbor fix), internal DNS names replace ports, registry card_url = discovery; AKS later is a registry-URL change only |

| 15 | Container deployment standardization | ✅ done (builds pending Docker Desktop) | ONE shared `Agents/Dockerfile` (ARG AGENT — same recipe for all six agents), `Dockerfile.ace` (root context for kit path-dep), frontend nginx image (proxies /api → ace), `docker-compose.yml` local cloud-parity (pgvector + ACE + 6 agents + UI, service-DNS card URLs), `infra/main.bicep` + `infra/agent.bicep` (shared CAE, one app per agent with own cpu/mem/replica knobs + team tags, internal ingress DNS = card_url, scale-to-zero) |

| 16 | Concurrency & load handling | ✅ done | DB pool yaml-sized (10+20, both auth modes); bulk sharepoint/blob ingestion → 202 + background job (`ingestion_jobs` table, GET `/knowledge/ingest/jobs/{id}`; Service-Bus-ready shape) — verified completed + failed lifecycles; per-actor token-bucket rate limit on chat/stream + 4 ingestion routes (yaml `security.rate_limit`, burst enforced, per-actor isolation verified); A2A card cache TTL (300s, two-turn chat verified); casbin auto-reload every `authorization.policy_reload_seconds` (300s — multi-replica staleness stopgap); dead `config.max_workers` removed. Edge (APIM/Front Door) owns global limits |

| 17 | Twilio SMS channel + agents | ✅ done (live Twilio pending creds) | E2E (fake Twilio via DI): inbound SMS → routed through the REAL general agent over A2A (role-scoped `sms_patient` reply) → REST reply; conversation continuity across texts; bodies+numbers `enc::` AES-256-GCM at rest; STOP honored (no replies, outreach blocked), START re-enables; outreach send works; webhook `/channels/sms/inbound` with signature validation (skipped only when unconfigured); capability `/capability/sms/send` + kit `sms_send`; `sms_outreach` agent on :3700 |

| 18 | Microsoft Teams channel | ✅ done (live pending real webhook secret) | E2E: HMAC-SHA256 over raw body — valid accepted, tampered/forged rejected; @mention stripped; activity routed through the REAL general agent (role-scoped `teams_user` reply) with inline activity response (Teams 5s contract); conversation continuity per (thread,user); bodies + user ids `enc::` at rest; `teams_conversations`/`teams_messages` entities; endpoint `/api/v1/channels/teams/messages`; app manifest template at `teams-app-manifest.json` |

| 18b | Per-agent Teams webhooks (opt-in) + Benefits agent | ✅ done | Teams exposure is a per-agent CHOICE: manifest `data.channels.teams.{enabled, webhook_secret}` → registry team_config; no opt-in ⇒ 404 on `/channels/teams/{agent_key}/messages` (no platform-secret fallback). Live E2E: benefits+team secret→200 answer, wrong secret→401, non-opted-in wellness→404, unknown→404; AAD object id → `user_role_assignments` roles; `benefits_agent` (hr_benefits, :3800) |

Statuses: ⬜ not started · 🔨 in progress · ✅ done · ⏸ blocked

## Foundation (completed before phase tracking began)

- ✅ Config 100% yaml-centralized; `ApplicationContext` sole env reader; `ENV` only bootstrap var; yamls have `security` / `authorization` / `knowledge` / `agents` sections (all 4 envs, BOM-free, read `utf-8-sig`)
- ✅ Legacy auth removed (single Entra callback; compat router + legacy mapping migration deleted)
- ✅ Chunking strategies selectable: simple / recursive / hierarchical / semantic (`services/knowledge/chunker.py`, per-request `chunking_strategy`)
- ✅ Retrieval modes selectable: dense / sparse / hybrid (per-agent `retrieval_mode`, provider dispatch)
- ✅ Databricks `WorkspaceClient` factory — PAT all envs (`database/databricks/`)
- ✅ Agent registry: `odt_teams` + `registered_agents`, `/api/v1/admin/agents*`, Casbin auto-seed on registration, team-owned `team_config`
- ✅ Template A2A agent `Agents/scheduling_agent` (a2a-sdk 1.1.2, card served, self-registration script)

## Decision log

| Date | Decision |
|---|---|
| 2026-08-09 | Databricks auth = PAT for ALL environments (no M2M). |
| 2026-08-09 | Config ownership: team data config (warehouse/catalog/schema/genie ids, agent app id) in team `agent.yaml` → registry `team_config`; Entra/common stays ACE-side. |
| 2026-08-09 | UI = single chat screen only + canvas panel. No dashboards for now. |
| 2026-08-09 | Out-of-scope/no-access questions → polite refusal naming owning team + `contact_email` from registry. Never raw 403, never a made-up answer. |
| 2026-08-09 | Config parity: one code path all envs; no dummy-cred special-casing; real creds go into yaml later and must just work. |
| 2026-08-09 | OOP mandate: no loose Python functions — classes + `get_*()` singleton accessors + route handlers only. DRY via shared Agent Kit. |
| pending | Entra app registration: per team (recommended) vs per agent. |
| 2026-08-09 | Agent→agent topology: **direct** calls implemented (phase 5) with team-owned `delegations:` targets in agent.yaml + mandatory envelope forwarding + ACE dispatch audit. ACE-mediated capability resolution endpoint can be added later without changing the agent contract. |
| pending | Team agent deployment target (ACA / AKS). |

## Environment / run reference

- ACE backend: from `ACE/` → `uv run python -m backend.app.app` (port 3000, `/docs` when local)
- Template agent: from `Agents/scheduling_agent/` → `uv run python -m app.main` (port 3100; card at `/.well-known/agent-card.json`)
- Local dev DB overrides (until real creds land in yaml): `ACE_DB_POSTGRES_USER=postgres`, `ACE_DB_POSTGRES_PASSWORD=12345678`, `ACE_DB_POSTGRES_DBNAME=postgres`
- Config env selector: `ENV` (local | dev | uat | prd) → `backend/app/config/env/<ENV>.yaml`

## Credential-swap verification checklist (run after real creds land in yaml)

1. Boot ACE — `SettingsValidator` startup log shows no `your_*` placeholder warnings.
2. `GET /api/healthcheck` → 200.
3. `GET /api/v1/admin/health/integrations` → postgres / databricks / keyvault / llm all green.
4. Entra login round-trip → `GET /api/v1/auth/me` shows roles + groups.
5. Ingest a doc (each chunking strategy) → chat question retrieves it (each retrieval mode).
6. Template agent registration → row in `registered_agents`, policies seeded.
7. Chat turn routed to A2A agent end-to-end (phase 2+).

## Known issues

- (fixed 2026-08-09) `policies/reload` 500 — `_audit` wrote None into NOT NULL
  audit columns when no policy tuple; now writes "-". Verified: reload → 200.

## Hardening backlog (deliberate deferrals)

- Push notifications (long-running tasks) — webhook receiver validating
  AuthenticationInfo with the Entra validator.
- AgentCard signing (`AgentCardSignature`) — prod change control.
- Alembic migrations replacing the `ALTER ... IF NOT EXISTS` upgrades in init_db.
- ACE-mediated capability-resolution endpoint for agents (currently direct
  team-config delegation targets).
- Chat `sources` currently only ride the stream `meta` event (RAG path);
  registered A2A agents return sources via artifacts later.

## Changelog

- 2026-08-09 — **Phase 18b done.** Per-agent Teams webhooks, OPT-IN: not
  every agent goes to Teams — the owning team chooses by declaring
  `data.channels.teams.{enabled: true, webhook_secret}` in their agent.yaml
  (flows verbatim to registry team_config at registration). Their Outgoing
  Webhook points at `/api/v1/channels/teams/<agent_key>/messages` with the
  secret Teams generated for THEM; agents without the block get 404 on that
  route — there is NO fallback to the platform secret (that would have made
  every registered agent reachable over Teams). The platform default route
  `/channels/teams/messages` stays yaml-configured. Identity: `from.
  aadObjectId` is the real actor; roles from `user_role_assignments`
  (Entra-provisioned) ∪ channel default_roles. New `Agents/benefits_agent`
  (hr_benefits/benefits, :3800, versioned system/fallback prompts).
  Live-verified against running ACE: benefits+team secret→200 answer,
  wrong secret→401, non-opted-in wellness agent→404, unknown agent→404.
- 2026-08-09 — **Phase 18 done.** Teams outgoing-webhook channel
  (`services/teams/teams_channel_service.py` + route
  `POST /api/v1/channels/teams/messages`): validates `Authorization: HMAC
  <sig>` (SHA-256 over RAW body, base64 secret from yaml
  `microsoft.microsoft_teams.outgoing_webhook_secret`, constant-time compare;
  skipped with loud warning only when unconfigured); strips `<at>` mention;
  maps (Teams thread, user) via peppered hash → `teams_conversations` bound to
  an ACE chat session (default agent from yaml `microsoft_teams.agent`,
  fallback `general`; roles `microsoft_teams.default_roles`); replies INLINE
  in the HTTP response per the Teams outgoing-webhook contract; activity-id
  idempotency; bodies + user ids AES-GCM at rest (`teams_messages`).
  Yaml gains `microsoft_teams.tenant_id`/`default_roles`. App manifest
  template at repo root. To go live: create the Outgoing Webhook in the Teams
  channel (callback URL = https://<ace>/api/v1/channels/teams/messages) and
  paste the generated security token into the yaml secret.
- 2026-08-09 — Phase 17 follow-up: status callback now signature-validated
  AND persists `MessageStatus` onto `sms_messages.delivery_status`
  (`update_delivery_status`) — full delivery lifecycle recorded in DB.
- 2026-08-09 — **Phase 17 done.** SMS channel, agnostic by design (same
  patient can text about any team's topic — only the conversation's
  agent_key changes; inbound flows through the normal chat pipeline to the
  A2A agents). ACE side: `services/sms/` — `TwilioSettings` (existing
  `twilio:` yaml + new `messaging:` policy keys default_agent/inbound_roles/
  opt keywords, `tenant_id`, `voice:` placeholder for telephony later),
  `TwilioSmsClient` (official SDK in worker thread; messaging_service_sid
  preferred, REST replies not TwiML; signature validation — AAAS pattern),
  `SmsChannelService` (HMAC phone hash w/ yaml `security.identity_hash_pepper`,
  find-or-create conversation bound to chat session, opt-out/opt-in ledger on
  conversation, synthetic SessionContext with yaml-configured sms roles).
  `FieldEncryptor` (`security/field_encryptor.py`, AES-256-GCM `enc::`
  envelope, key from yaml `security.field_encryption_key` — loud warning when
  missing, unlike the silent reference impl). New entities `sms_conversations`
  + `sms_messages` (unique twilio sid = webhook idempotency). Routes:
  `POST /api/v1/channels/sms/{inbound,status}` (always 200/TwiML to Twilio),
  capability `POST /capability/sms/send` + kit `sms_send` (creds never leave
  ACE, opt-outs enforced centrally). New `Agents/sms_outreach_agent`
  (clinical_care, :3700, one-way notifications). twilio==9.10.9 added.
  To go live: fill twilio.* creds + webhook_base_url (point Twilio number's
  webhook at /api/v1/channels/sms/inbound) + encryption key/pepper.
- 2026-08-09 — **Phase 16 done.** Load handling, kept simple: (1)
  `database.postgres.pool_size/max_overflow` in yaml → engine (was defaults
  5+15/replica — first bottleneck). (2) `IngestionJobService` +
  `ingestion_jobs` table (`services/knowledge/ingestion_job_service.py`) —
  sharepoint/blob POSTs now return 202 + job_id, work runs as asyncio task,
  status at GET `/knowledge/ingest/jobs/{job_id}`; same shape a Service Bus
  worker consumes later. (3) `RateLimiter` (`security/rate_limiter.py`,
  per-actor token bucket, yaml `security.rate_limit`) on chat, chat/stream,
  and all 4 ingestion POSTs — defense-in-depth behind APIM; protects Foundry
  quota from a runaway session. (4) A2A AgentCard TTL cache (300s) — no
  discovery round-trip per turn. (5) Casbin periodic auto-reload
  (`authorization.policy_reload_seconds: 300`) — replicas converge without a
  broker; Service Bus pub/sub can replace both (2) and (5) later. Dead
  `config.max_workers` removed (`config: {}`). Chat E2E re-verified through
  the cached-card path.
- 2026-08-09 — **Phase 15 done (files; builds await Docker).** Deployment
  standardization: `Agents/Dockerfile` (uv slim base, ARG AGENT — one recipe,
  six agents), `Dockerfile.ace` (root build context because ACE path-depends
  on Agents/agent_kit; ENV picks the yaml, no creds in images),
  `ACE/frontend/Dockerfile` (node build → nginx, /api proxied to `ace`),
  `docker-compose.yml` (pgvector+ACE+agents+UI; in compose mode register
  card_urls with service DNS names e.g. http://scheduling:3100/...),
  `infra/agent.bicep` (per-agent Container App: internal ingress FQDN becomes
  the card_url, own scale rules, team cost tags) + `infra/main.bicep`
  (log analytics + shared CAE + ACE external + 6 agent module calls).
  Verify when Docker Desktop is running: `docker compose up --build`.
- 2026-08-09 — **Phase 14 done.** `LlmGatewayService`
  (`services/a2a/llm_gateway_service.py`): agents call
  `POST /api/v1/capability/llm/chat` (envelope + agent_key + deployment +
  messages); ACE validates the deployment against the agent's registered
  `team_config.llm_deployments` (teams declare in agent.yaml `llm:` section —
  scheduling has chat/summarize examples), calls the ONE shared Foundry
  (base_endpoint/api_key stay in ACE yaml; key never leaves ACE) via
  `astream_chat(model=deployment)`, and meters per team (structured log:
  team/agent/deployment/actor/chars). Kit `AceCapabilityClient.llm_chat()`.
  Config parse + register payload updated (team_config.llm_deployments).
  Per-deployment Foundry TPM quotas = LLM noisy-neighbor isolation.
- 2026-08-09 — **Phase 13 done.** `agent_versions` table
  (`entity/agents/agent_version_entity.py`): immutable snapshot per
  (tenant, agent_key, version) — card, skills, team_config, allowed_roles,
  **prompts** — newest = `current`, others `superseded`.
  `registered_agents.prompts` JSONB added (+ init_db upgrades incl.
  chat_sessions actor_id/updated_at for old-DB reuse). Registry:
  `_record_version` on every registration, `list_agent_versions`,
  `activate_version` (rollback restores full snapshot + re-seeds policies).
  Routes: GET `/admin/agents/{key}/versions`, POST
  `.../versions/{version}/activate`. Kit: `PromptStore`/`PromptDefinition`
  (from agent.yaml `prompts:` name → {version, content}; safe format;
  `to_registration_payload`). Scheduling agent bumped to 0.2.0 with
  versioned `booking_ack` + `delegation_reason` prompts, executor is
  prompt-driven; register.py sends prompts. Ownership: teams author and run
  their prompts from their own manifest — ACE only records versions.
- 2026-08-09 — **Phase 12 done.** `BlobIngestionService`
  (`services/knowledge/blob_ingestion_service.py`) — validates
  `microsoft.azure.storage_account.storage_account_url`, lists/reads via
  `AzureStorageBlobClient` (managed identity) in a worker thread, per-file
  markitdown ingestion under `blob:<source_name>`;
  `POST /api/v1/knowledge/ingest/blob` (team supplies container/prefix).
  `Agents/blob_agent` (pay_ops/blob_qa, :3600, sources `blob:claims`,
  sparse default) — same double-enforcement as SharePoint agent, verified.
  **All four knowledge-wave agents complete: general (:3300), file_qa
  (:3400), sharepoint_qa (:3500), blob_qa (:3600) — plus scheduling (:3100)
  and insurance (:3200). Seven services + frontend.**
- 2026-08-09 — **Phase 11 done.** Phase A: `SharePointIngestionService`
  (`services/knowledge/sharepoint_ingestion_service.py`) — validates
  `microsoft.sharepoint.*` (PlaceholderPolicy names the exact yaml key),
  lists/downloads via existing `SharePointClient` in a worker thread,
  per-file markitdown ingestion under `sharepoint:<source_name>` (one bad
  file never sinks the batch); `POST /api/v1/knowledge/ingest/sharepoint`
  (CSRF + per-source write enforced in the pipeline). Phase B:
  `Agents/sharepoint_agent` (clinical_care/sharepoint_qa, :3500, sources
  `sharepoint:policies`, sparse default) — retrieval limited to declared
  sources AND the caller's Casbin read policies (gateway double-enforcement
  verified: developer grounded, nurse filtered to zero with actionable
  message). Team's site/drive/path documented in agent.yaml `data.sharepoint`.
- 2026-08-09 — **Phase 10 done.** `Agents/file_upload_agent`
  (ace_platform/file_qa, :3400, roles developer+nurse): retrieves via
  `AceCapabilityClient` scoped to `envelope.chat_session_id` ONLY (empty
  knowledge_sources — cannot see shared data); no docs → upload prompt;
  answers quote grounded snippets with source names (LLM plug-point marked).
  Capability retrieve skips embedding when `retrieval_mode=sparse` — the whole
  file-QA path works without LLM creds; flip agent.yaml to hybrid when
  azure_foundry is live. Frontend: 📎 upload in composer (disabled until a
  session exists) → multipart `/knowledge/ingest/file` + CSRF. **Retrieval
  fix:** `plainto_tsquery` ANDs all words (any non-document word killed the
  match); sparse+hybrid lexical CTEs now use OR-semantics
  (`regexp_replace(...'&'→'|')`) with rank ordering. Note: UI upload path
  needs LLM creds (ingestion embeds chunks); planted-doc E2E covers retrieval.
- 2026-08-09 — **Phase 9 done.** Service-plane capability API
  (`api/routers/v1/capability_v1_routes/`): `/capability/knowledge/retrieve`
  (envelope→SessionContext via `EnvelopeContextMapper`, gateway enforces
  per-source RBAC) + `/capability/agents/catalog`; guarded by
  `ServiceAuthGuard` (`security/service_auth.py`, explicit
  `security.capability_auth_enabled` yaml switch, Entra JWKS validation when
  on). Kit gained `AceCapabilityClient` (retrieve + accessible_agents, optional
  bearer). New `Agents/general_agent` (ace_platform/general, :3300,
  allowed_roles developer+nurse): access questions answered from the live
  catalog, safe template answers otherwise. **SECURITY FIX**: casbin matcher
  used keyMatch2 — `:key` in `agent:general` parsed as a named parameter so any
  agent policy matched every agent; switched to keyMatch (literal colon, `*`
  wildcards preserved). markitdown[all] installed (docx/pptx/xlsx/pdf...).
- 2026-08-09 — **Phase 8 done.** Fixed `policies/reload` 500 (audit None →
  "-" in NOT NULL columns; also hardened `CasbinEnforcer.reload` with
  `_maybe_await`). `ChunkerFactory` class replaces the loose `build_chunker`
  function (alias kept for call sites). Remaining hardening items moved to
  the explicit backlog section above. Reload verified 200 (`loaded: 5`);
  chunker tests pass.
- 2026-08-09 — **Phase 7 done (code-complete).** `services/databricks/
  genie_service.py`: `GenieService` wraps `WorkspaceClient.genie.
  start_conversation_and_wait` in a worker thread (PAT/yaml only); extracts
  text + SQL attachments and best-effort query results
  (`get_message_query_result_by_attachment` → columns/rows); `GenieAnswer.
  to_artifact_data()` is canvas-ready. Space id is ALWAYS team-owned
  (registry `team_config.databricks.genie_space_id`) — validated with
  PlaceholderPolicy, errors name the exact key. Registered in
  ServiceContainer (`provide_genie_service`). Cred-swap test: call
  `get_genie_service().ask(space_id=<real>, question=...)` — zero code
  changes needed. Note: SDK retries unreachable hosts for minutes — health
  probe (`databricks`) is the fast connectivity check.
- 2026-08-09 — **Phase 6 done.** `ACE/frontend` (React 18 + TS strict + Vite 6,
  proxy `/api`→:3000): `AceApiClient` class (CSRF from `ace_session_csrf`
  cookie → `X-CSRF-Token`), `ChatStreamController` class (fetch-based SSE
  parser), `useChat` hook, three-pane layout (SessionList | thread | Canvas),
  `RefusalCard` (team + mailto contact), `Canvas` renders artifacts by part
  kind (data→table, text→markdown, url→link) + sources. Sign-in gate on 401 →
  `/api/v1/auth/login`. Backend: new `AgentCatalogService`
  (`services/agents/agent_catalog_service.py`) — `/api/v1/chat/agents` now
  returns the RBAC-filtered union of built-in + active registered A2A agents.
  Dev: `npm run dev` in ACE/frontend (5173). Sessions are fingerprint-bound
  (IP+UA) — by design.
- 2026-08-09 — **Phase 5 done.** New shared package `Agents/agent_kit`
  (`ace-agent-kit`, editable path dep in ACE + both agents — single source of
  truth, DRY): `ContextEnvelope` (tenant/actor/user/roles/correlation/
  chat_session/purpose/delegated_from/delegation_reason; namespace
  `ace.context/v1`; to/from Message.metadata; `with_delegation`) and
  `AgentDelegator` (cross-team A2A calls with mandatory envelope forwarding,
  referenceTaskIds, optional bearer provider for authed partners). ACE:
  `A2AMessageFactory` now carries the envelope; `ConversationService` builds it
  per turn with a fresh correlation id; `A2ADispatchAuditor` writes one
  `policy_audit_log` row per dispatch (action=a2a_dispatch, correlation id in
  target_domain, outcome in target_action — message-only responses audit as
  `completed`). Scheduling agent: `delegations:` section in agent.yaml
  (team-owned partner card URLs + audience), executor parses inbound envelope
  and delegates insurance verification. New `Agents/insurance_agent` (Pay Ops,
  :3200, skills verify_insurance/appeal_claim) echoes the received envelope —
  proving identity crosses team boundaries. Registered in registry as
  pay_ops/insurance (active).
- 2026-08-09 — **Phase 4 done.** ACE side (`services/a2a/service_token_provider.py`):
  `ServiceTokenProvider` ABC + `EntraServiceTokenProvider` (client-credentials
  via ACE's own Entra app from yaml `microsoft.entra`, per-audience token cache
  with expiry skew — AzurePostgresToken pattern) + `AceCredentialService`
  feeding the SDK's `AuthInterceptor` (audience travels in ClientCallContext
  state; interceptor only fires when the target card declares security — card-
  driven, not env-driven). `A2AClientService.stream_message` gained
  `auth_audience`; ConversationService reads it from registry
  `team_config.auth_audience`. Agent template side (`app/auth.py`):
  `AgentAuthSettings` (explicit `auth.enabled` yaml switch + tenant/audience +
  issuer/jwks_url overrides for sovereign clouds/test), `JwksCache`,
  `EntraTokenValidator` (RS256, issuer+audience, require exp/iss/aud),
  `EntraAuthMiddleware` (401 with WWW-Authenticate; /.well-known/* stays
  public). Card declares `openIdConnectSecurityScheme` when auth enabled.
  OOP conversions done early: `AgentCardBuilder`, `AgentApplicationBuilder`,
  `AceRegistrationClient` (register.py now sends `auth_audience` in
  team_config). Template gained pyjwt[crypto]. **To go live**: fill
  microsoft.entra in ACE yaml + agent auth section with real tenant/app ids —
  zero code changes.
- 2026-08-09 — **Phase 3 done.** `AgentCardService` (`services/a2a/`): fetches via
  SDK `A2ACardResolver`, distinguishes unreachable (ExternalServiceError,
  `agent_card_unreachable`) from invalid content (ValidationError), validates
  name/version/skills/interfaces, snapshots card. `registered_agents` gained
  `skills` + `card_snapshot` JSONB columns (idempotent `ALTER ... IF NOT EXISTS`
  upgrades in init_db — note: replace with Alembic at prod hardening).
  `register_agent` validates the card whenever card_url is present — a broken
  agent can never become routable. `CapabilityResolver` (`services/agents/`):
  deterministic skill-id → tag → agent-key/alias matching over stored skills,
  returns owning team + contact. `OutOfScopeResponder`
  (`services/conversation/`): builds access-denied / no-capability refusals
  naming team + contact_email. `ConversationService`: PermissionDeniedError on a
  turn now produces a persisted refusal answer (metadata `refusal`), returned in
  `ChatTurnResult.refusal`/`ChatTurnResponse.refusal` and streamed as a
  `refusal` SSE event — never a raw 403. Refused turns still record the user's
  question in the session.
- 2026-08-09 — **Phase 2 done.** New `backend/app/services/a2a/` package (all
  classes): `A2AClientService` (streams `A2AStreamEvent`s — text/artifact/state —
  from `Client.send_message`; splits card URL into base + relative path),
  `A2AMessageFactory` (sole builder of outbound Messages; `context_id` = chat
  session id; metadata under `ace.context/v1` with tenant/actor/user — grows
  into the full envelope in phase 5), `PartMapper` (oneof `content`:
  text/raw/url/data), `ArtifactMapper` → canvas-ready dicts,
  `TaskLifecycleTracker` (9 proto states, terminal/user-action detection,
  transition logging), `A2AErrorTranslator` (SDK/httpx → ACE typed errors).
  `A2ASettings` from yaml `agents.a2a_request_timeout_seconds` /
  `a2a_streaming_enabled` (added to all 4 yamls). Registry gained
  `find_active_agent` (key or alias, ACTIVE only). `ConversationService`:
  `_resolve_agent` prefers registered A2A agents over built-ins; A2A turns skip
  local RAG/prompting (remote agent owns retrieval); `send`/`stream` branch via
  `prepared.is_a2a`; stream emits `artifact` + `state` events for the canvas.
  ACE now depends on `a2a-sdk` 1.1.2 (client side). E2E test: register team+agent
  → activate → chat send + stream (alias, same session) → 4 messages persisted.
- 2026-08-09 — **Phase 1 done.** `SettingsValidator` + `PlaceholderPolicy`
  (`backend/app/config/settings_validator.py`) run in the app lifespan: 0 errors /
  38 placeholder warnings locally, each naming the dotted yaml path.
  `IntegrationHealthService` (`backend/app/services/health/`) probes postgres
  (live SELECT 1), databricks (PAT `current_user.me` in thread), keyvault
  (credential token acquisition), llm_embeddings (live 1-token embed), and each
  active registered agent's card URL — never raises, reports
  ok/error/not_configured with sanitized detail. Endpoint
  `GET /api/v1/admin/health/integrations` (RBAC-guarded), DTOs in `dto/common`,
  provider in `ServiceContainer`. Registry gained platform-scope
  `list_active_agent_cards()`. All class-based, zero env branching.
- 2026-08-09 — Foundation work completed (config centralization, legacy removal,
  chunking/retrieval selectability, Databricks factory, registry, template agent).
  Plan rewritten with full 60-type A2A object model, Entra service-plane mapping,
  config parity rules, chat+canvas frontend spec. STATE.md created.
