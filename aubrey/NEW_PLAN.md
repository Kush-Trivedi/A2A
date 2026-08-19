# NEW_PLAN — Aubrey Memory Architecture + Cognitive Engine

Goal: a ChatGPT/Claude-grade conversational experience — the platform
remembers, anticipates, and routes intelligently — built as an
object-oriented, channel-agnostic memory layer with zero hardcoding and
no single-point bottleneck. Alignment document; nothing here is built yet.

Research grounding (2026): production agent memory converges on a
purpose-built memory layer between the LLM and the stack, combining
working memory + tiered external stores; five patterns trade accuracy
(~73% @ 17s p95) against latency (~67% @ 1.4s). Aubrey targets the
hybrid: hot working memory in-request, warm vector recall, cold ledgers —
with budgets so latency never runs away. Key research caution: memories
continuously REWRITTEN by LLMs degrade — Aubrey appends and decays,
never rewrites records in place.

--------------------------------------------------------------------
## 1. Principles

1. **The session is the unit of memory, the user is the unit of recall.**
   Web chat, SMS (phone+campaign thread → chat session), Teams, voice —
   every channel already resolves to a chat session id. Memory keys on
   `(tenant_id, user_id, session_id)`; channels get identical behavior
   for free. No channel-specific memory code, ever.
2. **Agents stay stateless.** All memory lives platform-side and reaches
   agents through the context envelope. An agent never stores anything.
3. **Config over code.** Budgets, decay half-lives, layer toggles,
   extraction prompts — all yaml (`agents.memory.*`) or manifest.
   A layer can be disabled per environment or per agent without code.
4. **Append + decay, never rewrite.** Records are immutable; relevance
   changes through weights and TTLs. Wrong memories fade, they are not
   LLM-edited (research shows in-place rewriting corrupts stores).
5. **Budget-bounded.** Every layer contributes to the envelope under a
   token budget; recall is async and parallel; a slow layer degrades to
   absent, never blocks the turn.

--------------------------------------------------------------------
## 2. The seven memory types (proposal → concrete design)

| # | Type (proposal) | What it stores | Backing store | Recall trigger |
|---|---|---|---|---|
| 1 | In-context working | Current session turns | chat_messages (exists) | every turn |
| 2 | Semantic | Stable facts + entities about the user/domain ("member id 123", "prefers Spanish", "manages vendor X") | `memory_facts` table, pgvector embedded | similarity to (rewritten) question |
| 3 | Episodic | Past interactions across sessions ("asked about knee MRI appeal in July") | `memory_episodes` (summary per closed session/topic), pgvector | similarity + recency-weighted |
| 4 | Procedural | How to do things: prompts, learned routines, tool sequences that worked | agent manifests (exists) + `memory_procedures` (learned, weighted) | agent/task match |
| 5 | External retrieval | Team documents + live data | knowledge/retrieve + data/* capabilities (exists) | agent-initiated |
| 6 | Parametric | What the model itself knows | the LLM weights — no store; represented as a descriptor so orchestration can reason about "the model likely knows this" | implicit |
| 7 | Prospective | Future commitments ("remind me", "follow up after results", campaign follow-ups) | `memory_prospects` with due_at | scheduler tick + session open |

Layers 1 and 5 exist today. 2, 3, 7 are new tables + services. 4 extends
manifests with a learned overlay. 6 is representation-only.

--------------------------------------------------------------------
## 3. Object-oriented design

```
backend/app/services/memory/
    scope.py          MemoryScope (frozen dataclass)
    record.py         MemoryRecord (frozen dataclass)
    layer.py          MemoryLayer (ABC)
    layers/
        working_memory.py        WorkingMemoryLayer
        semantic_memory.py       SemanticMemoryLayer
        episodic_memory.py       EpisodicMemoryLayer
        procedural_memory.py     ProceduralMemoryLayer
        retrieval_memory.py      ExternalRetrievalDescriptor
        parametric_memory.py     ParametricDescriptor
        prospective_memory.py    ProspectiveMemoryLayer
    orchestrator.py   MemoryOrchestrator
    contextualizer.py QueryContextualizer
    summarizer.py     SessionSummarizer
    extractor.py      MemoryExtractor
    decay.py          DecayPolicy
    settings.py       MemorySettings (yaml-backed)
```

**Core contracts (the whole system programs against these):**

```python
@dataclass(frozen=True)
class MemoryScope:
    tenant_id: str
    user_id: str            # "sms:+1..." and Entra users identical here
    session_id: str
    channel: str            # informational only — never branches logic

@dataclass(frozen=True)
class MemoryRecord:
    layer: str              # semantic | episodic | procedural | prospective
    content: str
    weight: float           # decayed relevance, 0..1
    created_at: datetime
    source: str             # extractor | feedback | manifest | scheduler
    metadata: dict

class MemoryLayer(ABC):
    name: str
    async def recall(self, scope, query, budget_tokens) -> list[MemoryRecord]
    async def record(self, scope, records) -> None
    async def decay(self) -> int          # periodic maintenance, returns pruned
```

**MemoryOrchestrator** — the one entry point (no bottleneck: layers run
in `asyncio.gather` with per-layer timeouts; a layer that misses its
deadline contributes nothing this turn):

```python
class MemoryOrchestrator:
    async def assemble(self, scope, question) -> MemoryBundle:
        rewritten = await self.contextualizer.rewrite(scope, question)
        results = await gather_with_timeouts(
            self.layers, scope, rewritten, self.settings.budgets)
        return MemoryBundle(question=rewritten, window=..., summary=...,
                            facts=..., episodes=..., prospects=...)
    async def commit(self, scope, turn_outcome) -> None:
        # post-turn, background: extract facts/episodes/prospects,
        # update summary, feed router signals — never blocks the reply
```

**Envelope extension** (versioned, additive — agents that ignore it keep
working): `aubrey.context/v1` gains a `memory` block:

```json
"memory": {
  "summary": "...rolling session summary...",
  "facts": ["member id 12345", "prefers benefits questions in Spanish"],
  "episodes": ["2026-07: appealed knee MRI denial, resolved"],
  "prospects": ["follow up on appeal outcome"]
}
```

**Channel agnosticism is structural**: `ConversationService` (web),
`SmsChannelService` (SMS), and future Teams/voice all call
`orchestrator.assemble(scope, question)` with their scope. Nothing else
changes per channel.

--------------------------------------------------------------------
## 4. The turn pipeline (Cognitive Engine integrated)

```
question in (any channel)
  → QueryContextualizer: rewrite to standalone using working memory
      ("what about children?" → "what is the PT copay for children?")
  → Routing on the REWRITTEN question:
      T1  fallback agent excluded from scoring (bugfix)
      T1  blocking collision gate at activation (overlap > threshold
          vs active agents → cannot activate without differentiation
          or admin override)
      T2  ambiguous band (top2 within margin, or low confidence) →
          LLM router-judge picks agent or ASK; else pure scoring
      T2  per-agent calibrated thresholds (from logged score outcomes)
  → MemoryOrchestrator.assemble → envelope with memory block
  → dispatch over A2A (unchanged)
  → stream answer (unchanged)
  → MemoryOrchestrator.commit (background):
      extractor mines facts/episodes/prospects from the turn
      summarizer folds the turn into the rolling summary
      router feedback: positive signal → mine utterance (decayed weight);
      agent said not_supported/no_data → penalize question→agent pair
      stickiness decay applied by turn distance
```

Anticipation: `meta` SSE event gains `suggestions[]` (from summary +
prospects + agent skills) so the UI shows "you might ask next" chips.

--------------------------------------------------------------------
## 5. Storage and decay

New tables (Postgres + pgvector, same conventions as everything else):
- `memory_facts`      (tenant, user_id, content, embedding, weight, source, created_at)
- `memory_episodes`   (tenant, user_id, session_id, summary, embedding, weight, created_at)
- `memory_procedures` (tenant, agent_key, content, weight, created_at)
- `memory_prospects`  (tenant, user_id, content, due_at, status, source_session)
- `session_summaries` (tenant, session_id, summary, updated_at)
- `router_feedback`   (tenant, question_embedding, agent_key, signal +|-, weight, created_at)

`DecayPolicy` (yaml half-lives per layer) runs in the app lifespan on an
interval: `weight *= 0.5 ** (age/half_life)`; prune below floor. Router
utterance mining and negative signals use the same decay so the routing
index tracks current usage (the "decay" requirement).

--------------------------------------------------------------------
## 6. Configuration (all knobs, no code)

```yaml
agents:
  memory:
    window_tokens: 2000            # exists
    summary_tokens: 300
    facts_top_k: 5
    episodes_top_k: 3
    layer_timeouts_ms: 250
    half_life_days: {facts: 90, episodes: 60, procedures: 45, router: 30}
    layers_enabled: [working, semantic, episodic, prospective]
  router:
    judge_enabled: true
    judge_band: 0.08               # score band that triggers the judge
    collision_blocking: true
    utterance_mining: true
```

--------------------------------------------------------------------
## 7. Build phases (each verifiable independently)

- **M10a — Router upgrades T1 + contextualizer** (small, immediate): fallback
  exclusion, blocking collision gate, QueryContextualizer wired before
  routing. Verify: follow-up questions route correctly in a fresh test.
- **M10b — Memory core**: scope/record/layer contracts, orchestrator,
  working+semantic selection (hybrid recency+relevance window), rolling
  summary, envelope memory block, SMS parity test.
- **M10c — Long-term layers**: facts/episodes extraction + recall,
  prospective memory + scheduler tick, decay job, anticipation chips.
- **M10d — Router learning (T2/T3)**: LLM judge on ambiguous band,
  calibrated thresholds, utterance mining + negative feedback loop.

Dependencies: none on M5 (delegation) — parallel tracks. Langfuse
tracing (separate item) makes M10d measurable and should land with it.

--------------------------------------------------------------------
## 8. Memory security and privacy (mandatory, not optional)

Research grounding: regulated-industry practice for LLM memory is
redact-before-store (entity detection on inputs before anything is
persisted or sent to a model), field-level encryption at rest with
customer-managed keys, PHI kept out of logs, tokenization where content
must remain linkable, and documented retention + deletion evidence for
HIPAA/GDPR review. Aubrey adopts all four.

**8.1 Redaction pipeline (before any memory write).**
`MemoryRedactor` sits inside `MemoryOrchestrator.commit`, before every
`layer.record()`: entity detection (names, MRNs, SSNs, DOBs, addresses,
credential/token patterns, card numbers) → REDACT for memory layers
(facts/episodes store "member asked about knee MRI appeal", never the
member's SSN) and TOKENIZE where linkage is needed (stable per-tenant
token replaces the raw identifier; the mapping lives in its own
encrypted table). Credentials and secrets are never stored in any
memory layer under any condition — detected creds drop the record and
raise an alert. Detection = rule set + NER, config-owned patterns
(yaml `agents.memory.redaction`), never hardcoded.

**8.2 Encryption at rest (field level).**
All new memory tables (`memory_facts`, `memory_episodes`,
`memory_procedures`, `memory_prospects`, `session_summaries`) encrypt
their `content`/`summary` columns app-side (AES-GCM via a
`FieldEncryptor` — the pattern already proven in the old AAAS platform)
with keys held in Azure Key Vault, per tenant, rotatable. Embeddings are
computed from the REDACTED text, so vectors never encode raw
identifiers. Existing sensitive columns join the same pass:
`sms_messages.body`, `sms_consent.phone` (encrypt + HMAC hash column
for lookup, as AAAS did), `chat_messages.content` (phase M10c).
Postgres TDE/disk encryption stays on underneath; field-level is the
control we own and can evidence.

**8.3 External subjects — the SMS/contract reality.**
Campaign recipients are NOT org users: patients and members outside
Entra, reached under a consent "contract". Their memory is a separate
class with stricter rules:

- `MemoryScope` gains `subject_type: internal | external`. External
  scope is derived automatically (user_id prefix `sms:`/`voice:`).
- **Consent-bound lifecycle**: external memory exists only while consent
  is opted_in. STOP/opt-out → prospective memory for that subject is
  cancelled and facts/episodes enter retention countdown (yaml
  `external_retention_days`); the consent ledger row itself is retained
  as legal evidence (TCPA burden of proof) but message bodies and memory
  content are purged on schedule.
- **Right to deletion**: `DELETE /api/v1/admin/subjects/{token}` erases
  all memory layers + message bodies for one external subject and writes
  a deletion-evidence row (what was deleted, when, by whom) — the
  auditable answer to an erasure request.
- **Minimal collection**: external extraction runs an allowlist —
  campaign-relevant facts only (config per campaign: e.g. preferred
  language, callback window). No health details in external memory:
  the least-PHI rule already enforced in SMS prompts extends to what
  the extractor may store.
- **No cross-campaign leakage**: external facts are scoped
  (tenant, subject, campaign); a BP-outreach fact is never recalled in
  a billing campaign's thread. Internal users keep tenant-wide recall.
- **Channel parity preserved**: the same MemoryLayer code serves both;
  only scope rules differ, and they are policy objects
  (`MemoryPolicy.for_scope(scope)`), config-driven, not branches
  scattered through code.

**8.4 Evidence trail.** Every redaction, encryption, recall, and
deletion decision logs a structured audit event (no content, metadata
only) — detection counts, layer, scope, policy applied — turning
privacy behavior into reviewable evidence for HIPAA/GDPR audits.

Phase placement: 8.1 + 8.2 land IN M10b (the core cannot ship
unencrypted); 8.3 lands with M10c alongside prospective memory (it
gates outreach follow-ups); 8.4 accompanies every phase.

--------------------------------------------------------------------
## 9. Full-application data protection (from the line-by-line audit)

A complete backend audit (every entity, service, and route) produced a
data inventory and ranked fixes. Nothing in the schema is encrypted
today; only three values are hashed (team token, CSRF, ip/user-agent).

**9.1 Sensitivity inventory (what must be protected).**
- PHI/CONTENT columns: chat_messages.content+metadata, chat_sessions.title
  (derived from first message), message_edit_versions.content,
  message_feedback.feedback, session_documents.content (highest density —
  full uploaded documents), sms_messages.body+vendor_details,
  document_chunks.content+embedding_text, knowledge_graph_nodes
  name/description (LLM-extracted people/medications), agent_routes.utterance.
- PII: users email/names, sms phone columns (indexed three ways),
  browser_sessions profile JSONB, odt_teams.contact_email.
- CREDENTIAL: connections.config.auth_header_value (vendor secrets,
  PLAINTEXT and echoed back on GET), team_tokens.token_hash (unsalted
  SHA-256), browser_sessions.session_id (raw cookie value as PK — a DB
  read is session takeover).
- AUDIT (retain): sms_consent(+history), policy_audit_log.

**9.2 Quick wins (Phase 0 — before any new feature).**
1. Twilio webhook fails CLOSED when unconfigured (today it fails open:
   unauthenticated writes + LLM dispatch possible in dev/uat/prd where
   auth_token is a placeholder; move it to Key Vault lookup:).
2. Mask connection.config on every read (redact *secret|token|password|
   key* fields) — stop returning vendor credentials to admin GETs.
3. Logging hygiene: remove the OAuth token-endpoint body log and
   str(exc) auth logs; add a redaction logging.Filter on the shared
   handler; disable rich_tracebacks local-variable rendering (the one
   place chat/SMS bodies reach the console today).
4. Hash browser_sessions.session_id with the existing SessionCrypto;
   tenant-scope team token validation; drop hash-prefix from the mask.
5. Store the classified keyword enum in sms_consent.keyword, not the
   raw inbound body.
6. Fix latent defects found while reading: TeamTokenService.list_for_team
   self._db unassigned; ChatSessionService._owned_message kwarg typo;
   chat_route calls two service methods that do not exist.

**9.3 Encryption at rest — full app, with the two real blockers.**
FieldEncryptor (AES-GCM, per-tenant Key Vault keys) over every PHI/PII/
CREDENTIAL column in 9.1 — but two schema features read plaintext and
must be redesigned FIRST:
- GENERATED tsvector columns (document_chunks, agent_routes) power
  sparse retrieval/routing — move sparse search to a derived token table
  built from redacted text (or a keyed deterministic index) before
  encrypting the source columns.
- Phone columns are indexed for lookup — searchable scheme: encrypted
  value + HMAC hash column for equality + last4 for display (the old
  AAAS pattern, proven).
Embeddings are computed from redacted text; HNSW indexes stay on
vectors, which never contain raw identifiers after redaction.

**9.4 Envelope signing.** The capability-plane context envelope is
self-asserted today: a team-token holder can claim any user_id/roles/
session_id — the only gate on files/context returning full document
text. Sign the envelope (HMAC with a platform key, timestamped) when
aubrey dispatches; capability endpoints verify signature + freshness.
Agents can no longer mint identities. (Also closes the same gap for
/mcp, which runs with roles=("service",).)

**9.5 Retention and erasure service (whole app, not just memory).**
- chat_sessions: archive → hard purge after N days (FK cascade then
  reaps messages, edits, session_documents, sms_threads). Today archive
  never deletes and uploaded document text is permanent.
- TTL jobs: sms_messages bodies (metadata retained), genie_conversations,
  browser_sessions purge_expired (exists, never scheduled — schedule it).
- Retain-list with longer clocks: sms_consent, policy_audit_log.
- Right-to-erasure endpoint (external subjects first, 8.3) with
  deletion-evidence rows.
All windows yaml-owned (retention: section per table class).

**9.6 Phasing update.** Phase 0 (9.2 quick wins + latent bugs) precedes
M10a; 9.3 encryption + 9.5 retention land as a parallel security track
(M10-S1 crypto foundations incl. sparse-index redesign, M10-S2
retention/erasure, M10-S3 envelope signing) alongside M10b-d, so the
memory layers are born onto an already-encrypted substrate.
