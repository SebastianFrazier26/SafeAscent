import pytest

from app.ml.cells import (
    FAMILY_OF,
    G_INDEX,
    MONTHS,
    STATE_GROUP_OF,
    STATE_GROUPS,
    TRUSTED_FOR_CELLS,
    TYPE_GROUPS,
    region_key,
)
from app.pipelines import exposure_math
from app.pipelines.gnis import STATE_CODES


def test_six_scorable_groups_two_families():
    assert TYPE_GROUPS == ("sport", "trad", "rock", "alpine", "ice", "mixed")
    assert TYPE_GROUPS == exposure_math.TYPE_GROUPS
    assert "unknown" not in TYPE_GROUPS
    assert {FAMILY_OF[g] for g in ("sport", "trad", "rock")} == {"rock_family"}
    assert {FAMILY_OF[g] for g in ("alpine", "ice", "mixed")} == {"mountain"}
    assert G_INDEX == {g: i for i, g in enumerate(TYPE_GROUPS)}
    assert MONTHS == tuple(exposure_math.MONTHS)


def test_state_groups_partition_every_us_code_once():
    codes = sorted(STATE_GROUP_OF)
    assert len(STATE_GROUPS) == 8
    assert sum(len(v) for v in STATE_GROUPS.values()) == len(codes)
    assert set(codes) == set(STATE_CODES.values())


def test_region_key_is_the_depth_two_prefix():
    assert region_key("900000100.900000200.900000300") == "900000100.900000200"
    assert region_key("900000100.900000200") == "900000100.900000200"
    assert region_key("900000100") == "900000100"
    with pytest.raises(ValueError):
        region_key("Colorado.Boulder")
    with pytest.raises(ValueError):
        region_key("")


def test_trusted_precisions_exclude_fallbacks():
    assert set(TRUSTED_FOR_CELLS).isdisjoint({"park_centroid", "region_fallback", "unknown"})
