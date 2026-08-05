from dashboard.art import COMPACT_ART, FULL_ART, artwork_for_size


def test_artwork_responsive_variants() -> None:
    assert artwork_for_size(140, 40) == FULL_ART
    assert artwork_for_size(80, 24) == COMPACT_ART
    assert artwork_for_size(50, 18) is None
    assert artwork_for_size(140, 40, enabled=False) is None
