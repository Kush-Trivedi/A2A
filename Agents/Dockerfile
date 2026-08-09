# Shared image recipe for EVERY team agent — build from the Agents/ folder:
#   docker build -f Dockerfile --build-arg AGENT=scheduling_agent -t ace/scheduling-agent .
# The same recipe serves all agents; only the AGENT build-arg changes.
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

ARG AGENT
WORKDIR /srv

# Shared kit first (path dependency), then the agent itself.
COPY agent_kit/ /srv/agent_kit/
COPY ${AGENT}/ /srv/${AGENT}/

WORKDIR /srv/${AGENT}
RUN uv sync --frozen --no-dev || uv sync --no-dev

# Port comes from the agent's own agent.yaml; expose is documentation only.
EXPOSE 3100-3700
CMD ["uv", "run", "python", "-m", "app.main"]
