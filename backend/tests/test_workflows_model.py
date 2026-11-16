"""Pre-flight F48/F47: no workflow holds the trainer credential or runs training (training, promotion and
rollback run from the owner's Mac through `TRAIN`), CI runs the credential-free fixture backtest with
the train group installed and audited, and every action is pinned to the SHA ci.yml uses for it."""

import re
from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"
CI = WORKFLOWS / "ci.yml"
FORBIDDEN = ("TRAINER_DATABASE_URL", "app.ml.train", "issue_comment")
USES = re.compile(r"^\s*-?\s*uses:\s*(\S+?)@(\S+)", re.MULTILINE)
# Actions only pages.yml uses (PR #19), so ci.yml carries no pin for them; reviewed 2026-11-15.
EXTRA_PINS = {
    "actions/configure-pages": {"45bfe0192ca1faeb007ade9deae92b16b8254a0d"},
    "actions/upload-pages-artifact": {"fc324d3547104276b827a68afc52ff2a11cc49c9"},
    "actions/deploy-pages": {"368f82528645a54fb793d4d04e342629a3f51346"},
}


def _files() -> list[Path]:
    return sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")])


def _triggers(doc: dict) -> set[str]:
    # PyYAML reads the bare key `on` as the boolean True.
    on = doc.get("on", doc.get(True))
    if isinstance(on, str):
        return {on}
    return set(on or ())


def _steps(name: str) -> list[dict]:
    doc = yaml.safe_load(CI.read_text(encoding="utf-8"))
    return doc["jobs"][name]["steps"]


def _run_of(steps: list[dict], step_name: str) -> str:
    matches = [s["run"] for s in steps if s.get("name") == step_name]
    assert len(matches) == 1, f"ci.yml backend job needs exactly one {step_name!r} step"
    return matches[0]


def test_there_are_workflows_to_check():
    assert CI in _files()


@pytest.mark.parametrize("path", _files(), ids=lambda p: p.name)
def test_no_workflow_trains_or_holds_the_trainer_credential(path):
    text = path.read_text(encoding="utf-8")
    for needle in FORBIDDEN:
        assert needle not in text, f"{path.name} mentions {needle}"
    assert "issue_comment" not in _triggers(yaml.safe_load(text))
    assert path.name not in ("model-train.yml", "model-rollback.yml")


def test_ci_runs_the_fixture_backtest_with_the_train_group():
    steps = _steps("backend")
    names = [s.get("name") for s in steps]
    backtest = _run_of(steps, "Fixture backtest")
    assert "python -m app.ml.backtest --fixture small" in backtest and "--group train" in backtest
    assert names.index("Fixture backtest") == names.index("Test") + 1
    install = _run_of(steps, "Install")
    assert "uv sync --frozen" in install and "--group pipelines" in install and "--group train" in install
    audit = _run_of(steps, "Dependency audit")
    assert "uv export" in audit and "--group train" in audit and "--group pipelines" in audit


@pytest.mark.parametrize("path", _files(), ids=lambda p: p.name)
def test_every_action_is_pinned_to_the_sha_ci_uses(path):
    pins: dict[str, set[str]] = {}
    for action, ref in USES.findall(CI.read_text(encoding="utf-8")):
        pins.setdefault(action, set()).add(ref)
    assert all(len(refs) == 1 for refs in pins.values()), f"ci.yml pins an action two ways: {pins}"
    assert not pins.keys() & EXTRA_PINS.keys(), "ci.yml now pins an EXTRA_PINS action; drop it from EXTRA_PINS"
    pins.update(EXTRA_PINS)
    for action, ref in USES.findall(path.read_text(encoding="utf-8")):
        assert re.fullmatch(r"[0-9a-f]{40}", ref), f"{path.name}: {action}@{ref} is not a full commit SHA"
        assert action in pins, f"{path.name}: {action} is not used by ci.yml, so it has no reviewed pin"
        assert {ref} == pins[action], f"{path.name}: {action}@{ref} differs from ci.yml's pin"
