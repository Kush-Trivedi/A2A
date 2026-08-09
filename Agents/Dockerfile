# Shared image recipe for EVERY team agent — build from the repo root:
#   docker build -f Agents/Dockerfile --build-arg AGENT=scheduling_agent -t ace/scheduling-agent .
# The same recipe serves all agents; only the AGENT build-arg changes.
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

ARG AGENT
WORKDIR /srv

# Mirror the repo layout so the agents' ../../agent_kit path dependency
# resolves identically on host and in-container.
COPY agent_kit/ /srv/agent_kit/
COPY Agents/${AGENT}/ /srv/Agents/${AGENT}/

WORKDIR /srv/Agents/${AGENT}
RUN uv sync --frozen --no-dev || uv sync --no-dev

# Port comes from the agent's own agent.yaml; expose is documentation only.
EXPOSE 3100-3800
CMD ["uv", "run", "python", "-m", "app.main"]
