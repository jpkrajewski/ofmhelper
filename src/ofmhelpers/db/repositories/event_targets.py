"""Where a person's events go, per sender (`user_event_targets`, alembic
b2c3d4e5f6a9). Nothing cached: a target is where messages go, and it must
never reach Redis or a log."""

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from ofmhelpers.db.models import UserEventTargetRow
from ofmhelpers.db.session import session_scope


class EventTargetRepository:
    def of(self, user_id: int, sender: str) -> str | None:
        """`user_id`'s own target on `sender`, or None."""
        row = UserEventTargetRow
        with session_scope() as s:
            return s.execute(
                select(row.target).where(row.user_id == user_id, row.sender == sender)
            ).scalar_one_or_none()

    def save(self, user_id: int, sender: str, target: str) -> None:
        """Stores or replaces it."""
        with session_scope() as s:
            s.execute(
                insert(UserEventTargetRow)
                .values(user_id=user_id, sender=sender, target=target)
                .on_conflict_do_update(
                    index_elements=["user_id", "sender"], set_={"target": target}
                )
            )

    def remove(self, user_id: int, sender: str) -> bool:
        """False when there was none."""
        row = UserEventTargetRow
        with session_scope() as s:
            gone = s.execute(
                delete(row)
                .where(row.user_id == user_id, row.sender == sender)
                .returning(row.user_id)
            )
            return bool(gone.all())
