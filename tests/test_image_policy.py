# tests/test_image_policy.py

from lada.brain import image_policy as ip
from config import OWNER_ID


def test_detect_level_basic_scenes():
    assert ip._detect_level("sitting by window, coffee") == 0
    assert ip._detect_level("lingerie, bedroom") == 7
    assert ip._detect_level("nude, lying on bed") == 9
    assert ip._detect_level("bikini, beach") == 6


def test_owner_max_level():
    assert ip.effective_max_level(OWNER_ID) == 9


def test_initiative_cap_for_unknown():
    # для незнакомца с 0/9 инициатива даст cap 0
    uid = 999999998
    # минимальный уровень сцены 0 всегда проходит при initiative
    allowed, _ = ip.check_allowed(uid, "sitting by window", source="initiative")
    assert allowed is True


def test_override_caps_to_4():
    uid = 999999997
    ip.set_max_level_override(uid, 4)
    allowed, reason = ip.check_allowed(uid, "nude, bed", source="chat")
    assert allowed is False
    ip.set_max_level_override(uid, None)


def test_explain_refusal_returns_string():
    s = ip.explain_refusal(999999999, "nude, bed")
    assert isinstance(s, str)
    assert len(s) > 0