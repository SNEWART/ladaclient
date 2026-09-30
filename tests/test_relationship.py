# tests/test_relationship.py

from lada.brain import relationship as rel


def test_status_order_valid():
    assert "neutral" in rel.STATUS_ORDER
    assert "husband" in rel.STATUS_ORDER
    assert rel.STATUS_ORDER.index("husband") > rel.STATUS_ORDER.index("neutral")


def test_get_status_default_for_unknown():
    assert rel.get_status(999999999) == "neutral"


def test_set_and_get_status():
    uid = 888888888
    rel.set_status(uid, "lover", note="test")
    assert rel.get_status(uid) == "lover"
    rel.set_status(uid, "neutral")


def test_status_label_returns_string():
    label = rel.get_status_label(999999999)
    assert isinstance(label, str)
    assert len(label) > 0


def test_familiarity_returns_float():
    f = rel.familiarity(999999999)
    assert isinstance(f, float)
    assert 0.0 <= f <= 1.0