import pytest

from app.pipelines.textsim import jaro, jaro_winkler, normalize_text, place_key


def test_classic_jaro_winkler_values():
    assert jaro("MARTHA", "MARHTA") == pytest.approx(0.9444, abs=1e-4)
    assert jaro_winkler("MARTHA", "MARHTA") == pytest.approx(0.9611, abs=1e-4)
    assert jaro_winkler("DIXON", "DICKSONX") == pytest.approx(0.8133, abs=1e-4)


def test_edge_cases():
    assert jaro_winkler("", "") == 1.0
    assert jaro_winkler("abc", "") == 0.0
    assert jaro_winkler("same", "same") == 1.0


def test_normalize_and_place_key_expand_abbreviations_and_drop_generic_words():
    assert normalize_text("Mt. St. Helens!") == "mount saint helens"
    assert place_key("Mt. Rainier") == "rainier"
    assert place_key("Longs Peak") == "longs"
    assert place_key("Peak") == "peak"
    assert place_key(None) == ""


def test_jaro_none_input_treated_as_empty_string():
    assert normalize_text(None) == ""


def test_jaro_no_common_characters_returns_zero():
    assert jaro("abc", "xyz") == 0.0


def test_jaro_symmetric():
    assert jaro("MARTHA", "MARHTA") == pytest.approx(jaro("MARHTA", "MARTHA"))


def test_normalize_text_unicode_and_punctuation():
    # Non-ASCII letters fall outside [a-z0-9] and are treated as separators,
    # same as any other punctuation - documented, not a bug.
    assert normalize_text("Café-Summit, No. 2!") == "caf summit no 2"


def test_normalize_text_collapses_whitespace_and_case():
    assert normalize_text("  MOUNT   Rainier  ") == "mount rainier"


def test_place_key_all_generic_words_falls_back_to_normalized_tokens():
    assert place_key("Mount the") == "mount the"


def test_place_key_whitespace_only_and_empty_string():
    assert place_key("") == ""
    assert place_key("   ") == ""


def test_jaro_winkler_prefix_scale_zero_matches_plain_jaro():
    assert jaro_winkler("MARTHA", "MARHTA", prefix_scale=0.0) == pytest.approx(
        jaro("MARTHA", "MARHTA")
    )
