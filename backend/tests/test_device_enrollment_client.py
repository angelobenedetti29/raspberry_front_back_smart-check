"""Pending/recovery ordering, lost-response safety and revocation handling."""

import json

import pytest
import requests

from device_enrollment.client import EnrollmentClient
from device_enrollment.errors import (
    CredentialRevokedError,
    IdentityCorruptError,
    NewInvitationRequiredError,
    NotEnrolledError,
)
from device_enrollment.identity import IdentityStore, PHASE_ENROLLED, PHASE_PENDING
from device_enrollment.proof import ENROLLMENT_SUBJECT_PREFIX, decode_header

API = "https://api.example.test/api/v1"
AUD = "https://api.example.test/api/v1"
RECOVER = API + "/dispositivos/enrollments/recover"
PROVISION = API + "/dispositivos/provision"
DEVICE_ID = "22222222-2222-2222-2222-222222222222"


class FakeResponse:
    def __init__(self, status_code, text="{}", is_redirect=False):
        self.status_code = status_code
        self.text = text
        self.is_redirect = is_redirect


class ScriptedSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(
        self, method, url, data=None, headers=None, timeout=None, allow_redirects=None
    ):
        self.calls.append({"method": method, "url": url, "data": data, "headers": headers})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        status, body = item
        return FakeResponse(status, body)


def error_body(code):
    return json.dumps({"success": False, "message": "error", "errors": {"code": code}})


def identity_body(fingerprint, dispositivo_id=DEVICE_ID, status="active"):
    return json.dumps(
        {
            "success": True,
            "message": "ok",
            "data": {
                "enrollmentId": "enrollment-1",
                "dispositivoId": dispositivo_id,
                "keyFingerprint": fingerprint,
                "authStatus": status,
                "enrolledAt": "2026-09-12T00:00:00Z",
                "audience": AUD,
            },
        }
    )


def make_store(tmp_path):
    store = IdentityStore(tmp_path / "identity")
    store.ensure_directory()
    return store


def prepare_pending(store):
    return store.initialize_pending(API, AUD)


def test_new_key_recover_404_then_provision_succeeds(tmp_path):
    store = make_store(tmp_path)
    pending = prepare_pending(store)
    session = ScriptedSession([(404, error_body("enrollment_not_found")), (201, identity_body(pending.fingerprint))])
    client = EnrollmentClient(store, API, AUD, session=session)

    outcome = client.enroll(code="CODE123")

    assert outcome.status == "enrolled"
    assert outcome.identity.phase == PHASE_ENROLLED
    assert outcome.identity.dispositivo_id == DEVICE_ID
    assert outcome.identity.fingerprint == pending.fingerprint
    assert session.calls[0]["url"] == RECOVER
    assert session.calls[1]["url"] == PROVISION
    # Each enrollment request is signed with the enrollment JWS type and subject.
    token = session.calls[0]["headers"]["Authorization"].split(" ", 1)[1]
    header = decode_header(token)
    assert header["typ"] == "sca-enrollment+jwt"
    assert header["kid"] == pending.fingerprint
    # Key was never rotated.
    assert store.load_identity().fingerprint == pending.fingerprint


def test_pending_recover_200_persists_without_code(tmp_path):
    store = make_store(tmp_path)
    pending = prepare_pending(store)
    session = ScriptedSession([(200, identity_body(pending.fingerprint, status="disabled"))])
    client = EnrollmentClient(store, API, AUD, session=session)

    outcome = client.enroll()

    assert outcome.status == "enrolled"
    assert outcome.device["authStatus"] == "disabled"
    assert store.load_metadata()["phase"] == PHASE_ENROLLED
    assert len(session.calls) == 1


def test_lost_response_after_redeem_recovers_same_key_never_rotates(tmp_path):
    store = make_store(tmp_path)
    pending = prepare_pending(store)
    session = ScriptedSession(
        [
            (404, error_body("enrollment_not_found")),
            requests.Timeout("connection lost"),
            (200, identity_body(pending.fingerprint)),
        ]
    )
    client = EnrollmentClient(store, API, AUD, session=session)

    outcome = client.enroll(code="CODE123")

    assert outcome.status == "enrolled"
    assert outcome.identity.fingerprint == pending.fingerprint
    assert [call["url"] for call in session.calls] == [RECOVER, PROVISION, RECOVER]


def test_ambiguous_provision_failure_retries_via_recover(tmp_path):
    store = make_store(tmp_path)
    pending = prepare_pending(store)
    session = ScriptedSession(
        [
            (404, error_body("enrollment_not_found")),
            (503, "service unavailable"),
            (200, identity_body(pending.fingerprint)),
        ]
    )
    client = EnrollmentClient(store, API, AUD, session=session)

    outcome = client.enroll(code="CODE123")

    assert outcome.status == "enrolled"
    assert session.calls[-1]["url"] == RECOVER


def test_credential_used_then_recover_returns_identity(tmp_path):
    store = make_store(tmp_path)
    pending = prepare_pending(store)
    session = ScriptedSession(
        [
            (404, error_body("enrollment_not_found")),
            (409, error_body("credential_used")),
            (200, identity_body(pending.fingerprint)),
        ]
    )
    client = EnrollmentClient(store, API, AUD, session=session)

    assert client.enroll(code="CODE123").status == "enrolled"


def test_revoked_credential_is_a_hard_stop_and_keeps_local_key(tmp_path):
    store = make_store(tmp_path)
    pending = prepare_pending(store)
    session = ScriptedSession([(409, error_body("credential_revoked"))])
    client = EnrollmentClient(store, API, AUD, session=session)

    with pytest.raises(CredentialRevokedError):
        client.enroll(code="CODE123")

    # The key/pending identity is preserved for a deliberate reset, not rotated.
    assert store.load_metadata()["phase"] == PHASE_PENDING
    assert store.load_identity().fingerprint == pending.fingerprint


def test_enrollment_unavailable_requires_a_new_invitation(tmp_path):
    store = make_store(tmp_path)
    prepare_pending(store)
    session = ScriptedSession(
        [
            (404, error_body("enrollment_not_found")),
            (400, error_body("enrollment_unavailable")),
            (404, error_body("enrollment_not_found")),
        ]
    )
    client = EnrollmentClient(store, API, AUD, session=session)

    with pytest.raises(NewInvitationRequiredError):
        client.enroll(code="CODE123")


def test_already_enrolled_is_idempotent_without_network(tmp_path):
    store = make_store(tmp_path)
    pending = prepare_pending(store)
    first = EnrollmentClient(
        store, API, AUD, session=ScriptedSession([(404, error_body("enrollment_not_found")), (201, identity_body(pending.fingerprint))])
    )
    first.enroll(code="CODE123")

    second = EnrollmentClient(store, API, AUD, session=ScriptedSession([]))
    outcome = second.enroll(code="CODE123")

    assert outcome.status == "already_enrolled"
    assert second.session.calls == []


def test_missing_code_after_404_requires_code(tmp_path):
    from device_enrollment.errors import CodeRequiredError

    store = make_store(tmp_path)
    prepare_pending(store)
    session = ScriptedSession([(404, error_body("enrollment_not_found"))])
    client = EnrollmentClient(store, API, AUD, session=session)

    with pytest.raises(CodeRequiredError):
        client.enroll()


def test_recover_command_404_reports_not_enrolled(tmp_path):
    store = make_store(tmp_path)
    prepare_pending(store)
    session = ScriptedSession([(404, error_body("enrollment_not_found"))])
    client = EnrollmentClient(store, API, AUD, session=session)

    with pytest.raises(NotEnrolledError):
        client.recover()


def test_recover_command_refreshes_enrolled_identity(tmp_path):
    store = make_store(tmp_path)
    pending = prepare_pending(store)
    enrolled = store.persist_enrolled(
        pending,
        {
            "enrollmentId": "enrollment-1",
            "dispositivoId": DEVICE_ID,
            "keyFingerprint": pending.fingerprint,
            "authStatus": "active",
            "enrolledAt": "2026-09-12T00:00:00Z",
            "audience": AUD,
        },
    )
    session = ScriptedSession([(200, identity_body(enrolled.fingerprint, status="disabled"))])
    client = EnrollmentClient(store, API, AUD, session=session)

    outcome = client.recover()

    assert outcome.device["authStatus"] == "disabled"
    assert store.load_metadata()["phase"] == PHASE_ENROLLED


def test_recover_records_the_current_api_base_url_not_the_stale_one(tmp_path):
    """A reconfigured central server must replace the stored apiBaseUrl."""
    store = make_store(tmp_path)
    old_api = "https://old.example.test/api/v1"
    new_api = "https://new.example.test/api/v1"
    pending = store.initialize_pending(old_api, AUD)
    store.persist_enrolled(
        pending,
        {
            "enrollmentId": "enrollment-1",
            "dispositivoId": DEVICE_ID,
            "keyFingerprint": pending.fingerprint,
            "authStatus": "active",
            "enrolledAt": "2026-09-12T00:00:00Z",
            "audience": AUD,
        },
    )
    assert store.load_metadata()["apiBaseUrl"] == old_api

    session = ScriptedSession([(200, identity_body(pending.fingerprint))])
    client = EnrollmentClient(store, new_api, AUD, session=session)
    outcome = client.recover()

    assert session.calls[0]["method"] == "POST"
    assert session.calls[0]["url"] == new_api + "/dispositivos/enrollments/recover"
    assert outcome.identity.api_base_url == new_api
    assert store.load_metadata()["apiBaseUrl"] == new_api


def test_corrupt_enrolled_key_is_a_hard_failure(tmp_path):
    store = make_store(tmp_path)
    pending = prepare_pending(store)
    store.persist_enrolled(
        pending,
        {
            "enrollmentId": "enrollment-1",
            "dispositivoId": DEVICE_ID,
            "keyFingerprint": pending.fingerprint,
            "authStatus": "active",
            "enrolledAt": "2026-09-12T00:00:00Z",
            "audience": AUD,
        },
    )
    store.private_key_path.unlink()
    client = EnrollmentClient(store, API, AUD, session=ScriptedSession([]))

    with pytest.raises(IdentityCorruptError):
        client.enroll(code="CODE123")


def test_enrollment_subject_uses_urn_prefix(tmp_path):
    store = make_store(tmp_path)
    pending = prepare_pending(store)
    session = ScriptedSession([(200, identity_body(pending.fingerprint))])
    client = EnrollmentClient(store, API, AUD, session=session)
    client.enroll()

    from device_enrollment.proof import decode_claims

    token = session.calls[0]["headers"]["Authorization"].split(" ", 1)[1]
    assert decode_claims(token)["sub"] == ENROLLMENT_SUBJECT_PREFIX + pending.fingerprint
