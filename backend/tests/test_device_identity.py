"""Identity persistence, permissions, atomicity and restart-reuse tests."""

import json
import os
import stat

import pytest

from device_enrollment.errors import IdentityCorruptError, IdentityError
from device_enrollment.identity import (
    PHASE_ENROLLED,
    PHASE_PENDING,
    IdentityStore,
)

API = "https://api.example.test/api/v1"
AUD = "https://api.example.test/api/v1"


def make_store(tmp_path) -> IdentityStore:
    store = IdentityStore(tmp_path / "identity")
    store.ensure_directory()
    return store


def device_descriptor(store: IdentityStore, dispositivo_id="22222222-2222-2222-2222-222222222222"):
    return {
        "enrollmentId": "abcdefghijklmnop",
        "dispositivoId": dispositivo_id,
        "keyFingerprint": None,  # filled by caller when needed
        "authStatus": "active",
        "enrolledAt": "2026-09-12T00:00:00Z",
        "audience": AUD,
    }


def test_initialize_pending_persists_key_and_metadata_with_permissions(tmp_path):
    store = make_store(tmp_path)
    identity = store.initialize_pending(API, AUD)

    assert identity.phase == PHASE_PENDING
    assert identity.dispositivo_id is None
    assert store.private_key_path.exists()
    assert store.identity_path.exists()
    assert stat.S_IMODE(os.stat(store.identity_dir).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(store.private_key_path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(store.identity_path).st_mode) == 0o600

    metadata = json.loads(store.identity_path.read_text(encoding="utf-8"))
    assert metadata["phase"] == PHASE_PENDING
    assert metadata["keyFingerprint"] == identity.fingerprint
    assert metadata["apiBaseUrl"] == API


def test_restart_reuses_the_same_pending_key(tmp_path):
    store = make_store(tmp_path)
    first = store.initialize_pending(API, AUD)

    reopened = IdentityStore(tmp_path / "identity")
    second = reopened.load_identity()

    assert second.fingerprint == first.fingerprint
    assert reopened.read_private_key_pem() == store.read_private_key_pem()


def test_crash_between_key_and_metadata_reuses_orphaned_key(tmp_path):
    store = make_store(tmp_path)
    orphan_key = store.generate_private_key()
    store.write_private_key(orphan_key)
    # No metadata yet (simulated crash). initialize_pending must reuse the key.
    recovered = store.initialize_pending(API, AUD)

    assert store.identity_path.exists()
    assert recovered.fingerprint == store.load_identity().fingerprint
    assert recovered.private_key.private_bytes_raw() == orphan_key.private_bytes_raw()


def test_persist_enrolled_marks_phase_and_keeps_key(tmp_path):
    store = make_store(tmp_path)
    pending = store.initialize_pending(API, AUD)
    descriptor = device_descriptor(store)
    descriptor["keyFingerprint"] = pending.fingerprint

    enrolled = store.persist_enrolled(pending, descriptor)

    assert enrolled.phase == PHASE_ENROLLED
    assert enrolled.dispositivo_id == descriptor["dispositivoId"]
    assert enrolled.enrollment_id == descriptor["enrollmentId"]
    assert enrolled.fingerprint == pending.fingerprint
    assert store.load_identity().phase == PHASE_ENROLLED


def test_persist_enrolled_records_the_current_api_base_url(tmp_path):
    store = make_store(tmp_path)
    old_api = "https://old.example.test/api/v1"
    new_api = "https://new.example.test/api/v1"
    pending = store.initialize_pending(old_api, AUD)
    descriptor = device_descriptor(store)
    descriptor["keyFingerprint"] = pending.fingerprint

    enrolled = store.persist_enrolled(pending, descriptor, api_base_url=new_api)

    assert enrolled.api_base_url == new_api
    assert store.load_metadata()["apiBaseUrl"] == new_api
    # Without an override the identity's own value is kept.
    assert store.persist_enrolled(enrolled, descriptor).api_base_url == new_api


def test_persist_enrolled_rejects_mismatched_fingerprint(tmp_path):
    store = make_store(tmp_path)
    pending = store.initialize_pending(API, AUD)
    descriptor = device_descriptor(store)
    descriptor["keyFingerprint"] = "not-the-key"

    with pytest.raises(IdentityCorruptError):
        store.persist_enrolled(pending, descriptor)


def test_enrolled_identity_with_missing_key_is_hard_failure(tmp_path):
    store = make_store(tmp_path)
    pending = store.initialize_pending(API, AUD)
    descriptor = device_descriptor(store)
    descriptor["keyFingerprint"] = pending.fingerprint
    store.persist_enrolled(pending, descriptor)

    store.private_key_path.unlink()
    with pytest.raises(IdentityCorruptError):
        store.load_identity()


def test_fingerprint_mismatch_is_a_hard_failure(tmp_path):
    store = make_store(tmp_path)
    store.initialize_pending(API, AUD)
    metadata = json.loads(store.identity_path.read_text(encoding="utf-8"))
    metadata["keyFingerprint"] = "tampered"
    store.identity_path.write_text(json.dumps(metadata), encoding="utf-8")
    os.chmod(store.identity_path, 0o600)

    with pytest.raises(IdentityCorruptError):
        store.load_identity()


def test_symlinked_private_key_is_rejected(tmp_path):
    store = make_store(tmp_path)
    store.initialize_pending(API, AUD)
    target = tmp_path / "elsewhere.pem"
    target.write_bytes(store.private_key_path.read_bytes())
    store.private_key_path.unlink()
    store.private_key_path.symlink_to(target)

    with pytest.raises(IdentityCorruptError):
        store.load_private_key()


def test_unsafe_file_permissions_are_rejected(tmp_path):
    store = make_store(tmp_path)
    store.initialize_pending(API, AUD)
    os.chmod(store.private_key_path, 0o644)

    with pytest.raises(IdentityCorruptError):
        store.load_identity()


def test_unsafe_directory_permissions_are_rejected(tmp_path):
    store = make_store(tmp_path)
    store.initialize_pending(API, AUD)
    os.chmod(store.identity_dir, 0o755)

    with pytest.raises(IdentityError):
        store.load_identity()


def test_ensure_directory_rejects_preexisting_unsafe_permissions(tmp_path):
    store = make_store(tmp_path)
    os.chmod(store.identity_dir, 0o750)

    with pytest.raises(IdentityError):
        store.ensure_directory()


def test_atomic_writes_leave_no_tempfiles(tmp_path):
    store = make_store(tmp_path)
    store.initialize_pending(API, AUD)
    store.write_metadata(store.load_metadata())

    leftovers = [p.name for p in store.identity_dir.iterdir() if p.name.startswith(".tmp-")]
    assert leftovers == []


def test_try_load_enrolled_returns_none_while_pending(tmp_path):
    store = make_store(tmp_path)
    store.initialize_pending(API, AUD)
    assert store.try_load_enrolled() is None


def test_reset_removes_key_and_metadata(tmp_path):
    store = make_store(tmp_path)
    store.initialize_pending(API, AUD)

    removed = store.reset()

    assert len(removed) == 2
    assert not store.private_key_path.exists()
    assert not store.identity_path.exists()
    # A fresh enroll after reset generates a brand-new key.
    assert IdentityStore(tmp_path / "identity").load_private_key() is None
