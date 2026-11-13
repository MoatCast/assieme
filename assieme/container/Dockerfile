# One image per agent kind: assieme-agent-<kind>:1. Built on this Mac by Assieme, on the person's click.
FROM node:22-bookworm-slim
ARG KIND
ARG MCP_VERSION
# git for the agent's copy, python3 and procps for Assieme's status hook and team tools.
RUN apt-get update && apt-get install -y --no-install-recommends git python3 python3-pip procps ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
RUN pip3 install --no-cache-dir --break-system-packages "mcp==${MCP_VERSION}"
RUN case "$KIND" in \
      claude) npm install -g @anthropic-ai/claude-code ;; \
      codex) npm install -g @openai/codex ;; \
      gemini) npm install -g @google/gemini-cli ;; \
      opencode) npm install -g opencode-ai ;; \
      cursor) HOME=/opt/cursor sh -c 'curl -fsS https://cursor.com/install | bash' \
              && ln -s /opt/cursor/.local/bin/cursor-agent /usr/local/bin/cursor-agent && chmod -R a+rX /opt/cursor ;; \
      *) echo "unknown kind $KIND" && exit 1 ;; \
    esac
USER node
WORKDIR /home/node
