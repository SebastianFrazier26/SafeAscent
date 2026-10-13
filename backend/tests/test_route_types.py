import pytest

from app.pipelines.route_types import flags_from_mp_type, map_type_group


def tg(type_str, grades=()):
    return map_type_group(flags_from_mp_type(type_str), grades).type_group


def test_precedence_mixed_beats_ice_beats_alpine_beats_trad_beats_sport():
    assert tg("Ice, Mixed, Alpine") == "mixed"
    assert tg("Ice, Alpine") == "ice"
    assert tg("Trad, Alpine") == "alpine"
    assert tg("Trad, Sport") == "trad"
    assert tg("Sport, TR") == "sport"


def test_grades_can_set_ice_mixed_and_alpine():
    assert tg("unknown", ["WI3"]) == "ice"
    assert tg("Trad", ["AI2"]) == "ice"
    assert tg("Trad", ["M6"]) == "mixed"
    assert tg("Trad", ["5.9 IV"]) == "alpine"
    assert tg("Trad", ["5.9 III"]) == "trad"


def test_commitment_token_in_the_type_string_is_alpine():
    assert tg("Trad, Grade IV") == "alpine"
    assert tg("Trad, Grade III") == "trad"


def test_v_scale_and_uiaa_grades_are_never_commitment():
    assert tg("Trad", ["V4"]) == "trad"
    assert tg("Sport", ["VI+"]) == "sport"
    assert tg("Trad", ["5.10a V"]) == "alpine"


def test_toprope_and_aid_take_the_underlying_rock_group():
    # Ruling R-1 (P3:56): the underlying rock group; with no sport/trad flag and no bolt data, trad.
    assert tg("TR") == "trad"
    assert tg("Aid") == "trad"
    assert tg("TR, Aid") == "trad"
    assert tg("Sport, TR") == "sport"
    assert tg("Trad, Aid") == "trad"
    assert tg("TR", ["WI3"]) == "ice"


def test_no_type_signal_is_unknown():
    assert tg("unknown", ["5.10a"]) == "unknown"
    assert tg(None) == "unknown"


def test_boulders_are_unscored_unless_roped():
    only = map_type_group(flags_from_mp_type("Boulder"), ["V3"])
    assert (only.type_group, only.is_boulder) == (None, True)
    assert map_type_group(flags_from_mp_type("Trad, Boulder"), []).is_boulder is False


@pytest.mark.parametrize("raw,flags", [
    ("Trad, Sport", {"trad", "sport"}),
    (" TR ,  Aid", {"tr", "aid"}),
    ("Top Rope", {"tr"}),
    ("Boulder", {"bouldering"}),
    ("Trad, Grade  V", {"trad", "commitment"}),
    ("Trad, 3 pitches, 500 ft", {"trad"}),
    (None, set()),
])
def test_mp_type_strings(raw, flags):
    assert flags_from_mp_type(raw) == flags
