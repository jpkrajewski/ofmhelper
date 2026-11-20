import hashlib
import os
import secrets
import shutil
import tempfile
from pathlib import Path

from starlette.datastructures import UploadFile

from ofmhelpers.config import settings
from ofmhelpers.db.stores.users import get_user
from ofmhelpers.features.assets.kinds import (
    AUDIO_EXTS,
    IMAGE_EXTS,
    VIDEO_EXTS,
    classify_kind,
)
from ofmhelpers.log import get_logger
from ofmhelpers.media.heic import HEIC_EXTENSIONS, heic_to_jpeg
lazy from ofmhelpers.integrations.downloaders.models import DownloadResult

logger = get_logger(__name__)


class AssetRefusedError(ValueError): ...


def uploads_root() -> Path:
    return Path(settings.infra.uploads_root)


def assets_root() -> Path:
    return uploads_root() / "assets"


def _store_root() -> Path:
    return assets_root()


def assets_root_for_user(user_id: int) -> Path:
    user = get_user(user_id)
    if user is not None and user.owner_id is not None:
        return assets_root_for_user(user.owner_id)
    if user is not None and user.is_password_account:
        return assets_root()
    return _user_root(user_id)


def _user_root(user_id: int | str) -> Path:
    return _users_dir() / str(user_id)


def _users_dir() -> Path:
    return assets_root() / "users"


def in_asset_root(root: Path, path: Path) -> bool:
    resolved = path.resolve()
    resolved_root = root.resolve()
    if resolved_root not in resolved.parents:
        return False
    if resolved_root != assets_root().resolve():
        return True
    thumbs = (assets_root() / ".thumbs").resolve()
    users = _users_dir().resolve()
    return users not in resolved.parents and thumbs not in resolved.parents


def assets_root_for_actor(actor: str | None) -> Path:
    if actor is None:
        return assets_root()
    if actor.isdigit():
        return assets_root_for_user(int(actor))
    return _user_root(actor)


IMAGE_KINDS = frozenset({"image"})


VIDEO_KINDS = frozenset({"video"})


MEDIA_KINDS = frozenset({"image", "video", "audio"})


def require_upload_kind(name: str | None, allowed: frozenset[str]) -> str:
    safe = safe_filename(name)
    if classify_kind(safe) not in allowed:
        allowed_exts = sorted(
            ext
            for kind, exts in (
                ("image", IMAGE_EXTS),
                ("video", VIDEO_EXTS),
                ("audio", AUDIO_EXTS),
            )
            if kind in allowed
            for ext in exts
        )
        msg = f"Unsupported file type. Allowed: {', '.join(allowed_exts)}"
        raise AssetRefusedError(msg)
    return safe


def convert_heic_upload(upload: UploadFile) -> UploadFile:
    name = upload.filename or ""
    path = Path(name.replace("\\", "/"))
    if path.suffix.lower() not in HEIC_EXTENSIONS:
        return upload
    try:
        jpeg = heic_to_jpeg(upload.file)
    except ValueError:
        logger.warning("could not decode HEIC upload %s", name, exc_info=True)
        msg = "Unreadable HEIC image"
        raise AssetRefusedError(msg) from None
    return UploadFile(file=jpeg, filename=path.with_suffix(".jpg").name)


def strip_asset_hash_prefix(filename: str) -> str:
    _, _, rest = filename.partition("__")
    return rest or filename


def safe_filename(name: str | None) -> str:
    base = Path((name or "").replace("\\", "/")).name
    if not base or base in {".", ".."} or "\0" in base:
        msg = "Invalid file name"
        raise AssetRefusedError(msg)
    return base


def make_job_dir(upload_root: Path) -> Path:
    upload_root.mkdir(parents=True, exist_ok=True)
    while True:
        job_dir = upload_root / _new_job_dir_name()
        try:
            job_dir.mkdir()
        except FileExistsError:  # silent: name taken, the loop draws another
            continue
        return job_dir


def _new_job_dir_name() -> str:
    return secrets.token_hex(16)


def _free_name(job_dir: Path, name: str) -> Path:
    dest = job_dir / name
    number = 1
    while dest.exists():
        number += 1
        dest = job_dir / f"{Path(name).stem}_{number}{Path(name).suffix}"
    return dest


def save_upload(job_dir: Path, upload: UploadFile) -> str:
    dest = _free_name(job_dir, safe_filename(upload.filename))
    with dest.open("wb") as out:
        shutil.copyfileobj(upload.file, out)
    return str(dest)


def save_asset(upload: UploadFile, assets_root: Path | None = None) -> str:
    upload = convert_heic_upload(upload)
    stored_name = require_upload_kind(upload.filename, MEDIA_KINDS)
    assets_root = assets_root or _store_root()
    assets_root.mkdir(parents=True, exist_ok=True)

    hasher = hashlib.sha256()
    with tempfile.NamedTemporaryFile(dir=assets_root, delete=False) as tmp:
        tmp_path = Path(tmp.name)
        try:
            for chunk in iter(lambda: upload.file.read(1 << 20), b""):
                hasher.update(chunk)
                tmp.write(chunk)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise
    try:
        digest = hasher.hexdigest()
        existing = next(assets_root.glob(f"{digest}__*"), None)
        if existing is not None:
            return str(existing)
        final_path = assets_root / f"{digest}__{stored_name}"
        try:
            tmp_path.rename(final_path)
        except FileExistsError:  # silent: a concurrent upload of the same content won
            tmp_path.unlink(missing_ok=True)
        return str(final_path)
    finally:
        tmp_path.unlink(missing_ok=True)


def register_generated_asset(
    path: Path, assets_root: Path | None = None
) -> Path | None:
    assets_root = assets_root or _store_root()
    try:
        assets_root.mkdir(parents=True, exist_ok=True)
        hasher = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                hasher.update(chunk)
        digest = hasher.hexdigest()

        existing = next(assets_root.glob(f"{digest}__*"), None)
        if existing is not None:
            return existing

        dest = assets_root / f"{digest}__{path.name}"
        try:
            os.link(path, dest)
        except OSError:  # silent: no hard links here: copied instead
            path.copy(dest, preserve_metadata=True)
    except OSError:
        logger.warning("could not register generated asset %s", path, exc_info=True)
        return None
    else:
        return dest


def register_grouped_results(results: list[DownloadResult], assets_root: Path) -> None:
    for result in results:
        for path in result.output_paths:
            register_generated_asset(path, assets_root)
