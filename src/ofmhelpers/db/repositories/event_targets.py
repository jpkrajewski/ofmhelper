"""Where a person's events go, per sender (`user_event_targets`, alembic
b2c3d4e5f6a9). Nothing cached: a target is where messages go, and it must
never reach Redis or a log."""

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

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

    def save(self, user_id: int, sender: str, target: str) -> bool:
        """Stores or replaces it. False: the person was deleted meanwhile (a
        link opened after its person was removed), nothing saved."""
        try:
            with session_scope() as s:
                s.execute(
                    insert(UserEventTargetRow)
                    .values(user_id=user_id, sender=sender, target=target)
                    .on_conflict_do_update(
                        index_elements=["user_id", "sender"], set_={"target": target}
                    )
                )
        except IntegrityError as exc:  # silent: the person is gone, the caller logs it
            if "user_event_targets_user_id_fkey" not in str(exc.orig):
                raise
            return False
        return True

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
