"""Validation helpers for Web Push VAPID configuration.

The module is deliberately small and dependency-light: settings validation and
the built-in ``web_push`` plugin share the same base64url and P-256 parsing so
an operator cannot start with keys that look valid in the admin panel but fail
on the first delivery.
"""

from __future__ import annotations

import base64
import binascii
import re
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

VAPID_PUBLIC_KEY_BYTES = 65
VAPID_PRIVATE_KEY_BYTES = 32
_BASE64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class WebPushKeyError(ValueError):
    """Raised when a VAPID key or subject is malformed."""


def b64url_decode(value: str) -> bytes:
    """Decode unpadded base64url, rejecting non-url-safe input."""

    text = str(value or "").strip()
    if not text or not _BASE64URL_RE.fullmatch(text):
        raise WebPushKeyError("invalid base64url value")
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError) as exc:
        raise WebPushKeyError("invalid base64url value") from exc


def b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def decode_vapid_public_key(value: str) -> bytes:
    raw = b64url_decode(value)
    if len(raw) != VAPID_PUBLIC_KEY_BYTES or raw[0] != 0x04:
        raise WebPushKeyError("VAPID public key must be an uncompressed P-256 point")
    try:
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
    except ValueError as exc:
        raise WebPushKeyError("VAPID public key is not on the P-256 curve") from exc
    return raw


def decode_vapid_private_key(value: str) -> ec.EllipticCurvePrivateKey:
    raw = b64url_decode(value)
    if len(raw) != VAPID_PRIVATE_KEY_BYTES:
        raise WebPushKeyError("VAPID private key must be a 32-byte scalar")
    scalar = int.from_bytes(raw, "big")
    if scalar <= 0 or scalar >= int(ec.SECP256R1().group_order):
        raise WebPushKeyError("VAPID private key scalar is out of range")
    try:
        return ec.derive_private_key(scalar, ec.SECP256R1())
    except ValueError as exc:
        raise WebPushKeyError("VAPID private key is invalid") from exc


def private_scalar_bytes(private_key: ec.EllipticCurvePrivateKey) -> bytes:
    return private_key.private_numbers().private_value.to_bytes(VAPID_PRIVATE_KEY_BYTES, "big")


def public_key_bytes(private_key: ec.EllipticCurvePrivateKey) -> bytes:
    return private_key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )


def vapid_subject_valid(value: str) -> bool:
    """Accept ``mailto:`` or absolute ``https://`` contacts only."""

    subject = str(value or "").strip()
    if not subject or any(char.isspace() for char in subject):
        return False
    lowered = subject.lower()
    if lowered.startswith("mailto:"):
        address = subject[7:]
        return bool(address and "@" in address and not address.startswith("@"))
    if lowered.startswith("https://"):
        try:
            parsed = urlsplit(subject)
        except ValueError:
            return False
        return bool(parsed.hostname)
    return False


def vapid_keys_valid(public_key: str, private_key: str) -> bool:
    try:
        public_bytes = decode_vapid_public_key(public_key)
        private = decode_vapid_private_key(private_key)
    except WebPushKeyError:
        return False
    return public_key_bytes(private) == public_bytes
