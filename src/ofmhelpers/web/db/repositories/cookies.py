"""Each user's own cookies.txt (`user_cookies`). Nothing here is cached:
every read returns or derives from the decrypted cookies, which must never
reach Redis."""

from __future__ import annotations

import time

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from ofmhelpers.web.db.models import UserCookiesRow
from ofmhelpers.web.db.session import session_scope


class CookieRepository:
    def get(self, user_id: int) -> dict | None:
        """{"cookies" (decrypted), "file_name", "uploaded_at"}, or None."""
        with session_scope() as s:
            row = s.get(UserCookiesRow, user_id)
            if row is None:
                return None
            return {
                "cookies": row.cookies,
                "file_name": row.file_name,
                "uploaded_at": row.uploaded_at,
            }

    def first_holder(self, user_ids: list[int]) -> int | None:
        """The first of `user_ids`, in that order, who has cookies."""
        if not user_ids:
            return None
        with session_scope() as s:
            holders = set(
                s.execute(
                    select(UserCookiesRow.user_id).where(
                        UserCookiesRow.user_id.in_(user_ids)
                    )
                ).scalars()
            )
        return next((u for u in user_ids if u in holders), None)

    def save(self, user_id: int, cookies: str, file_name: str) -> float:
        """Stores or replaces `user_id`'s cookies; returns `uploaded_at`."""
        values: dict = {
            "cookies": cookies,
            "file_name": file_name,
            "uploaded_at": time.time(),
        }
        with session_scope() as s:
            s.execute(
                insert(UserCookiesRow)
                .values(user_id=user_id, **values)
                .on_conflict_do_update(index_elements=["user_id"], set_=values)
            )
        return values["uploaded_at"]

    def delete(self, user_id: int) -> bool:
        with session_scope() as s:
            removed = s.execute(
                delete(UserCookiesRow)
                .where(UserCookiesRow.user_id == user_id)
                .returning(UserCookiesRow.user_id)
            )
            return bool(removed.all())
