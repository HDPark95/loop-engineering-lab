FROM node:22-bookworm-slim
ARG CLAUDE_CODE_VERSION=2.1.201
ARG CODEX_VERSION=0.144.1
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates python3 python3-pip \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m pip install --break-system-packages --no-cache-dir \
    asgiref==3.8.1 sqlparse==0.5.1 tzdata==2024.2 \
    && npm install -g \
    "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
    "@openai/codex@${CODEX_VERSION}" \
    && npm cache clean --force
ENV PYTHONPATH=/workspace
WORKDIR /workspace
CMD ["sleep", "infinity"]
