# DEFERRED: document_chunks content encryption (M10-S1 decision record)

`document_chunks.content` and `document_chunks.embedding_text` are **not**
encrypted in the M10-S1 pass, deliberately.

## Why

`document_chunks.search_vector` is a **GENERATED** tsvector column
(`to_tsvector('english', coalesce(embedding_text, content))`, see
`entity/knowledge/document_chunk_entity.py`) with a GIN index — it is the
sparse half of hybrid retrieval and, in `mode: sparse` environments
(local), the ONLY retrieval signal. Postgres computes it **from the stored
column values**: if those columns became AES-GCM ciphertext, every
search_vector would degenerate to tokens of base64 noise and sparse
retrieval (and RRF hybrid with it) would silently return nothing.
`agent_routes.utterance` has the same shape for routing.

## Required redesign first (NEW_PLAN §9.3)

Move sparse search off the generated column before encrypting the source:

1. **Derived token table** — at ingest, build the tsvector in the app from
   the REDACTED plaintext and store it in a separate table/column that
   carries no recoverable text (a tsvector of redacted text leaks
   individual redacted tokens, not documents), then drop the generated
   column and point the sparse query at the token table. Or:
2. **Keyed deterministic index** — HMAC each token (the phone_hash
   pattern) into a searchable inverted index; heavier, leaks nothing.

Then `content`/`embedding_text` join the FieldEncryptor pass like every
other PHI column (encrypt in `knowledge_sink`/ingest, decrypt in
`retrieval_service` reads).

## What IS already true

- Dense vectors: embeddings are computed before any of this and HNSW
  operates on vectors, not text — unaffected by the redesign.
- Chat/SMS columns encrypted in M10-S1 (`chat_messages.content`,
  `session_documents.content`, `sms_messages.body`,
  `message_edit_versions.content`) have **no** tsvector — safe today.

Owner: security track M10-S1 follow-up; blocks nothing in M10-S2/M10c.
