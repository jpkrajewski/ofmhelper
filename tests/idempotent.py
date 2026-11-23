# Copyright (C) 2026 jpkrajewski

import uuid


def keyed() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}
