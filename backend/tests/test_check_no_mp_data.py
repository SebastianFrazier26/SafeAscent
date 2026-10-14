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
