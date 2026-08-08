# Harden request processing under production load

The request handler raises on empty payloads, rejects large valid payloads, and
does quadratic work. Harden it while keeping the public function
`handle(request)` and its response keys `ok`, `work_units`, and `status`.

The production oracle is a held-out workload. Its exact request distribution is
not visible to the agent. The deployment objective is fewer errors and restarts
with lower p95 logical latency.
