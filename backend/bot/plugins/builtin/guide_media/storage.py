"""Content-addressed storage for guide screenshots."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import re
import tempfile
import threading
import time
from collections.abc import Generator
from pathlib import Path

from bot.services.message_image_service import (
    MESSAGE_IMAGE_CONTENT_TYPE,
    MessageImageError,
    PreparedMessageImage,
    UploadedMessageImage,
    prepare_message_image,
)

GUIDE_MEDIA_CONTENT_TYPE = MESSAGE_IMAGE_CONTENT_TYPE
GUIDE_MEDIA_DIR = Path(__file__).resolve().parents[5] / "data" / "guide-media"

_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")


def media_path(digest: str) -> Path:
    if not _DIGEST_RE.fullmatch(digest):
        raise MessageImageError("invalid_image", "Stored image identifier is invalid")
    return GUIDE_MEDIA_DIR / digest[:2] / f"{digest}.webp"


def media_url(digest: str) -> str:
    return f"/api/plugins/guide_media/media/{digest}.webp"


async def prepare_image(upload: UploadedMessageImage) -> PreparedMessageImage:
    prepared = await prepare_message_image(upload)
    if prepared is None:
        raise MessageImageError("empty_image", "Image file is empty")
    return prepared


def _write_image_atomically(prepared: PreparedMessageImage) -> Path:
    target = media_path(prepared.digest)
    if target.is_file():
        try:
            existing = target.read_bytes()
        except OSError:
            existing = b""
        if hashlib.sha256(existing).hexdigest() == prepared.digest:
            return target
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{prepared.digest}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(prepared.data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, target)
    finally:
        with contextlib.suppress(OSError):
            Path(temporary_name).unlink(missing_ok=True)
    return target


async def store_image(prepared: PreparedMessageImage) -> Path:
    return await asyncio.to_thread(_write_image_atomically, prepared)


async def read_image(digest: str, *, size_bytes: int) -> bytes:
    path = media_path(digest)
    try:
        body = await asyncio.to_thread(path.read_bytes)
    except OSError as exc:
        raise MessageImageError("image_missing", "Stored image is unavailable") from exc
    if len(body) != size_bytes or hashlib.sha256(body).hexdigest() != digest:
        raise MessageImageError("image_corrupt", "Stored image failed its integrity check")
    return body


async def delete_image_file(digest: str) -> None:
    if not _DIGEST_RE.fullmatch(digest):
        return
    path = media_path(digest)
    await asyncio.to_thread(path.unlink, missing_ok=True)


class StaleDigestScanner:
    """Retain a bounded scandir cursor across janitor runs, including live files.

    Only the 256 canonical digest shards are visited. No directory is listed or
    sorted in full. The open iterator resumes after the last inspected entry;
    referenced/young/invalid entries consume the same budget as orphan files.
    Restarting the worker restarts the traversal, without losing media safety.
    """

    def __init__(self) -> None:
        self._cursor = self._paths()
        self._lock = threading.Lock()

    def _paths(self) -> Generator[Path]:
        for shard in range(256):
            directory = GUIDE_MEDIA_DIR / f"{shard:02x}"
            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        yield Path(entry.path)
            except OSError:
                continue

    def close(self) -> None:
        with self._lock:
            self._cursor.close()

    def scan(self, *, older_than_seconds: float, limit: int) -> list[str]:
        # Cancellation of to_thread does not stop its thread. Protect the
        # retained iterator against another run or shutdown until it finishes.
        with self._lock:
            return self._scan(older_than_seconds=older_than_seconds, limit=limit)

    def _scan(self, *, older_than_seconds: float, limit: int) -> list[str]:
        cutoff = time.time() - max(0.0, older_than_seconds)
        candidates: list[str] = []
        for _ in range(max(0, limit)):
            path = next(self._cursor, None)
            if path is None:
                self._cursor = self._paths()
                break
            digest = path.stem
            if path.suffix != ".webp" or not _DIGEST_RE.fullmatch(digest):
                continue
            if path.parent.name != digest[:2]:
                continue
            try:
                if path.stat().st_mtime <= cutoff and path.is_file():
                    candidates.append(digest)
            except OSError:
                continue
        return candidates

    async def stale_digests(self, *, older_than_seconds: float, limit: int) -> list[str]:
        return await asyncio.to_thread(
            self.scan, older_than_seconds=older_than_seconds, limit=limit
        )
