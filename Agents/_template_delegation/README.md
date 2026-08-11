# Delegation template (reference pair)

`scheduling_agent` + `insurance_agent` — the retired phase-5 demo pair, kept
as the ONLY reference for cross-team delegation:

- `AgentDelegator` with mandatory `ContextEnvelope` forwarding (actor, tenant,
  roles, delegated_from, delegation_reason) + `referenceTaskIds`
- the receiving agent proving identity crossed the team boundary

They use the OLD single-file agent.yaml layout and are NOT part of the active
lineup — do not register or deploy them. When your team needs delegation,
copy the `AgentDelegator` usage from `scheduling_agent/app/agent_executor.py`
into a current-layout agent (see `Agents/_template/`).
