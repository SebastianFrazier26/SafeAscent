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


def test_rock_with_no_discipline_flag_is_its_own_group():
    # Owner decision 2026-10-13: 105,701 MP routes carry only "rock" (sport vs trad unknown).
    assert tg("Rock") == "rock"
    assert tg("Trad, Rock") == "trad"


def test_rock_precedence_is_overridden_by_any_other_discipline_flag():
    # The rock group applies only when no other discipline flag is present (I2).
    assert tg("Sport, Rock") == "sport"
    assert tg("TR, Rock") == "trad"
    assert tg("Aid, Rock") == "trad"
    assert tg("Ice, Rock") == "ice"


def test_rock_with_a_v_grade_is_a_boulder_not_a_rock_route():
    only = map_type_group(flags_from_mp_type("Rock"), ["V4"])
    assert (only.type_group, only.is_boulder) == (None, True)
    assert map_type_group(flags_from_mp_type("Rock"), ["5.9"]).type_group == "rock"


def test_v_grade_boulder_forms_are_all_recognized():
    # Controller ruling: "VB" and "V-easy" are boulder grades too (Minor 1).
    assert map_type_group(flags_from_mp_type("Rock"), ["VB"]).is_boulder is True
    assert map_type_group(flags_from_mp_type("Rock"), ["V-easy"]).is_boulder is True


def test_boulder_flag_with_rock_and_no_other_discipline_is_a_boulder():
    # I1: "Boulder, Rock" must not fall through to the scorable rock group.
    only = map_type_group(flags_from_mp_type("Boulder, Rock"), [])
    assert (only.type_group, only.is_boulder) == (None, True)


def test_sport_still_maps_to_sport():
    assert tg("Sport") == "sport"


def test_toprope_still_takes_the_underlying_rock_group():
    assert tg("TR") == "trad"
    assert tg("Top Rope") == "trad"


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
    ("Rock", {"rock"}),
    (None, set()),
])
def test_mp_type_strings(raw, flags):
    assert flags_from_mp_type(raw) == flags
