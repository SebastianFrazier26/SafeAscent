import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "check_no_mp_data", Path(__file__).resolve().parents[2] / "scripts" / "check_no_mp_data.py")
assert SPEC and SPEC.loader
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)

# Every URL and "real" id is assembled at run time from the guard's own parts and a fake id just
# under the synthetic floor: a literal would trip check_no_scrapers.py (host in a .py file) and
# check_no_mp_data.py itself (this plan lives under docs/).
HOST = guard.HOST
BELOW_FLOOR = guard.SYNTHETIC_FLOOR - 1
FAKE = guard.SYNTHETIC_FLOOR + 1


def run(files: dict[str, str]) -> list[str]:
    return guard.violations(files, files.__getitem__)


def test_route_and_area_urls_are_flagged_but_domain_mentions_are_not():
    assert run({"docs/a.md": f"see https://www.{HOST}/route/{FAKE}/fixture-route"})
    assert run({"data/b.json": f'"url": "{HOST}/area/{FAKE}"'})
    assert run({"docs/c.md": f"no {HOST} URLs in fixtures"}) == []


def test_real_mp_ids_flagged_synthetic_range_allowed():
    assert run({"backend/tests/fixtures/x.json": f'{{"mp_id": {BELOW_FLOOR}}}'})
    assert run({"backend/tests/fixtures/x.json": f'{{"mp_route_id": "{BELOW_FLOOR}"}}'})
    assert run({"data/golden/y.csv": f"mp_route_id,label\n{BELOW_FLOOR},yes\n"})
    assert run({"backend/tests/fixtures/x.json": f'{{"mp_location_id": {FAKE}}}'}) == []


def test_out_of_scope_paths_are_ignored():
    assert run({"backend/app/x.py": f"mp_route_id = {BELOW_FLOOR}"}) == []


def test_guard_source_never_spells_the_host():
    source = (Path(__file__).resolve().parents[2] / "scripts" / "check_no_mp_data.py").read_text()
    assert HOST not in source


def test_bare_and_unprefixed_id_keys_are_flagged():
    for key in ("location_id", "route_id", "parent_id", "area_id", "mp_id"):
        assert run({"docs/k.md": f"{key} = {BELOW_FLOOR}"}), key
        assert run({"backend/tests/fixtures/k.json": f'{{"{key}": "{BELOW_FLOOR}"}}'}), key
        assert run({"docs/k.md": f"{key}: {FAKE}"}) == [], key


def test_plural_and_list_forms_are_flagged():
    assert run({"backend/tests/fixtures/x.json": f'{{"mp_route_ids": [{FAKE}, {BELOW_FLOOR}]}}'})
    assert run({"docs/a.md": f'area_ids = ("{BELOW_FLOOR}",)'})
    assert run({"docs/a.md": f"WHERE location_id IN ({FAKE}, {BELOW_FLOOR})"})
    assert run({"backend/tests/fixtures/x.json": f'{{"mp_location_ids": [{FAKE}, {FAKE + 1}]}}'}) == []


def test_key_must_start_at_a_word_boundary():
    assert run({"docs/a.md": f"temp_id = {BELOW_FLOOR}"}) == []
    assert run({"docs/a.md": f"xroute_id = {BELOW_FLOOR}"}) == []
    assert run({"docs/a.md": f"accident_id: {BELOW_FLOOR}"}) == []


def test_small_ids_under_these_keys_are_not_mp_ids():
    assert run({"docs/a.md": "route_id = 12, location_id: 4521"}) == []


def test_csv_columns_are_parsed_as_csv():
    assert run({"data/golden/z.csv": f'note,location_id\n"a, quoted, cell",{BELOW_FLOOR}\n'})
    assert run({"data/golden/z.csv": f'"route_id",x\n{FAKE},1\n'}) == []


def test_uuid_literals_under_id_keys_are_not_mp_ids():
    assert run({"docs/a.md": "WHERE route_id = '00000000-0000-0000-0000-0000000000b2'"}) == []
    assert run({"docs/a.md": "WHERE area_id = '12345678-aaaa-bbbb-cccc-0000000000b3'"}) == []
    assert run({"docs/a.md": f"WHERE area_id = '{BELOW_FLOOR}'"})
