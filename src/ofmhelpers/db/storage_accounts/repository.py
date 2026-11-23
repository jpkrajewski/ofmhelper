from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from ofmhelpers.db.models import StorageAccountRow
from ofmhelpers.db.session import session_scope
from ofmhelpers.db.storage_accounts.models import StorageAccount
from ofmhelpers.db.users.schema import StorageProvider


def _account(row: StorageAccountRow) -> StorageAccount:
    return StorageAccount(
        user_id=row.user_id,
        provider=StorageProvider(row.provider),
        account_id=row.account_id,
        email=row.email,
        connected_at=row.connected_at.timestamp(),
    )


class StorageAccountRepository:
    def save(
        self,
        user_id: int,
        provider: StorageProvider,
        refresh_token: str,
        account_id: str,
        email: str,
    ) -> None:
        row = StorageAccountRow
        with session_scope() as s:
            s.execute(
                insert(row)
                .values(
                    user_id=user_id,
                    provider=provider.value,
                    refresh_token=refresh_token,
                    account_id=account_id,
                    email=email,
                )
                .on_conflict_do_update(
                    index_elements=[row.user_id, row.provider],
                    set_={
                        "refresh_token": refresh_token,
                        "account_id": account_id,
                        "email": email,
                        "connected_at": func.now(),
                    },
                )
            )

    def refresh_token(self, user_id: int, provider: StorageProvider) -> str | None:
        with session_scope() as s:
            return s.execute(
                select(StorageAccountRow.refresh_token).where(
                    StorageAccountRow.user_id == user_id,
                    StorageAccountRow.provider == provider.value,
                )
            ).scalar_one_or_none()

    def get(self, user_id: int, provider: StorageProvider) -> StorageAccount | None:
        with session_scope() as s:
            row = s.get(StorageAccountRow, (user_id, provider.value))
            return _account(row) if row else None
