# Agent template — the config contract

Copy this folder to start a team agent (in the monorepo now; as your own
Azure DevOps repo once the Artifacts feed is live). It defines the
**configuration contract** every ACE agent follows; the runnable pieces
(main/card/executor/registrar) land with the retrieval template.

## The two-file rule

| File | Varies by env? | Holds |
|---|---|---|
| `agent.yaml` | **NEVER** | Identity, skills (+ routing examples), versioned prompts, delegations — your product definition. Registered and snapshotted by ACE. |
| `config/env/<ENV>.yaml` | values only | server, ace URLs + registration token, auth, LLM **deployment names**, retrieval, connections (by name), channels opt-in, your Key Vault. **Same keys in all four files.** |

## Same feel, every environment

```powershell
# local: values are hardcoded in config/env/local.yaml
uv sync
uv run python -m app.main            # ENV defaults to "local"

# dev/uat/prd: identical code; secrets say  lookup:<secret-name>  and resolve
# from YOUR team's Key Vault (managed identity). Only ENV changes:
$env:ENV = "dev"
uv run python -m app.main
```

There is no local mode vs prod mode — one code path, values differ per file.
`AgentContext` (from `ace-agent-kit`) enforces it: env-var override wins
(`AGENT_<SECTION>_<KEY>`), `lookup:` hits Key Vault, `your_*` placeholders
are reported with their exact yaml path at startup.

## Rules of the road

- LLM: you declare **deployment names** only; the Foundry endpoint + key stay
  in ACE. You can point dev and prd at different deployments.
- Integrations: reference **connections by name** (registered with ACE once);
  never paste secrets into these files.
- Channels: everything is opt-in — unused channels stay `enabled: false`.
- Routing: your `skills[].examples` are how questions find your agent.
  Make them realistic and distinct; ACE warns at registration if they
  overlap another agent's.
