FROM node:22-bookworm-slim
ARG CLAUDE_CODE_VERSION=2.1.201
ARG CODEX_VERSION=0.144.1
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates python3 \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g \
    "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
    "@openai/codex@${CODEX_VERSION}" \
    && npm cache clean --force
WORKDIR /workspace
CMD ["sleep", "infinity"]
