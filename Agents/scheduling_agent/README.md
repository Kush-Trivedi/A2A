# Scheduling Agent — ODT Team Template

This is the template every ODT team copies to build an agent on the ACE platform,
using the **official Google A2A SDK**. The platform (ACE) owns auth, RBAC, chat,
RAG, and audit. Your team owns everything in this folder.

## What your team edits

| File | What it is |
|---|---|
| `agent.yaml` | Your manifest: identity, skills, **your** Databricks resources (warehouse, catalog, Genie space), the roles allowed to reach your agent. |
| `app/agent_executor.py` | Your agent logic. Replace the canned response with your real workflow. |

You never configure Entra, sessions, or Casbin — ACE handles the common concerns.

## Run locally

```powershell
uv sync
uv run python -m app.main
```

The agent serves the A2A protocol on the port from `agent.yaml`, including your
AgentCard at `/.well-known/agent-card.json`.

## Register with ACE (automation)

One command stores your agent in the ACE registry **and** seeds the access
policies for the roles you listed in `agent.yaml`:

```powershell
uv run python -m app.register --cookie "<ace_session_cookie>" --csrf "<csrf_token>"
```

Re-run it whenever `agent.yaml` changes — registration is an upsert.

## Talking to other teams' agents

Resolve the other agent's card from the ACE registry (`GET /api/v1/admin/agents`),
then use the A2A client from `a2a-sdk` against its `card_url`. Always forward the
context envelope you received (tenant, actor, correlation id) so the chain stays
auditable.
