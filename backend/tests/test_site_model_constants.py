"""The project page's illustrative model-v2 demo (site/index.html) must use the backend's real constants
and formulas: every fixed constant in its model-constants block is pinned to its source here, and the
page's JS port of the shrinkage, interval and confidence steps is run under node against the real
app.ml functions. The fitted parts (kappas, phi, confidence cutoffs) are example values the page labels
illustrative; they are only held to the ranges and grids the real model draws them from."""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("scipy")  # app.ml imports the train group's scipy

from scipy.special import ndtr  # noqa: E402

from app.ml import eb, posterior, tuning  # noqa: E402
from app.services.risk_bands import RISK_BAND_THRESHOLDS, color_code_for  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SITE_PAGE = ROOT / "site" / "index.html"
RISK_UTILS = ROOT / "frontend" / "src" / "utils" / "riskUtils.ts"
PINNED = {"risk_band_thresholds", "interval_quantiles", "phi_grid", "confidence_ratio_grid", "n_high", "n_geo",
          "n_own", "min_prior_shape", "kappa_bounds", "band_hex", "no_risk_hex", "labels", "illustrative"}
ILLUSTRATIVE = {"phi", "conf_high", "conf_medium", "kappa_area", "kappa_arch", "kappa_cell", "reference_log_sd"}


def _page() -> str:
    return SITE_PAGE.read_text(encoding="utf-8")


def _script(script_id: str, kind: str = "") -> str:
    attrs = f' type="{kind}" id="{script_id}"' if kind else f' id="{script_id}"'
    match = re.search(rf"<script{attrs}>(.*?)</script>", _page(), re.DOTALL)
    assert match, f"{script_id} block missing from site/index.html"
    return match.group(1)


def _site_constants() -> dict:
    return json.loads(_script("model-constants", "application/json"))


def _ts_string(name: str) -> str:
    match = re.search(rf"export const {name} = '([^']*)';", RISK_UTILS.read_text(encoding="utf-8"))
    assert match, f"{name} not found in riskUtils.ts"
    return match.group(1)


def _ts_object(name: str) -> dict[str, str]:
    match = re.search(rf"export const {name}[^=]*= \{{(.*?)\}};", RISK_UTILS.read_text(encoding="utf-8"), re.DOTALL)
    assert match, f"{name} not found in riskUtils.ts"
    return dict(re.findall(r"(\w+): '([^']*)'", match.group(1)))


def test_block_holds_only_pinned_keys():
    """A new constant must be pinned below (or go under "illustrative") before the page can use it."""
    site = _site_constants()
    assert set(site) == PINNED
    assert set(site["illustrative"]) == ILLUSTRATIVE


def test_model_constants_match_backend():
    site = _site_constants()
    assert tuple(site["risk_band_thresholds"]) == RISK_BAND_THRESHOLDS
    assert tuple(site["interval_quantiles"]) == (posterior.Q_LO, posterior.Q_HI)
    assert tuple(site["phi_grid"]) == posterior.PHI_GRID
    assert tuple(site["confidence_ratio_grid"]) == tuning.RATIO_GRID
    assert site["n_high"] == posterior.N_HIGH
    assert site["n_geo"] == posterior.N_GEO
    assert site["n_own"] == posterior.N_OWN
    assert site["min_prior_shape"] == eb.MIN_PRIOR_SHAPE
    assert tuple(site["kappa_bounds"]) == (eb.KAPPA_MIN, eb.KAPPA_MAX)


def test_display_constants_match_frontend():
    site = _site_constants()
    hexes = _ts_object("RISK_COLOR_HEX")
    hexes["green"] = _ts_string("LOWEST_BAND_HEX")
    assert site["band_hex"] == hexes
    assert site["no_risk_hex"] == _ts_string("NO_RISK_HEX")
    labels = site["labels"]
    assert labels["lowest_band"] == _ts_string("LOWEST_BAND_LABEL")
    assert labels["insufficient"] == _ts_string("INSUFFICIENT_DATA_LABEL")
    assert labels["insufficient_message"] == _ts_string("INSUFFICIENT_DATA_MESSAGE")
    assert labels["confidence"] == _ts_object("CONFIDENCE_LABEL")
    legend = re.findall(r"\{ label: `([^`(]+?) \(", RISK_UTILS.read_text(encoding="utf-8"))
    assert labels["band"] == dict(zip(("green", "yellow", "orange", "red"), legend[:4]))
    assert labels["disclaimer"] == re.search(r"RISK_DISCLAIMER =\s*'([^']*)'", RISK_UTILS.read_text()).group(1)
    page = _page()
    for code, hex_ in site["band_hex"].items():
        assert f"--band-{code}: {hex_};" in page, code
    assert f"--no-risk: {site['no_risk_hex']};" in page


def test_illustrative_values_lie_where_the_model_draws_them():
    ill = _site_constants()["illustrative"]
    assert ill["phi"] in posterior.PHI_GRID
    assert ill["conf_high"] in tuning.RATIO_GRID and ill["conf_medium"] in tuning.RATIO_GRID
    assert ill["conf_high"] < ill["conf_medium"]
    posterior.Thresholds(d_star=1.0, conf_high=ill["conf_high"], conf_medium=ill["conf_medium"], phi=ill["phi"])
    for k in ("kappa_area", "kappa_arch", "kappa_cell"):
        assert eb.KAPPA_MIN <= ill[k] <= eb.KAPPA_MAX
    assert ill["reference_log_sd"] > 0


def test_page_carries_no_kernel_or_reassuring_wording():
    page = _page()
    for banned in ("Low Risk", "bandwidth", "weather³", "Weather similarity", "normalization", "temporal_lambda",
                   "min_estimable_score", ">Low<"):
        assert banned not in page, banned
    assert "Illustrative" in page and "Made-up inputs" in page


CASES = [
    {"y": 0, "mu0": 0.6, "oArea": 0, "eArea": 2.0, "oArch": 14, "eArch": 6.0, "useArch": True},
    {"y": 12, "mu0": 6.0, "oArea": 25, "eArea": 15.0, "oArch": 30, "eArch": 25.0, "useArch": True},
    {"y": 0, "mu0": 12.0, "oArea": 1, "eArea": 20.0, "oArch": 10, "eArch": 25.0, "useArch": True},
    {"y": 0, "mu0": 0.6, "oArea": 0, "eArea": 3.0, "oArch": 0, "eArch": 5.0, "useArch": False},
    {"y": 0, "mu0": 0.05, "oArea": 0, "eArea": 0.1, "oArch": 0, "eArch": 0.1, "useArch": True},
    {"y": 0, "mu0": 0.05, "oArea": 0, "eArea": 0.1, "oArch": 1, "eArch": 0.1, "useArch": True},
    {"y": 3, "mu0": 20.0, "oArea": 2, "eArea": 50.0, "oArch": 0, "eArch": 0.1, "useArch": False},
]


def _reference(M: dict, x: dict) -> dict:
    """The demo's steps through the real app.ml functions (one-level geo chain, national rate 1)."""
    ill = M["illustrative"]
    a = lambda v: np.array([float(v)])  # noqa: E731
    one, zero = a(1.0), a(0.0)
    k_a = eb._floored_kappa(ill["kappa_area"], one, zero)
    k_k = eb._floored_kappa(ill["kappa_arch"], one, zero)
    sh_a, rt_a = x["oArea"] + k_a, x["eArea"] + k_a
    sh_k, rt_k = x["oArch"] + k_k, x["eArch"] + k_k
    use = np.array([x["useArch"]])
    prior, prior_var, wg = eb.blend_moments(sh_a, rt_a, sh_k, rt_k, use)
    k_c = eb._floored_kappa(ill["kappa_cell"], prior, prior_var)
    shape, rate = x["y"] + k_c * prior, x["mu0"] + k_c
    omega = x["mu0"] / rate
    chain = {"omega_area": x["eArea"] / rt_a, "O_area": a(x["oArea"]), "omega_region": zero, "O_region": zero,
             "omega_state": zero, "O_state": zero,
             "omega_arch": np.where(use, x["eArch"] / rt_k, 0.0), "O_arch": np.where(use, x["oArch"], 0.0)}
    cp = eb.CellPost(eb.GammaPost(shape, rate), prior, omega, wg, a(x["y"]), chain)
    n_geo = posterior.n_eff_geo(cp)
    # posterior.summarize computes n_feat inline (no function to call); this is its expression.
    n_feat = (1 - omega) * (1 - wg) * chain["omega_arch"] * chain["O_arch"]
    geo = n_geo > posterior.GEO_TOL
    # evidence_dist is the feature distance to the nearest area with incidents (model.predict); the
    # page's proxy: within d* exactly when comparable routes are used and have incidents.
    dist = a(0.0 if x["useArch"] and x["oArch"] > 0 else np.inf)
    insufficient = posterior.insufficient_mask(geo, n_feat, dist, d_star=1.0)
    out = {"insufficient": bool(insufficient[0]), "shape": float(shape[0]), "rate": float(rate[0]),
           "nGeo": float(n_geo[0]), "nFeat": float(n_feat[0])}
    if out["insufficient"]:
        return out
    lo, mean, hi = posterior.interval90(shape, rate, ill["phi"])
    th = posterior.Thresholds(d_star=1.0, conf_high=ill["conf_high"], conf_medium=ill["conf_medium"],
                              phi=ill["phi"])
    conf = posterior.confidence_level(hi / lo, insufficient, th, n_geo, n_feat, a(x["y"]))
    s = ill["reference_log_sd"]
    pct = [float(100 * ndtr((np.log(v[0]) + s * s / 2) / s)) for v in (lo, mean, hi)]
    out.update(rr=[float(lo[0]), float(mean[0]), float(hi[0])], conf=str(conf[0]),
               band=color_code_for(round(pct[1], 1)), pct=pct)
    return out


def test_page_js_matches_the_real_model_functions():
    node = shutil.which("node")
    if node is None:
        if os.environ.get("CI"):
            pytest.fail("node is required in CI to check the page's demo math")
        pytest.skip("node not on PATH")
    M = _site_constants()
    harness = (_script("demo-math") + f"\nvar M = {json.dumps(M)};\n"
               f"process.stdout.write(JSON.stringify({json.dumps(CASES)}.map(function (x) {{"
               "return SafeAscentDemo.run(M, x); })));")
    got = json.loads(subprocess.run([node, "-"], input=harness, capture_output=True, text=True, check=True,
                                    timeout=60).stdout)
    for x, js in zip(CASES, got):
        ref = _reference(M, x)
        assert js["insufficient"] == ref["insufficient"], x
        for k in ("shape", "rate", "nGeo", "nFeat"):
            assert js[k] == pytest.approx(ref[k], rel=1e-9, abs=1e-12), (x, k)
        if ref["insufficient"]:
            continue
        assert [js["rr"]["lo"], js["rr"]["mean"], js["rr"]["hi"]] == pytest.approx(ref["rr"], rel=1e-6), x
        assert js["conf"] == ref["conf"], x
        assert js["band"] == ref["band"], x
        assert [js["pct"]["lo"], js["pct"]["mean"], js["pct"]["hi"]] == pytest.approx(ref["pct"], abs=1e-4), x
    assert {r.get("conf") for r in got} >= {"high", "low"}
    assert [r["insufficient"] for r in got] == [False, False, False, True, True, False, False]
