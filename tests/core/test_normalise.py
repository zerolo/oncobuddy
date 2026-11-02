import pytest

from oncobuddy.core.normalise import normalise_condition, normalise_drug

KELVIN = "K"  # KELVIN SIGN: uppercases/lowercases like K but is not ASCII


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Trastuzumab ", "trastuzumab"),
        ("trastuz", "trastuz"),
        ("Herceptin", "herceptin"),
        ("trastuzumab emtansine", None),
        ("abc123", None),
        ("", None),
        ("x", None),
        ("kelvin".replace("k", KELVIN), None),
        ("ß" * 3, None),
    ],
)
def test_normalise_drug(raw, expected):
    assert normalise_drug(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("c50.9 ", "C50.9"),
        ("C50", "C50"),
        ("8500/3", "8500/3"),
        ("D05.1", "D05.1"),
        ("AB", None),
        ("JOE", None),
        ("HER", None),
        ("C50.9/3", None),
        ("1234", None),
        ("8500", None),
        ("breast cancer", None),
        ("ß1", None),
        ("ı" + "12", None),
        (KELVIN + "50", None),
        ("", None),
    ],
)
def test_normalise_condition(raw, expected):
    assert normalise_condition(raw) == expected
