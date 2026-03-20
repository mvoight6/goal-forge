"""ID generator tests."""


def test_id_generator_format():
    """ID generator produces correctly formatted GF- IDs."""
    from goalforge.id_generator import _parse_numeric
    assert _parse_numeric("GF-0001") == 1
    assert _parse_numeric("GF-0042") == 42
    assert _parse_numeric("GF-1234") == 1234
    assert _parse_numeric("invalid") == 0
