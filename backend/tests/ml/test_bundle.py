import io
import zipfile

import numpy as np
import pytest

import app.ml.bundle as bundle_mod
from app.ml.bundle import ModelBundle, sha256


def _bundle() -> ModelBundle:
    return ModelBundle(version="eb-2026-10-15", family="eb_v1",
                       arrays={"coef": np.array([0.5, -1.0]), "g": np.array([0, 5], dtype=np.int8)},
                       manifest={"names": ["intercept", "x"], "phi": 1.5})


def test_round_trip_and_determinism():
    b = _bundle()
    raw = b.to_bytes()
    assert raw == _bundle().to_bytes()
    back = ModelBundle.from_bytes(raw)
    assert back.version == b.version and back.manifest == b.manifest
    np.testing.assert_array_equal(back.arrays["coef"], b.arrays["coef"])
    assert back.arrays["g"].dtype == np.int8
    assert len(sha256(raw)) == 64


def test_bytes_do_not_depend_on_insertion_order():
    b = _bundle()
    flipped = ModelBundle(version=b.version, family=b.family, arrays=dict(reversed(list(b.arrays.items()))),
                          manifest=dict(reversed(list(b.manifest.items()))))
    assert flipped.to_bytes() == b.to_bytes()


def test_refuses_object_arrays_and_nonfinite_manifest():
    with pytest.raises(ValueError, match="object"):
        ModelBundle(version="v", family="eb_v1", arrays={"x": np.array(["a"], dtype=object)}, manifest={}).to_bytes()
    with pytest.raises(ValueError, match="finite"):
        ModelBundle(version="v", family="eb_v1", arrays={}, manifest={"phi": float("nan")}).to_bytes()
    with pytest.raises(ValueError, match="finite"):
        ModelBundle(version="v", family="eb_v1", arrays={}, manifest={"a": [{"b": float("inf")}]}).to_bytes()


def test_refuses_path_like_array_names():
    with pytest.raises(ValueError, match="plain member name"):
        ModelBundle(version="v", family="eb_v1", arrays={"../x": np.zeros(1)}, manifest={}).to_bytes()


def _zip(members: dict[str, bytes | str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    return buf.getvalue()


def test_from_bytes_refuses_extra_members():
    raw = _zip({"manifest.json": '{"version": "v", "family": "eb_v1", "arrays": [], "manifest": {}}',
                "evil.py": "print(1)"})
    with pytest.raises(ValueError, match="unexpected member"):
        ModelBundle.from_bytes(raw)


def test_from_bytes_refuses_missing_array_and_missing_manifest():
    with pytest.raises(ValueError, match="missing an array"):
        ModelBundle.from_bytes(_zip({"manifest.json": '{"version": "v", "family": "eb_v1", "arrays": ["a"], '
                                                       '"manifest": {}}'}))
    with pytest.raises(ValueError, match="no manifest"):
        ModelBundle.from_bytes(_zip({}))


def test_from_bytes_never_unpickles():
    member = io.BytesIO()
    np.save(member, np.array([{"a": 1}], dtype=object), allow_pickle=True)
    raw = _zip({"manifest.json": '{"version": "v", "family": "eb_v1", "arrays": ["x"], "manifest": {}}',
                "x.npy": member.getvalue()})
    with pytest.raises(ValueError, match="allow_pickle"):
        ModelBundle.from_bytes(raw)


def test_zero_dim_arrays_round_trip():
    raw = ModelBundle(version="v", family="eb_v1", arrays={"phi": np.array(1.5), "k": np.array(3, dtype=np.int16)},
                      manifest={}).to_bytes()
    back = ModelBundle.from_bytes(raw)
    assert back.arrays["phi"].shape == () and float(back.arrays["phi"]) == 1.5
    assert back.arrays["k"].shape == () and back.arrays["k"].dtype == np.int16


def test_from_bytes_caps_member_size(monkeypatch):
    raw = ModelBundle(version="v", family="eb_v1", arrays={"big": np.zeros(4096)}, manifest={}).to_bytes()
    monkeypatch.setattr(bundle_mod, "MAX_MEMBER_BYTES", 1024)
    with pytest.raises(ValueError, match="exceeds 1024 bytes"):
        ModelBundle.from_bytes(raw)
