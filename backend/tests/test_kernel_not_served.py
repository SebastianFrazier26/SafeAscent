import ast
from pathlib import Path

APP = Path(__file__).parents[1] / "app"
FROZEN = ("app.ml.kernel_ref", "app.ml.kernel_adapter")
# Everything the API and the worker serve from. app/ml (training, backtest) and app/ml/kernel_ref
# itself are the only places the frozen kernel may be reached.
SERVED = ("api", "tasks", "services", "schemas", "utils", "pipelines", "models", "db")
SERVED_FILES = ("main.py", "celery_app.py", "celery_signals.py", "config.py", "healthchecks.py")


def _imports(path: Path) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.append(node.module)
            found += [f"{node.module}.{a.name}" for a in node.names]
    return found


def _served_files() -> list[Path]:
    files = [APP / name for name in SERVED_FILES]
    for pkg in SERVED:
        files += sorted((APP / pkg).rglob("*.py"))
    return files


def test_nothing_served_imports_the_frozen_kernel():
    offenders = [f"{f.relative_to(APP)} imports {mod}" for f in _served_files() for mod in _imports(f)
                 if any(mod == p or mod.startswith(p + ".") for p in FROZEN)]
    assert offenders == []


def test_scan_covers_the_served_tree():
    names = {f.name for f in _served_files()}
    assert {"main.py", "model_scores.py", "mp_routes.py", "cache_warming.py"} <= names


MOVED = ("safety_algorithm", "safety_algorithm_vectorized", "weather_similarity", "algorithm_config",
         "temporal_weighting", "spatial_weighting", "elevation_weighting", "grade_weighting",
         "route_type_weighting", "severity_weighting", "route_type_mapper")


def test_kernel_modules_left_services():
    for name in MOVED:
        assert not (APP / "services" / f"{name}.py").exists(), name
        assert (APP / "ml" / "kernel_ref" / f"{name}.py").exists(), name
    assert not (APP / "services" / "location_safety_computation.py").exists()


def test_the_kernel_nightly_is_gone():
    assert not (APP / "tasks" / "safety_computation_optimized.py").exists()
