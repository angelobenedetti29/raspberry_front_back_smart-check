"""Compact JWS (EdDSA) construction bound to the exact HTTP request bytes."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

DEVICE_TYP = "sca-device+jwt"
ENROLLMENT_TYP = "sca-enrollment+jwt"
ENROLLMENT_SUBJECT_PREFIX = "urn:sca:enrollment-key:"
PROOF_LIFETIME_SECONDS = 60
JTI_BYTES = 16


def b64url(data: bytes) -> str:
    """RFC 4648 base64url without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    """Decode unpadded base64url, tolerating missing padding."""
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def public_jwk(public_key: Ed25519PublicKey) -> dict[str, str]:
    """Return the exact public JWK accepted by the Go verifier."""
    raw = public_key.public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return {"kty": "OKP", "crv": "Ed25519", "x": b64url(raw)}


def canonical_jwk(public_key: Ed25519PublicKey) -> str:
    """Exact canonical JSON whose SHA-256 is the RFC7638 fingerprint."""
    jwk = public_jwk(public_key)
    return '{"crv":"Ed25519","kty":"OKP","x":"' + jwk["x"] + '"}'


def jwk_fingerprint(public_key: Ed25519PublicKey) -> str:
    """RFC7638 SHA-256 thumbprint, base64url unpadded."""
    digest = hashlib.sha256(canonical_jwk(public_key).encode("utf-8")).digest()
    return b64url(digest)


def body_hash(body: bytes) -> str:
    """SHA-256 of the exact transmitted body, base64url unpadded."""
    return b64url(hashlib.sha256(body).digest())


def serialize_body(payload: dict[str, Any]) -> bytes:
    """Serialize a payload once, deterministically, as UTF-8 JSON."""
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def build_proof(
    private_key: Ed25519PrivateKey,
    fingerprint: str,
    audience: str,
    method: str,
    target: str,
    body: bytes,
    typ: str,
    subject: str,
    *,
    issued_at: int | None = None,
    lifetime: int = PROOF_LIFETIME_SECONDS,
    jti: bytes | None = None,
) -> str:
    """Build a compact JWS with the exact protected header and claim set.

    ``target`` must be the exact origin-form request target (including any
    escapes) and ``body`` the exact bytes that will be transmitted.
    """
    issued = int(time.time()) if issued_at is None else int(issued_at)
    if jti is None:
        jti = os.urandom(JTI_BYTES)
    if len(jti) != JTI_BYTES:
        raise ValueError("jti must be exactly 16 random bytes")

    claims: dict[str, Any] = {
        "sub": subject,
        "aud": audience,
        "iat": issued,
        "exp": issued + int(lifetime),
        "jti": b64url(jti),
        "htm": method,
        "rt": target,
        "bhash": body_hash(body),
    }
    headers = {"kid": fingerprint, "typ": typ}
    token = jwt.encode(claims, private_key, algorithm="EdDSA", headers=headers)
    if isinstance(token, bytes):
        token = token.decode("ascii")
    return token


def signing_input(token: str) -> bytes:
    """Return the ``header.payload`` bytes that Ed25519 signs."""
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("not a compact JWS")
    return (parts[0] + "." + parts[1]).encode("ascii")


def decode_header(token: str) -> dict[str, Any]:
    """Decode and return the protected header (unverified)."""
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("not a compact JWS")
    return json.loads(b64url_decode(parts[0]).decode("utf-8"))


def decode_claims(token: str) -> dict[str, Any]:
    """Decode and return the claim set (unverified)."""
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("not a compact JWS")
    return json.loads(b64url_decode(parts[1]).decode("utf-8"))


def verify_signature(token: str, public_key: Ed25519PublicKey) -> bool:
    """Verify the Ed25519 signature over the exact signing input."""
    parts = token.split(".")
    if len(parts) != 3:
        return False
    try:
        signature = b64url_decode(parts[2])
        public_key.verify(signature, signing_input(token))
    except Exception:
        return False
    return True
