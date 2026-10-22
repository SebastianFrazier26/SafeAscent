"""Term decomposition of a stored RR (P3 Task 14). The terms sum to log(rr_mean) exactly, with no
outside constant: the hazard terms are coef * design value for every non-exposure column, the type and
season columns plus const(g, m) = -log r_ref(g, m) form one term relative to the national typical route
of that type and month, and the evidence term is the residual, so it absorbs the posterior shrinkage.
With top=N the hidden hazard terms are summed into an "Other factors" term, so the list still adds up.
Exposure columns (density, season, ticks) cancel out of the RR and are never named. A missing raw
feature is explained through its missing indicator; its z term is the training mean, a zero effect.
An insufficient cell has no stored number, so a non-finite n_eff_geo or n_eff_feature raises ValueError."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from app.ml.bundle import ModelBundle
from app.ml.cells import FAMILY_OF, TYPE_GROUPS
from app.ml.dataset import STATIC_FEATURES
from app.ml.features import transform_static

EXPOSURE_NAMES = frozenset({"x_density", "x_season", "ind_season_missing", "x_ticks", "ind_ticks_off"})
TYPE_SEASON = "type_season"
EVIDENCE = "evidence"
OTHER = "other"

LABELS: dict[str, str] = {
    TYPE_SEASON: "Season for this route type",
    "z_elevation_m": "Elevation",
    "z_lat": "Latitude",
    "z_tmax_mean": "Typical daytime high this month",
    "z_tmin_mean": "Typical overnight low this month",
    "z_precip_mm": "Precipitation this month",
    "z_snowfall_cm": "Snowfall this month",
    "z_freeze_thaw_days": "Freeze-thaw days this month",
    "ind_elevation_missing": "Elevation not on file",
    "ind_normals_missing": "Monthly weather not on file",
    OTHER: "Other factors",
    EVIDENCE: "Reported incidents here and on similar routes",
}


@dataclass(frozen=True)
class Term:
    name: str
    label: str
    log_effect: float


def _is_type_season(name: str) -> bool:
    return name in ("intercept", "fam_mountain") or name.startswith(("a_", "m_", "d_"))


def explain(bundle: ModelBundle, *, type_group: str, month: int, raw_features: Mapping[str, float | None],
            rr_mean: float, n_eff_geo: float, n_eff_feature: float, top: int | None = 3) -> list[Term]:
    if type_group not in TYPE_GROUPS:
        raise ValueError(f"unknown type group {type_group!r}")
    if not 1 <= month <= 12:
        raise ValueError("month must be 1..12")
    if not (math.isfinite(rr_mean) and rr_mean > 0):
        raise ValueError("rr_mean must be finite and > 0")
    if not (math.isfinite(n_eff_geo) and math.isfinite(n_eff_feature)):
        raise ValueError("an insufficient cell has no decomposition (n_eff is not finite)")
    if top is not None and top < 0:
        raise ValueError("top must be >= 0")
    unknown = set(raw_features) - set(STATIC_FEATURES)
    if unknown:
        raise ValueError(f"unknown raw features: {sorted(unknown)}")

    names: list[str] = list(bundle.manifest["names"])
    coef = bundle.arrays["coef"]
    center, scale = bundle.arrays["center"], bundle.arrays["scale"]
    const = float(bundle.manifest["const"][type_group][str(month)])
    tr = transform_static(raw_features)

    effects: dict[str, float] = {}
    for k, name in enumerate(names):
        if name in EXPOSURE_NAMES:
            continue
        if _is_type_season(name):
            if name == "intercept":
                x = 1.0
            elif name == "fam_mountain":
                x = float(FAMILY_OF[type_group] == "mountain")
            elif name.startswith("a_"):
                x = float(name == f"a_{type_group}")
            elif name.startswith("m_"):
                x = float(name == f"m_{month}")
            else:
                x = float(name == f"d_{type_group}_{month}")
            key = TYPE_SEASON
        elif name.startswith("ind_"):
            if name not in tr:
                raise ValueError(f"unknown design column {name!r}")
            x, key = tr[name], name
        else:
            if name not in tr:
                raise ValueError(f"unknown design column {name!r}")
            v = tr[name]
            x, key = (0.0 if math.isnan(v) else (v - float(center[k])) / float(scale[k])), name
        effects[key] = effects.get(key, 0.0) + float(coef[k]) * x

    effects[TYPE_SEASON] = effects.get(TYPE_SEASON, 0.0) + const
    hazard = sorted(effects.items(), key=lambda kv: (-abs(kv[1]), kv[0]))
    evidence = math.log(rr_mean) - sum(effects.values())
    shown, hidden = (hazard, []) if top is None else (hazard[:top], hazard[top:])
    terms = [Term(n, LABELS[n], v) for n, v in shown]
    if hidden:
        terms.append(Term(OTHER, LABELS[OTHER], sum(v for _, v in hidden)))
    terms.append(Term(EVIDENCE, LABELS[EVIDENCE], evidence))
    return terms
