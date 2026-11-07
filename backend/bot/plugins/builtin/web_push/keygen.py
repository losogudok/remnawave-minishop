"""Generate a VAPID key pair for the built-in Web Push plugin.

Usage::

    python -m bot.plugins.builtin.web_push.keygen

The generated private key is a deployment secret: put it in ``.env`` or the
admin settings, never commit it or paste it into logs/screenshots.
"""

from __future__ import annotations

from cryptography.hazmat.primitives.asymmetric import ec

from config.web_push_keys import b64url_encode, private_scalar_bytes, public_key_bytes


def generate_vapid_keys() -> tuple[str, str]:
    private = ec.generate_private_key(ec.SECP256R1())
    return (
        b64url_encode(public_key_bytes(private)),
        b64url_encode(private_scalar_bytes(private)),
    )


def main() -> int:
    public_key, private_key = generate_vapid_keys()
    print("# Web Push VAPID keys (keep the private key secret)")
    print(f"WEB_PUSH_VAPID_PUBLIC_KEY={public_key}")
    print(f"WEB_PUSH_VAPID_PRIVATE_KEY={private_key}")
    print("WEB_PUSH_VAPID_SUBJECT=mailto:admin@example.com")
    print("WEB_PUSH_ENABLED=True")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
