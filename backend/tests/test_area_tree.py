from app.pipelines.area_tree import LocationRow, build_area_tree, root_state

STATE, REGION, CRAG, SUBCRAG, COPYSTATE = 900000100, 900000110, 900000111, 900000112, 900000113
ABROAD, ABROAD_CRAG = 900000200, 900000201
CYCLE_A, CYCLE_B, ORPHAN = 900000300, 900000301, 900000400
ROOTS = {STATE: "CO", ABROAD: None}


def rows(override=None):
    base = {
        STATE: LocationRow(STATE, None, 39.0, -105.5),
        REGION: LocationRow(REGION, STATE, 40.2, -105.3),
        CRAG: LocationRow(CRAG, REGION, 40.2, -105.3),
        SUBCRAG: LocationRow(SUBCRAG, CRAG, 40.2, -105.3),
        COPYSTATE: LocationRow(COPYSTATE, STATE, 39.0, -105.5),
        ABROAD: LocationRow(ABROAD, None, None, None),
        ABROAD_CRAG: LocationRow(ABROAD_CRAG, ABROAD, 46.5, 7.9),
        CYCLE_A: LocationRow(CYCLE_A, CYCLE_B, 40.0, -105.0),
        CYCLE_B: LocationRow(CYCLE_B, CYCLE_A, 40.0, -105.0),
        ORPHAN: LocationRow(ORPHAN, 900000999, 40.0, -105.0),
    }
    base.update(override or {})
    return list(base.values())


def test_paths_depths_and_states():
    tree = build_area_tree(rows(), ROOTS)
    assert (tree[CRAG].path, tree[CRAG].depth, tree[CRAG].tree_status, tree[CRAG].state_code) == (
        f"{STATE}.{REGION}.{CRAG}", 3, "us", "CO")
    assert (tree[STATE].path, tree[STATE].depth) == (str(STATE), 1)
    assert (tree[ABROAD_CRAG].tree_status, tree[ABROAD_CRAG].state_code, tree[ABROAD_CRAG].path) == (
        "foreign_root", None, f"{ABROAD}.{ABROAD_CRAG}")


def test_cycles_and_orphans_are_flagged_not_followed():
    tree = build_area_tree(rows(), ROOTS)
    assert {tree[CYCLE_A].tree_status, tree[CYCLE_B].tree_status} == {"cycle"}
    assert tree[CYCLE_A].path is None and tree[CYCLE_A].state_code is None
    assert (tree[ORPHAN].tree_status, tree[ORPHAN].path) == ("orphan_parent", None)


def test_inherited_coordinates_resolve_to_their_true_source():
    tree = build_area_tree(rows(), ROOTS)
    assert (tree[REGION].coord_precision, tree[REGION].coord_source_id) == ("own", REGION)
    assert (tree[CRAG].coord_precision, tree[CRAG].coord_source_id) == ("inherited", REGION)
    assert (tree[SUBCRAG].coord_precision, tree[SUBCRAG].coord_source_id) == ("inherited", REGION)
    assert tree[SUBCRAG].point_key == tree[REGION].point_key == "40.20000:-105.30000"


def test_state_level_coordinates_are_none():
    tree = build_area_tree(rows(), ROOTS)
    for area in (STATE, COPYSTATE):
        node = tree[area]
        assert (node.coord_precision, node.lat, node.point_key, node.coord_issue) == ("none", None, None, "state_level")


def test_bad_and_missing_coordinates_are_none_never_zero():
    tree = build_area_tree(rows({CRAG: LocationRow(CRAG, REGION, 0.0, 0.0),
                                   SUBCRAG: LocationRow(SUBCRAG, CRAG, None, None)}), ROOTS)
    assert (tree[CRAG].coord_precision, tree[CRAG].lat, tree[CRAG].coord_issue) == ("none", None, "outside_us")
    assert (tree[SUBCRAG].coord_precision, tree[SUBCRAG].coord_issue) == ("none", "missing_coords")


def test_a_child_matching_an_invalid_parent_pair_is_still_judged_on_its_own_pair():
    tree = build_area_tree(rows({REGION: LocationRow(REGION, STATE, 0.0, 0.0),
                                   CRAG: LocationRow(CRAG, REGION, 0.0, 0.0)}), ROOTS)
    assert (tree[CRAG].coord_precision, tree[CRAG].coord_issue) == ("none", "outside_us")


def test_root_state_from_name():
    assert root_state("Colorado") == "CO"
    assert root_state("District of Columbia") == "DC"
    assert root_state("Fixture Abroad") is None
    assert root_state(None) is None
