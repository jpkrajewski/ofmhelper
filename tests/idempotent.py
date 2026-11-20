import uuid


def keyed() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}
