# STATE — Aubrey living tracker

Updated: 2026-08-19. Companion to NEW_PLAN.md (design) — this file says
what is DONE, IN PROGRESS, and NEXT. Update every working session.

## Delivered (verified, on GitHub unless noted)

| Area | Status |
|---|---|
| M1 Retrieval capability plane (hybrid RRF + graph, llm stream) | done |
| M2 Sessions + token-budgeted memory window (session-scoped, cross-agent) | done |
| M3 Cognitive Engine v1 (routing, disambiguation, stickiness, refusal facts) | done |
| M4 Agent Kit + agents (general, benefit, policy, file) | done |
| M6 SMS channel (campaigns, consent ledger, keywords, delivery ledger, gates) | done |
| M7a Databricks data plane (multi-workspace, genie + sql, session-scoped genie conversations) | done |
| Text2sql fast lane (/data/ask: introspection + few-shot CTE + guards; genie fallback mode) | done (restored e79d8f6) |
| Data agents: gda (draft appeals) + contract_negotiation, mode ask|genie | done |
| Admin: Entra admin-group→team mapping, /my-team, masked token list, revoke, team-admin guard | done |
| Admin dashboard UI (single-file bento, My Team / Global Directory, one-time token panel) | done (frontend_new/) |
| Vendor gateway: mcp connections + capability proxy + kit mcp_tools/mcp_call | done |
| MCP dual-dialect client (2026-07-28 primary, legacy fallback, auto) | done |
| MCP bridge agent template (8120) | done |
| Aubrey as MCP server (POST /api/v1/mcp: ask_agent, retrieve_knowledge, query_data) | done |
| Voice + Teams channels | done on user's machine (not in this repo) |
| Proposal (12-page PDF/doc, proposal/) | done, not committed |

## Phase 0 security quick wins (from NEW_PLAN §9.2) — 2026-08-19

- [x] Twilio webhook FAILS CLOSED when unconfigured (local opts out via validate_signatures:false; dev/uat/prd auth_token → Key Vault lookup:)
- [x] Connection config masked on read (secret|token|password|key|credential → ***)
- [x] OAuth token-endpoint body log redacted; auth exc logs → error_type only
- [x] rich_tracebacks locals disabled
- [x] sms_consent.keyword stores classified enum, not raw body
- [x] TeamTokenService.list_for_team self._db → self._connector (crash fix)
- [x] browser_sessions PK = hashed cookie value (raw only in cookie/context)
- [x] Token mask uses id suffix, no hash material (validate: global hash uniqueness enforced by constraint — tenant scoping unnecessary pre-auth)
- [x] Pushed 2026-08-19 (all phases in one push)

## Known divergences / bugs

- chat_route edit/feedback methods + _owned_message kwarg: repo copy diverges
  from user's working machine (their copy works) — do not "fix" blind.
- M10b caveats (honest): placeholder field_encryption.key = PLAINTEXT
  passthrough (startup warning; "enc:v1:" prefix keeps old rows readable
  when a key lands). Decay recomputes weight from age — idempotent and
  replica-safe, but assumes birth weight 1.0 (M10d boosting needs a stored
  base weight). Working-memory older-turn embeddings compute per request
  under the 250ms layer deadline — first turn of a long session may fall
  back to recency-only until the in-process cache warms. Redaction is
  regex-rules only (yaml-extensible); NER pass is future work per §8.1.
- Router fallback-competition bug: diagnosed, fix = exclude
  router.fallback_agent from scoring (lands in M10a).
- Envelope is self-asserted on capability plane (M10-S3 signs it).
- Archive never hard-deletes; uploaded docs permanent (M10-S2).

## Next (build order from NEW_PLAN §9.6)

1. ~~Phase 0~~ COMPLETE 2026-08-19
2. ~~M10a~~ COMPLETE 2026-08-19: fallback excluded from scoring, BLOCKING
   collision gate at activation (yaml router.collision_blocking, pgvector
   overlap SQL vs active agents), QueryContextualizer wired pre-routing
   (yaml router.contextualizer, fail-open, original text kept in history)
3. ~~M10b~~ COMPLETE 2026-08-19: memory core — scope/record/layer contracts +
   orchestrator (gather, per-layer deadlines, fail-soft), hybrid recency+cosine
   working window, memory_facts/episodes + session_summaries (redact→embed→
   encrypt, FieldEncryptor AES-GCM, decay job in lifespan), envelope `memory`
   block (additive, kit mirrored), background summary+fact commit, SMS parity.
4. **M10-S1** — FieldEncryptor rollout to EXISTING columns + sparse-index
   redesign (token table), phone HMAC scheme (NEW_PLAN §9.3); per-tenant
   Key Vault keys + rotation (M10b ships one platform key from yaml/KV)
5. **M10-S2** — Retention/erasure service; **M10c** — episodic/prospective
   population + external-subject rules §8.3 (episodic recall exists but
   nothing writes episodes until session-close detection; SMS assembles
   memory but does NOT commit it — deliberate least-PHI until §8.3 lands)
6. **M10d** — Router learning (judge, calibration, utterance mining+decay)
7. **M5** — Delegation (parallel track; unlocks vendor A2A piece 4, SMS→web consults)
8. M9 dashboards (Ask Aubrey button), semantic query cache (M7b), Langfuse

## Standing decisions

Two teams model superseded by many (platform, hr-team, data-analytics,
supply-chain, clinical-outreach...). Kit = folder until Azure Artifacts.
PAT-only Databricks until Entra M2M. Genie kept as config-selectable
fallback. Opt-out keywords are the ONLY approved hardcode. Session id =
A2A contextId = memory key, all channels.


## Deploy notes for all phases (2026-08-19)

- Dev DB RESET required (new tables: memory_facts/episodes/prospects,
  session_summaries, deletion_evidence, router_feedback; new columns:
  phone_hash x3). pgvector >= 0.7 (halfvec).
- Generate keys in yaml: security.field_encryption.key + 
  security.envelope_signing.key (base64 32B; validator prints the command).
  Placeholders = passthrough (plaintext, unsigned) for local dev.
- Version coupling: once envelope signing key is set, kit and platform
  must deploy together (old kit drops sig -> 403; new kit vs old platform -> 422).
- Known accepted caveats: consent dedup race under concurrency, episodic
  double-fire edge, retention yaml has no lookup:/env overrides, judge+
  feedback SQL not yet exercised against live DB/LLM, sms memory commit
  is policy-gated no-op for external until campaigns opt in.
