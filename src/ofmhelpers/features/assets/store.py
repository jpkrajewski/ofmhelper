# Copyright (C) 2026 jpkrajewski

import hashlib
import io
import os
import secrets
import shutil
import tempfile
from pathlib import Path
from typing import BinaryIO, Protocol, cast

import pillow_heif
from PIL import Image

from ofmhelpers.config import settings
from ofmhelpers.db.users.store import get_user
from ofmhelpers.features.constants import file_kind_constants
from ofmhelpers.features.refusals import AssetRefusedError
from ofmhelpers.log import get_logger
lazy from ofmhelpers.features.assets.models import MediaUpload
lazy from ofmhelpers.features.kinds import FileKind
lazy from ofmhelpers.integrations.downloaders.models import DownloadResult

logger = get_logger(__name__)

EDIT_STAGING = ".edit"


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


def require_upload_kind(kind: FileKind, allowed: frozenset[FileKind]) -> None:
    if kind not in allowed:
        allowed_exts = sorted(ext for kind in allowed for ext in kind.extensions)
        msg = f"Unsupported file type. Allowed: {', '.join(allowed_exts)}"
        raise AssetRefusedError(msg)


class _MultiFrameImage(Protocol):
    format: str
    n_frames: int
    width: int
    height: int

    def seek(self, frame: int) -> None: ...

    def load(self) -> None: ...


def _load_multi_frame(image: _MultiFrameImage) -> None:
    frame_limit = settings.web.max_image_frames
    if image.format in file_kind_constants().sequential_frame_formats:
        frame_count = frame_limit + 1
    else:
        frame_count = image.n_frames
        if frame_count > frame_limit:
            msg = "Too many image frames"
            raise AssetRefusedError(msg)
    total_pixels = 0
    for frame_index in range(frame_count):
        try:
            image.seek(frame_index)
        except EOFError:
            if image.format in file_kind_constants().sequential_frame_formats:
                break
            raise
        if frame_index == frame_limit:
            msg = "Too many image frames"
            raise AssetRefusedError(msg)
        total_pixels += image.width * image.height
        if total_pixels > settings.web.max_image_total_pixels:
            msg = "Image exceeds the pixel limit"
            raise AssetRefusedError(msg)
        image.load()


def _require_gif_trailer(file: BinaryIO) -> None:
    file.seek(-1, 2)
    if file.read(1) != b";":
        raise OSError


def validate_image(file: BinaryIO) -> BinaryIO:
    try:
        Image.open(file).verify()
        file.seek(0)
        with Image.open(file) as image:
            if image.format in file_kind_constants().multi_frame_formats:
                _load_multi_frame(cast("_MultiFrameImage", image))
            else:
                image.load()
            if image.format == "GIF":
                _require_gif_trailer(file)
    except AssetRefusedError:
        raise
    except OSError, SyntaxError, ValueError, Image.DecompressionBombError, EOFError:
        msg = "Unreadable image"
        raise AssetRefusedError(msg) from None
    finally:
        file.seek(0)
    return file


def jpeg_from_heic_or_current(src: BinaryIO, path: Path) -> BinaryIO:
    if path.suffix.lower() not in file_kind_constants().heic_extensions:
        return src
    try:
        img = pillow_heif.open_heif(src, convert_hdr_to_8bit=True).to_pillow()
        out = io.BytesIO()
        img.convert("RGB").save(
            out,
            "JPEG",
            quality=100,
            subsampling=0,
            icc_profile=img.info.get("icc_profile"),
            exif=img.info.get("exif") or b"",
        )
        out.seek(0)
    except ValueError:
        logger.warning("could not decode HEIC upload %s", path.name, exc_info=True)
        msg = "Unreadable HEIC image"
        raise AssetRefusedError(msg) from None
    else:
        return out


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


def save_upload(job_dir: Path, upload: MediaUpload) -> str:
    dest = _free_name(job_dir, safe_filename(upload.filename))
    with dest.open("wb") as out:
        shutil.copyfileobj(upload.file, out)
    return str(dest)


def save_asset(upload: MediaUpload, assets_root: Path | None = None) -> str:
    stored_name = safe_filename(upload.filename)
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
    path: Path,
    assets_root: Path | None = None,
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
