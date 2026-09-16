"""Signed transport tests: exact bytes, redirects and allowlisted logging."""

import json
import threading
import time

import pytest
import requests

from device_enrollment.tests.fakes import FakeResponse, make_enrolled_identity
from device_enrollment.proof import decode_claims, decode_header
from device_enrollment.transport import (
    PreparedRequest,
    SignedTransport,
    prepare,
    send_prepared,
)

API = "https://api.example.test/api/v1"
AUD = "https://api.example.test/api/v1"
DEVICE_ID = "11111111-1111-1111-1111-111111111111"


class FakeSession:
    def __init__(self, response=None, exc=None):
        self.response = response
        self.exc = exc
        self.calls = []

    def request(
        self, method, url, data=None, headers=None, timeout=None, allow_redirects=None
    ):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "data": data,
                "headers": headers,
                "timeout": timeout,
                "allow_redirects": allow_redirects,
            }
        )
        if self.exc is not None:
            raise self.exc
        return self.response


def test_prepare_signs_exact_body_and_never_sets_api_key(tmp_path):
    identity = make_enrolled_identity(tmp_path)
    payload = {"dispositivoId": DEVICE_ID, "cpuPct": 12.5}

    prepared = prepare(identity, "POST", API + "/dispositivos/ping", payload, "sca-device+jwt", DEVICE_ID)

    assert prepared.target == "/api/v1/dispositivos/ping"
    assert prepared.body == json.dumps(payload, separators=(",", ":")).encode("utf-8")
    assert prepared.headers["Content-Type"] == "application/json"
    token = prepared.headers["Authorization"].split(" ", 1)[1]
    assert prepared.headers["Authorization"].startswith("DeviceProof ")
    assert "X-API-Key" not in prepared.headers
    claims = decode_claims(token)
    assert claims["htm"] == "POST"
    assert claims["rt"] == "/api/v1/dispositivos/ping"
    assert claims["sub"] == DEVICE_ID
    assert decode_header(token)["typ"] == "sca-device+jwt"


def test_prepare_rejects_query_strings(tmp_path):
    identity = make_enrolled_identity(tmp_path)
    with pytest.raises(Exception):
        prepare(
            identity,
            "POST",
            API + "/dispositivos/ping?x=1",
            {},
            "sca-device+jwt",
            DEVICE_ID,
        )


def test_prepared_unicode_body_is_sent_as_exact_utf8_bytes(tmp_path):
    identity = make_enrolled_identity(tmp_path)
    payload = {"turno": "mañana", "productoId": "ü"}
    prepared = prepare(identity, "POST", API + "/lotes", payload, "sca-device+jwt", DEVICE_ID)
    session = FakeSession(FakeResponse(status_code=201))

    assert send_prepared(prepared, session).ok
    sent = session.calls[0]["data"]
    assert sent == json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    assert "ma\u00f1ana".encode("utf-8") in sent
    # The signed bhash binds those exact bytes.
    token = prepared.headers["Authorization"].split(" ", 1)[1]
    from device_enrollment.proof import body_hash

    assert decode_claims(token)["bhash"] == body_hash(sent)


def test_send_prepared_sends_exact_bytes_and_does_not_follow_redirects(tmp_path):
    identity = make_enrolled_identity(tmp_path)
    prepared = prepare(
        identity, "POST", API + "/lotes", {"totalUnidades": 1}, "sca-device+jwt", DEVICE_ID
    )
    session = FakeSession(FakeResponse(status_code=200))

    result = send_prepared(prepared, session, timeout=5.0)

    assert result.ok
    call = session.calls[0]
    assert call["data"] == prepared.body
    assert call["allow_redirects"] is False
    assert call["headers"] == prepared.headers
    assert call["method"] == "POST"


def test_send_prepared_honors_prepared_method(tmp_path):
    """The transport must send the prepared method, never a hardcoded POST."""
    identity = make_enrolled_identity(tmp_path)
    prepared = prepare(
        identity, "POST", API + "/lotes", {}, "sca-device+jwt", DEVICE_ID
    )
    # Same signed bytes, but a deliberately different method: it must be the one
    # handed to the session (regression guard for hardcoded session.post()).
    non_post = PreparedRequest(
        method="DELETE",
        url=prepared.url,
        target=prepared.target,
        body=prepared.body,
        headers=prepared.headers,
    )
    session = FakeSession(FakeResponse(status_code=200))

    assert send_prepared(non_post, session).ok
    assert session.calls[0]["method"] == "DELETE"


def test_shared_session_is_serialised_across_threads(tmp_path):
    """Telemetry + lote senders share one transport; sends must not overlap."""
    identity = make_enrolled_identity(tmp_path)

    class ConcurrencySession:
        def __init__(self):
            self._guard = threading.Lock()
            self.active = 0
            self.max_active = 0
            self.calls = 0

        def request(self, method, url, data=None, headers=None, timeout=None, allow_redirects=None):
            with self._guard:
                self.active += 1
                self.calls += 1
                self.max_active = max(self.max_active, self.active)
            time.sleep(0.02)
            with self._guard:
                self.active -= 1
            return FakeResponse(status_code=200, text="{}")

    session = ConcurrencySession()
    transport = SignedTransport(lambda: identity, API, AUD, session=session)

    results = []
    errors = []

    def worker(n):
        try:
            results.append(transport.post("/dispositivos/ping", {"dispositivoId": DEVICE_ID, "n": n}))
        except Exception as exc:  # pragma: no cover - defensive
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert all(results)
    assert session.calls == 8
    assert session.max_active == 1


def test_redirect_response_is_reported_as_failure(tmp_path):
    identity = make_enrolled_identity(tmp_path)
    prepared = prepare(identity, "POST", API + "/lotes", {}, "sca-device+jwt", DEVICE_ID)
    session = FakeSession(FakeResponse(status_code=307, is_redirect=True))

    result = send_prepared(prepared, session)

    assert result.is_redirect
    assert not result.ok


def test_transport_without_identity_fails_closed(tmp_path):
    transport = SignedTransport(lambda: None, API, AUD, session=FakeSession(FakeResponse()))
    assert transport.post("/dispositivos/ping", {"dispositivoId": DEVICE_ID}) is False
    assert transport.last_error == "device_not_enrolled"


def test_transport_success_and_error_code(tmp_path):
    identity = make_enrolled_identity(tmp_path)

    ok_transport = SignedTransport(
        lambda: identity, API, AUD, session=FakeSession(FakeResponse(200, "{}"))
    )
    assert ok_transport.post("/lotes/inicio", {"hornoId": "h", "productoId": "p"}) is True

    error_body = json.dumps(
        {"success": False, "message": "no", "errors": {"code": "device_identity_mismatch"}}
    )
    err_transport = SignedTransport(
        lambda: identity, API, AUD, session=FakeSession(FakeResponse(403, error_body))
    )
    assert err_transport.post("/dispositivos/ping", {"dispositivoId": DEVICE_ID}) is False
    assert err_transport.last_error == "device_identity_mismatch"


def test_transport_redirect_is_not_followed(tmp_path):
    identity = make_enrolled_identity(tmp_path)
    transport = SignedTransport(
        lambda: identity, API, AUD, session=FakeSession(FakeResponse(302, is_redirect=True))
    )
    assert transport.post("/lotes", {}) is False
    assert transport.last_error == "unexpected_redirect"


def test_transport_timeout_is_ambiguous_and_safe(tmp_path):
    identity = make_enrolled_identity(tmp_path)
    transport = SignedTransport(
        lambda: identity,
        API,
        AUD,
        session=FakeSession(exc=requests.Timeout("secret url http://user:pw@host?token=x")),
    )
    assert transport.post("/lotes", {}) is False
    assert transport.last_error == "Timeout"


def test_logging_is_allowlisted_and_never_contains_secrets(tmp_path, caplog):
    identity = make_enrolled_identity(tmp_path)
    prepared = prepare(
        identity,
        "POST",
        API + "/dispositivos/provision",
        {"code": "SECRET_INVITATION_CODE", "publicKey": identity.public_jwk},
        "sca-enrollment+jwt",
        "urn:sca:enrollment-key:" + identity.fingerprint,
    )
    token = prepared.headers["Authorization"].split(" ", 1)[1]
    session = FakeSession(FakeResponse(200, "SECRET_RESPONSE_BODY"))

    with caplog.at_level("INFO", logger="device_enrollment.transport"):
        send_prepared(prepared, session)

    assert "SECRET_INVITATION_CODE" not in caplog.text
    assert "SECRET_RESPONSE_BODY" not in caplog.text
    assert token not in caplog.text
    assert "path=/api/v1/dispositivos/provision" in caplog.text
    assert "Authorization" not in caplog.text
