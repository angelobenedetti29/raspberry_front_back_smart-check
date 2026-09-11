"""Direct tests for the ToastTracker state machine.

These tests document the *current* behavior of ``backend/use_cases/
toast_tracker.py``; they intentionally do not change it.  Known inconsistency:
the constructor exposes ``min_burnt_confirm_frames`` (default 5) but the
matching branch hardcodes a 3-frame confirmation threshold.  That option only
takes effect for brand-new tracks when it is <= 1.
"""

from backend.domain.entities.detection import DetectionResult
from backend.use_cases.toast_tracker import ToastTracker

BBOX = (10, 10, 20, 20)


def _det(label="TCOK", bbox=BBOX, confidence=0.9):
    return DetectionResult(label, confidence, bbox)


def test_same_bbox_keeps_one_track_and_id():
    tracker = ToastTracker()
    tracker.update([_det()])
    assert len(tracker.tracked_toasts) == 1

    tracker.update([_det()])
    tracker.update([_det()])

    assert list(tracker.tracked_toasts.keys()) == [1]
    assert tracker.next_id == 2


def test_new_track_starts_ok_with_burnt_counter_one():
    tracker = ToastTracker()
    active, newly = tracker.update([_det("TCQ")])

    toast = tracker.tracked_toasts[1]
    assert toast.state == "ok"
    assert toast.label == "TCOK"  # hysteresis: not burnt yet
    assert toast.consecutive_burnt_frames == 1
    assert [t.id for t in active] == [1]
    assert newly == []


def test_burn_transition_on_third_consecutive_frame():
    tracker = ToastTracker()
    # The constructor default is 5, but the transition hardcodes 3 frames.
    assert tracker.min_burnt_confirm_frames == 5

    for expected in (1, 2):
        _, newly = tracker.update([_det("TCQ")])
        assert tracker.tracked_toasts[1].state == "ok"
        assert tracker.tracked_toasts[1].consecutive_burnt_frames == expected
        assert newly == []

    _, newly = tracker.update([_det("TCQ")])
    toast = tracker.tracked_toasts[1]
    assert toast.state == "burnt"
    assert toast.label == "TCQ"
    assert [t.id for t in newly] == [1]


def test_newly_burnt_is_reported_once():
    tracker = ToastTracker()
    newly = []
    for _ in range(3):
        _, newly = tracker.update([_det("TCQ")])
    assert [t.id for t in newly] == [1]

    for _ in range(5):
        _, repeated = tracker.update([_det("TCQ")])
        assert repeated == []


def test_burnt_state_is_sticky():
    tracker = ToastTracker()
    for _ in range(3):
        tracker.update([_det("TCQ")])
    assert tracker.tracked_toasts[1].state == "burnt"

    tracker.update([_det("TCOK")])
    toast = tracker.tracked_toasts[1]
    assert toast.state == "burnt"
    assert toast.label == "TCQ"


def test_lost_track_is_dropped_after_max_lost_frames():
    tracker = ToastTracker(max_lost_frames=2)
    tracker.update([_det()])
    assert 1 in tracker.tracked_toasts

    tracker.update([])  # frames_since_seen = 1
    tracker.update([])  # 2  (not strictly greater than max_lost_frames)
    assert 1 in tracker.tracked_toasts

    tracker.update([])  # 3  (> 2 -> dropped)
    assert tracker.tracked_toasts == {}
    assert tracker.next_id == 1  # counter resets once the scene is empty


def test_burnt_labels_are_recognized_and_normalized():
    for label in ("TCQ", "tcq", "Tostada Quemada", "quemada"):
        tracker = ToastTracker()
        tracker.update([_det(label)])
        assert tracker.tracked_toasts[1].consecutive_burnt_frames == 1


def test_non_burnt_labels_do_not_advance_burn_counter():
    for label in ("TCOK", "Tostada OK", "ok"):
        tracker = ToastTracker()
        _, newly = tracker.update([_det(label)])
        toast = tracker.tracked_toasts[1]
        assert toast.consecutive_burnt_frames == 0
        assert toast.state == "ok"
        assert toast.label == "TCOK"
        assert newly == []


def test_min_burnt_confirm_frames_one_confirms_immediately():
    tracker = ToastTracker(min_burnt_confirm_frames=1)
    _, newly = tracker.update([_det("TCQ")])

    toast = tracker.tracked_toasts[1]
    assert toast.state == "burnt"
    assert toast.label == "TCQ"
    assert [t.id for t in newly] == [1]


def test_reset_clears_tracks_and_ids():
    tracker = ToastTracker()
    tracker.update([_det()])
    tracker.update([_det()])

    tracker.reset()

    assert tracker.tracked_toasts == {}
    assert tracker.next_id == 1
