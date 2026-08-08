"""Request handler used by the S3 production-operations task."""


def handle(request: dict) -> dict:
    payload = request["payload"]
    if not payload:
        raise ValueError("payload required")
    work_units = len(payload) * len(payload)
    return {"ok": len(payload) < 100, "work_units": work_units, "status": 200}
