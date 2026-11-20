from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from ofmhelpers.db.models import UserCookiesRow
from ofmhelpers.db.session import session_scope
from ofmhelpers.schemas.cookies import StoredCookies


class CookieRepository:
    def get(self, user_id: int) -> StoredCookies | None:
        with session_scope() as s:
            row = s.get(UserCookiesRow, user_id)
            if row is None:
                return None
            return StoredCookies(
                cookies=row.cookies,
                file_name=row.file_name,
                uploaded_at=row.updated_at.timestamp(),
            )

    def first_holder(self, user_ids: list[int]) -> int | None:
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
        values: dict = {"cookies": cookies, "file_name": file_name}
        with session_scope() as s:
            saved = s.execute(
                insert(UserCookiesRow)
                .values(user_id=user_id, **values)
                .on_conflict_do_update(index_elements=["user_id"], set_=values)
                .returning(UserCookiesRow.updated_at)
            ).scalar_one()
        return saved.timestamp()

    def delete(self, user_id: int) -> bool:
        with session_scope() as s:
            removed = s.execute(
                delete(UserCookiesRow)
                .where(UserCookiesRow.user_id == user_id)
                .returning(UserCookiesRow.user_id)
            )
            return bool(removed.all())
