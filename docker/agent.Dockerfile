FROM python:3.12-slim
# net tools so the adversarial boundary probe can genuinely try to reach the oracle,
# plus node + the claude CLI for the real-agent pilot. Installing the toolchain at
# build time spends NO model quota; only invoking the agent (the gated run) does.
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl iproute2 dnsutils iputils-ping ca-certificates gnupg && \
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    npm install -g @anthropic-ai/claude-code && \
    npm cache clean --force && rm -rf /var/lib/apt/lists/*
WORKDIR /code
CMD ["sleep", "infinity"]
