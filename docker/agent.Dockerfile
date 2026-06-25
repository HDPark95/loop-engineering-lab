FROM python:3.12-slim
# net tools so the adversarial boundary probe can genuinely try to reach the oracle
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl iproute2 dnsutils iputils-ping && rm -rf /var/lib/apt/lists/*
WORKDIR /code
CMD ["sleep", "infinity"]
