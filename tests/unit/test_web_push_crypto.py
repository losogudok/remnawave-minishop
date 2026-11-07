"""RFC 8291 encryption and VAPID JWT tests for the built-in web_push plugin."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from bot.plugins.builtin.web_push.crypto import (
    VAPID_JWT_LIFETIME_SECONDS,
    WebPushKeyError,
    b64url_decode,
    b64url_encode,
    build_vapid_jwt,
    decode_subscription_keys,
    decode_vapid_private_key,
    decode_vapid_public_key,
    encrypt_push_payload,
    load_vapid_private_key,
    public_key_bytes,
)

RFC_P256DH = (
    "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
)
RFC_AUTH = "BTBZMqHH6r4Tts7J_aSIgg"
RFC_SALT = "DGv6ra1nlYgDCS1FRnbzlw"
RFC_SERVER_PRIVATE = "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"
RFC_BODY = (
    "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27ml"
    "mlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPT"
    "pK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN"
)
RFC_PLAINTEXT = "When I grow up, I want to be a watermelon"


def _decrypt_rfc8291(
    body: bytes,
    *,
    ua_private: str,
    auth_secret: str,
) -> bytes:
    salt = body[:16]
    record_size = int.from_bytes(body[16:20], "big")
    assert record_size == 4096
    id_length = body[20]
    server_public = body[21 : 21 + id_length]
    ciphertext = body[21 + id_length :]
    private = decode_vapid_private_key(ua_private)
    server_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), server_public)
    shared = private.exchange(ec.ECDH(), server_key)
    key_info = b"WebPush: info\x00" + public_key_bytes(private) + server_public
    ikm = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b64url_decode(auth_secret),
        info=key_info,
    ).derive(shared)
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    cek = hmac.new(prk, b"Content-Encoding: aes128gcm\x00\x01", hashlib.sha256).digest()[:16]
    nonce = hmac.new(prk, b"Content-Encoding: nonce\x00\x01", hashlib.sha256).digest()[:12]
    return AESGCM(cek).decrypt(nonce, ciphertext, None)


def test_rfc8291_section_5_vector_matches_exactly() -> None:
    body = encrypt_push_payload(
        {"p256dh": RFC_P256DH, "auth": RFC_AUTH},
        RFC_PLAINTEXT,
        salt=b64url_decode(RFC_SALT),
        server_private_key=load_vapid_private_key(RFC_SERVER_PRIVATE),
    )
    assert b64url_encode(body) == RFC_BODY


def test_rfc8291_round_trip_decrypts_plaintext_with_delimiter() -> None:
    body = encrypt_push_payload(
        {"p256dh": RFC_P256DH, "auth": RFC_AUTH},
        RFC_PLAINTEXT,
        salt=b64url_decode(RFC_SALT),
        server_private_key=load_vapid_private_key(RFC_SERVER_PRIVATE),
    )
    decrypted = _decrypt_rfc8291(
        body,
        ua_private="q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94",
        auth_secret=RFC_AUTH,
    )
    assert decrypted == RFC_PLAINTEXT.encode() + b"\x02"


def test_vapid_jwt_structure_and_es256_signature() -> None:
    private = ec.generate_private_key(ec.SECP256R1())
    public = public_key_bytes(private)
    now = 1_780_000_000
    token = build_vapid_jwt(
        private,
        public,
        "mailto:ops@example.com",
        "https://fcm.googleapis.com",
        now=now,
    )
    header_part, claims_part, signature_part = token.split(".")
    header = json.loads(b64url_decode(header_part))
    claims = json.loads(b64url_decode(claims_part))
    assert header == {"alg": "ES256", "typ": "JWT"}
    assert claims == {
        "aud": "https://fcm.googleapis.com",
        "exp": now + VAPID_JWT_LIFETIME_SECONDS,
        "sub": "mailto:ops@example.com",
    }
    signature = b64url_decode(signature_part)
    assert len(signature) == 64
    r_value = int.from_bytes(signature[:32], "big")
    s_value = int.from_bytes(signature[32:], "big")
    private.public_key().verify(
        encode_dss_signature(r_value, s_value),
        f"{header_part}.{claims_part}".encode(),
        ec.ECDSA(hashes.SHA256()),
    )


def test_vapid_key_parsing_rejects_rotation_and_bad_subjects() -> None:
    first = ec.generate_private_key(ec.SECP256R1())
    second = ec.generate_private_key(ec.SECP256R1())
    private_raw = b64url_encode(first.private_numbers().private_value.to_bytes(32, "big"))
    parsed = load_vapid_private_key(private_raw)
    assert public_key_bytes(parsed) == public_key_bytes(first)
    with pytest.raises(WebPushKeyError):
        build_vapid_jwt(
            first,
            public_key_bytes(second),
            "mailto:ops@example.com",
            "https://fcm.googleapis.com",
        )
    with pytest.raises(WebPushKeyError):
        build_vapid_jwt(first, public_key_bytes(first), "ops@example.com", "https://example.com")
    with pytest.raises(WebPushKeyError):
        build_vapid_jwt(first, public_key_bytes(first), "mailto:ops@example.com", "http://x")


def test_key_decoding_rejects_wrong_lengths_and_non_curve_points() -> None:
    private = ec.generate_private_key(ec.SECP256R1())
    public = public_key_bytes(private)
    with pytest.raises(WebPushKeyError):
        decode_vapid_public_key(b64url_encode(public[:64]))
    with pytest.raises(WebPushKeyError):
        decode_vapid_private_key("AAAA")
    with pytest.raises(WebPushKeyError):
        decode_subscription_keys(
            {"p256dh": b64url_encode(b"\x04" + b"\x01" * 64), "auth": RFC_AUTH}
        )
    with pytest.raises(WebPushKeyError):
        decode_subscription_keys({"p256dh": RFC_P256DH, "auth": b64url_encode(b"x" * 15)})


def test_encrypt_push_payload_generates_fresh_salt_and_ephemeral_key() -> None:
    subscription = {"p256dh": RFC_P256DH, "auth": RFC_AUTH}
    first = encrypt_push_payload(subscription, "hello")
    second = encrypt_push_payload(subscription, "hello")
    assert first[:16] != second[:16]
    assert first[21:86] != second[21:86]
    assert len(base64.urlsafe_b64decode(b64url_encode(first) + "==")) >= 86
    with pytest.raises(WebPushKeyError):
        encrypt_push_payload(subscription, b"x" * 4000)


def test_vapid_jwt_does_not_embed_private_material() -> None:
    private = ec.generate_private_key(ec.SECP256R1())
    token = build_vapid_jwt(
        private,
        public_key_bytes(private),
        "https://example.com/contact",
        "https://web.push.apple.com",
        now=int(datetime.now(UTC).timestamp()),
    )
    private_raw = private.private_numbers().private_value.to_bytes(32, "big")
    assert b64url_encode(private_raw) not in token
