# One image per agent kind: assieme-agent-<kind>:1. Built on this Mac by Assieme, on the person's click.
FROM node:22-bookworm-slim
ARG KIND
# git for the agent's copy and procps for its tools; asm runs Assieme's status hook and team tools.
RUN apt-get update && apt-get install -y --no-install-recommends git procps ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
COPY asm /usr/local/bin/asm
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
