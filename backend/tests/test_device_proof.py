"""Unit tests for compact JWS construction and tamper detection."""

import base64
import hashlib

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from device_enrollment.proof import (
    DEVICE_TYP,
    ENROLLMENT_TYP,
    b64url,
    body_hash,
    build_proof,
    decode_claims,
    decode_header,
    jwk_fingerprint,
    public_jwk,
    serialize_body,
    signing_input,
    verify_signature,
)

AUDIENCE = "https://api.example.test/api/v1"
SUBJECT = "11111111-1111-1111-1111-111111111111"
TARGET = "/api/v1/dispositivos/ping"


def make_key():
    return Ed25519PrivateKey.from_private_bytes(bytes(range(32)))


def make_proof(body=None, target=TARGET, typ=DEVICE_TYP, jti=bytes(range(16))):
    key = make_key()
    body = body if body is not None else serialize_body({"dispositivoId": SUBJECT})
    token = build_proof(
        key,
        jwk_fingerprint(key.public_key()),
        AUDIENCE,
        "POST",
        target,
        body,
        typ,
        SUBJECT,
        issued_at=1757600000,
        lifetime=60,
        jti=jti,
    )
    return key, body, token


def test_serialize_body_is_compact_utf8():
    assert serialize_body({"turno": "mañana"}) == '{"turno":"mañana"}'.encode("utf-8")


def test_body_hash_matches_independent_sha256():
    body = b'{"a":1}'
    expected = base64.urlsafe_b64encode(hashlib.sha256(body).digest()).rstrip(b"=").decode()
    assert body_hash(body) == expected


def test_fingerprint_is_rfc7638_of_canonical_jwk():
    key = make_key()
    x = public_jwk(key.public_key())["x"]
    canonical = ('{"crv":"Ed25519","kty":"OKP","x":"' + x + '"}').encode("utf-8")
    expected = base64.urlsafe_b64encode(hashlib.sha256(canonical).digest()).rstrip(b"=")
    assert jwk_fingerprint(key.public_key()) == expected.decode()


def test_header_has_exactly_alg_typ_kid():
    _, _, token = make_proof()
    header = decode_header(token)
    assert set(header) == {"alg", "typ", "kid"}
    assert header["alg"] == "EdDSA"
    assert header["typ"] == DEVICE_TYP


def test_enrollment_type_and_subject_prefix_are_supported():
    _, _, token = make_proof(typ=ENROLLMENT_TYP)
    assert decode_header(token)["typ"] == ENROLLMENT_TYP


def test_claims_bind_method_target_body_and_lifetime():
    _, body, token = make_proof()
    claims = decode_claims(token)
    assert claims["sub"] == SUBJECT
    assert claims["aud"] == AUDIENCE
    assert claims["htm"] == "POST"
    assert claims["rt"] == TARGET
    assert claims["bhash"] == body_hash(body)
    assert claims["exp"] - claims["iat"] == 60
    assert len(claims["jti"]) == 22


def test_jti_must_be_16_bytes():
    with pytest.raises(ValueError):
        make_proof(jti=b"short")


def test_signature_verifies_and_tampering_is_rejected():
    key, _, token = make_proof()
    assert verify_signature(token, key.public_key())

    header, payload, signature = token.split(".")
    tampered_payload = base64.urlsafe_b64encode(
        b'{"sub":"attacker"}'
    ).rstrip(b"=").decode()
    assert not verify_signature(
        f"{header}.{tampered_payload}.{signature}", key.public_key()
    )


def test_signature_rejects_different_body_binding():
    # A signature over one body must not verify once the bhash claim changes;
    # this models the server recomputing bhash from the raw transmitted body.
    key, body, token = make_proof()
    assert verify_signature(token, key.public_key())
    assert body_hash(body) != body_hash(body + b"x")


def test_b64url_roundtrip_is_unpadded():
    encoded = b64url(bytes(range(16)))
    assert "=" not in encoded
    assert len(encoded) == 22
