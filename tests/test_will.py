# tests/test_will.py

from lada.brain import will


def test_boundary_detection():
    assert will.detect_boundary_request("не присылай мне это") is not None
    assert will.detect_boundary_request("прекрати") is not None
    assert will.detect_boundary_request("как дела?") is None


def test_mood_descriptor_returns_string():
    desc = will.mood_descriptor_for_photo()
    assert isinstance(desc, str)
    assert len(desc) > 0


def test_response_signal_returns_string():
    sig = will.response_signal(123, "привет")
    assert isinstance(sig, str)
    assert "настроение" in sig


def test_messages_since_last_photo():
    n = will.messages_since_last_photo(999999999, 100)
    assert isinstance(n, int)