"""VAPID signing and RFC 8291 ``aes128gcm`` payload encryption.

Only standard-library + ``cryptography`` primitives are used; the plugin adds
no runtime dependency. Private keys, shared secrets and derived content keys
never leave this module except as opaque HTTP header/payload bytes.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from collections.abc import Mapping
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from config.web_push_keys import (
    WebPushKeyError,
    b64url_decode,
    b64url_encode,
    decode_vapid_private_key,
    decode_vapid_public_key,
    public_key_bytes,
    vapid_keys_valid,
    vapid_subject_valid,
)

VAPID_JWT_LIFETIME_SECONDS = 12 * 60 * 60
PUSH_RECORD_SIZE = 4096
AES_GCM_NONCE_BYTES = 12
PUSH_SALT_BYTES = 16
AUTH_SECRET_BYTES = 16
SUBSCRIPTION_PUBLIC_KEY_BYTES = 65
_CONTENT_ENCODING = b"Content-Encoding: "
_PUSH_INFO_PREFIX = b"WebPush: info\x00"
_CEK_INFO = _CONTENT_ENCODING + b"aes128gcm\x00"
_NONCE_INFO = _CONTENT_ENCODING + b"nonce\x00"
_DELIMITER = b"\x02"

__all__ = [
    "AUTH_SECRET_BYTES",
    "PUSH_RECORD_SIZE",
    "VAPID_JWT_LIFETIME_SECONDS",
    "WebPushKeyError",
    "b64url_decode",
    "b64url_encode",
    "build_vapid_jwt",
    "decode_subscription_keys",
    "decode_vapid_private_key",
    "decode_vapid_public_key",
    "encrypt_push_payload",
    "load_vapid_private_key",
    "load_vapid_public_key",
    "public_key_bytes",
    "vapid_keys_valid",
    "vapid_subject_valid",
]


def load_vapid_public_key(value: str) -> bytes:
    """Return the raw uncompressed public point, validating the curve."""

    return decode_vapid_public_key(value)


def load_vapid_private_key(value: str | None) -> ec.EllipticCurvePrivateKey:
    if not value:
        raise WebPushKeyError("VAPID private key is not configured")
    return decode_vapid_private_key(value)


def _b64url_json(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return b64url_encode(payload)


def build_vapid_jwt(
    private_key: ec.EllipticCurvePrivateKey,
    public_key: bytes,
    subject: str,
    audience: str,
    now: int | None = None,
) -> str:
    """Build an ES256 VAPID JWT with a raw ``r || s`` signature."""

    if not vapid_subject_valid(subject):
        raise WebPushKeyError("invalid VAPID subject")
    if len(public_key) != 65 or public_key[0] != 0x04:
        raise WebPushKeyError("invalid VAPID public key")
    expected_public = public_key_bytes(private_key)
    if not hmac.compare_digest(expected_public, public_key):
        raise WebPushKeyError("VAPID public and private keys do not match")
    origin = str(audience or "").strip().rstrip("/")
    if not origin.startswith("https://"):
        raise WebPushKeyError("invalid VAPID audience")
    issued_at = int(time.time() if now is None else now)
    header = _b64url_json({"typ": "JWT", "alg": "ES256"})
    claims = _b64url_json(
        {
            "aud": origin,
            "exp": issued_at + VAPID_JWT_LIFETIME_SECONDS,
            "sub": str(subject).strip(),
        }
    )
    signing_input = f"{header}.{claims}".encode("ascii")
    der_signature = private_key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
    r_value, s_value = decode_dss_signature(der_signature)
    raw_signature = r_value.to_bytes(32, "big") + s_value.to_bytes(32, "big")
    return f"{header}.{claims}.{b64url_encode(raw_signature)}"


def decode_subscription_keys(subscription_keys: Mapping[str, Any]) -> tuple[bytes, bytes]:
    public_raw = b64url_decode(str(subscription_keys.get("p256dh") or ""))
    auth_secret = b64url_decode(str(subscription_keys.get("auth") or ""))
    if len(public_raw) != SUBSCRIPTION_PUBLIC_KEY_BYTES or public_raw[0] != 0x04:
        raise WebPushKeyError("subscription p256dh must be an uncompressed P-256 point")
    if len(auth_secret) != AUTH_SECRET_BYTES:
        raise WebPushKeyError("subscription auth secret must be 16 bytes")
    try:
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), public_raw)
    except ValueError as exc:
        raise WebPushKeyError("subscription p256dh is not on the P-256 curve") from exc
    return public_raw, auth_secret


def encrypt_push_payload(
    subscription_keys: Mapping[str, Any],
    plaintext: bytes | str,
    *,
    salt: bytes | None = None,
    server_private_key: ec.EllipticCurvePrivateKey | None = None,
) -> bytes:
    """Encrypt one push message as RFC 8291 ``aes128gcm`` body bytes.

    The optional salt/ephemeral key parameters exist for RFC 8291 test vectors;
    production callers use fresh random values for every message.
    """

    ua_public, auth_secret = decode_subscription_keys(subscription_keys)
    message = plaintext.encode("utf-8") if isinstance(plaintext, str) else bytes(plaintext)
    if len(message) > 3993:
        raise WebPushKeyError("push plaintext exceeds the 3993-byte aes128gcm limit")
    salt_value = os.urandom(PUSH_SALT_BYTES) if salt is None else bytes(salt)
    if len(salt_value) != PUSH_SALT_BYTES:
        raise WebPushKeyError("push salt must be 16 bytes")
    ephemeral = (
        server_private_key
        if server_private_key is not None
        else ec.generate_private_key(ec.SECP256R1())
    )
    as_public = public_key_bytes(ephemeral)

    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    ecdh_secret = ephemeral.exchange(ec.ECDH(), ua_key)
    key_info = _PUSH_INFO_PREFIX + ua_public + as_public
    ikm = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=auth_secret,
        info=key_info,
    ).derive(ecdh_secret)
    prk = hmac.new(salt_value, ikm, hashlib.sha256).digest()
    cek = hmac.new(prk, _CEK_INFO + b"\x01", hashlib.sha256).digest()[:16]
    nonce = hmac.new(prk, _NONCE_INFO + b"\x01", hashlib.sha256).digest()[:AES_GCM_NONCE_BYTES]
    ciphertext = AESGCM(cek).encrypt(nonce, message + _DELIMITER, None)

    record_size = PUSH_RECORD_SIZE.to_bytes(4, "big")
    return salt_value + record_size + bytes([len(as_public)]) + as_public + ciphertext
