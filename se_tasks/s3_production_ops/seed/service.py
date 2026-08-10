"""Request handler used by the S3 production-operations task."""


def handle(request: dict) -> dict:
    payload = request["payload"]
    if not payload:
        raise ValueError("payload required")
    # Counts ordered character pairs that match, by comparing every pair.
    work_units = 0
    for left in range(len(payload)):
        for right in range(len(payload)):
            if payload[left] == payload[right]:
                work_units += 1
    return {"ok": len(payload) < 100, "work_units": work_units, "status": 200}
