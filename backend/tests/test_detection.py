"""Tests del predicado compartido de tostada quemada."""

import pytest

from backend.domain.entities.detection import is_burnt


@pytest.mark.parametrize(
    "label, state, expected",
    [
        ("TCQ", None, True),
        ("tcq", None, True),
        ("Tostada Quemada", None, True),
        ("quemada", None, True),
        ("Tostada muy quemada", None, True),
        ("TCOK", None, False),
        ("tostadas ok", None, False),
        (None, "burnt", True),
        ("TCOK", "burnt", True),
        (None, None, False),
        ("TCOK", "ok", False),
    ],
)
def test_is_burnt(label, state, expected):
    assert is_burnt(label, state) is expected
