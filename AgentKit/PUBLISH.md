# Publishing ace-agent-kit to Azure Artifacts

The kit is the ONE shared implementation teams install (`uv add
ace-agent-kit`). This monorepo is its source of truth; teams consume released
versions from an Azure Artifacts feed — path dependencies exist only inside
the monorepo.

## One-time setup

1. Create a feed (org-scoped so all team projects can read):
   Azure DevOps → Artifacts → Create Feed → e.g. `ace-packages`.
2. Note the PyPI-compatible URLs:
   - upload: `https://pkgs.dev.azure.com/<org>/_packaging/ace-packages/pypi/upload/`
   - simple: `https://pkgs.dev.azure.com/<org>/_packaging/ace-packages/pypi/simple/`

## Manual publish (from AgentKit/)

```powershell
uv build                                     # builds sdist + wheel into dist/
uv publish --publish-url https://pkgs.dev.azure.com/<org>/_packaging/ace-packages/pypi/upload/ `
  --username any --password <ADO_PAT_with_Packaging_ReadWrite>
```

## Pipeline publish (on tag) — azure-pipelines snippet

```yaml
trigger:
  tags:
    include: ["kit-v*"]
steps:
  - task: UsePythonVersion@0
    inputs: { versionSpec: "3.13" }
  - script: pip install uv && uv build
    workingDirectory: AgentKit
  - task: TwineAuthenticate@1
    inputs: { artifactFeed: "ace-packages" }
  - script: pip install twine && twine upload -r ace-packages --config-file $(PYPIRC_PATH) AgentKit/dist/*
```

## Rules

- **Semver.** Bump `version` in pyproject.toml with every publish; teams pin
  `ace-agent-kit>=X.Y,<X.Y+1` and upgrade deliberately.
- Never publish from a dirty tree; tag `kit-vX.Y.Z` = the released source.
- Team repos consume via:
  ```toml
  [[tool.uv.index]]
  name = "ace-artifacts"
  url = "https://pkgs.dev.azure.com/<org>/_packaging/ace-packages/pypi/simple/"
  ```
  plus `uv add ace-agent-kit` (auth via `UV_INDEX_ACE_ARTIFACTS_USERNAME/PASSWORD`
  or azure keyring in pipelines).
