"""Model bundle bytes (P3 M3): a deterministic zip of .npy arrays plus manifest.json, stored in
model.registry.bundle. numpy-only (DP7) so the API image needs no Parquet stack; allow_pickle is
never enabled, so loading a bundle cannot execute code."""

from __future__ import annotations

import hashlib
import io
import json
import math
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.typing import NDArray

# Fixed timestamp and creator OS: zip headers otherwise carry the write time and platform,
# and the same model would hash differently on every run.
_EPOCH = (1980, 1, 1, 0, 0, 0)
_UNIX = 3
MANIFEST = "manifest.json"
# A fitted bundle is a few MB; the cap only stops a corrupt or hostile zip from inflating without bound.
MAX_MEMBER_BYTES = 64 * 1024 * 1024


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _check_finite(value: Any, path: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{path} is not finite")
    if isinstance(value, Mapping):
        for k, v in value.items():
            _check_finite(v, f"{path}.{k}")
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            _check_finite(v, f"{path}[{i}]")


def _read(z: zipfile.ZipFile, name: str) -> bytes:
    if z.getinfo(name).file_size > MAX_MEMBER_BYTES:
        raise ValueError(f"bundle member {name} exceeds {MAX_MEMBER_BYTES} bytes")
    with z.open(name) as f:
        data = f.read(MAX_MEMBER_BYTES + 1)
    if len(data) > MAX_MEMBER_BYTES:
        raise ValueError(f"bundle member {name} exceeds {MAX_MEMBER_BYTES} bytes")
    return data


def _member(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=_EPOCH)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = _UNIX
    info.external_attr = 0o644 << 16
    return info


@dataclass(frozen=True)
class ModelBundle:
    version: str
    family: str
    arrays: Mapping[str, NDArray[Any]] = field(default_factory=dict)
    manifest: Mapping[str, Any] = field(default_factory=dict)

    def to_bytes(self) -> bytes:
        _check_finite(dict(self.manifest), "manifest")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for name in sorted(self.arrays):
                if not name or "/" in name or "\\" in name:
                    raise ValueError(f"array name {name!r} is not a plain member name")
                arr = np.asarray(self.arrays[name], order="C")  # ascontiguousarray would turn a 0-d array into 1-d
                if arr.dtype == object:
                    raise ValueError(f"array {name} has dtype object; bundles hold numeric arrays only")
                member = io.BytesIO()
                np.save(member, arr, allow_pickle=False)
                z.writestr(_member(f"{name}.npy"), member.getvalue())
            head = {"version": self.version, "family": self.family, "arrays": sorted(self.arrays),
                    "manifest": dict(self.manifest)}
            z.writestr(_member(MANIFEST), json.dumps(head, sort_keys=True, separators=(",", ":"), allow_nan=False))
        return buf.getvalue()

    @classmethod
    def from_bytes(cls, raw: bytes) -> ModelBundle:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            names = z.namelist()
            if len(names) != len(set(names)):
                raise ValueError("duplicate member in bundle")
            if MANIFEST not in names:
                raise ValueError("bundle has no manifest.json")
            head = json.loads(_read(z, MANIFEST))
            if not (isinstance(head, dict) and isinstance(head.get("arrays"), list)
                    and isinstance(head.get("manifest"), dict)):
                raise ValueError("bundle manifest.json is malformed")
            expected = {MANIFEST, *(f"{n}.npy" for n in head["arrays"])}
            for member in names:
                if member not in expected:
                    raise ValueError(f"unexpected member in bundle: {member}")
            if len(names) != len(expected):
                raise ValueError("bundle is missing an array its manifest lists")
            arrays = {n: np.load(io.BytesIO(_read(z, f"{n}.npy")), allow_pickle=False) for n in head["arrays"]}
        return cls(version=str(head["version"]), family=str(head["family"]), arrays=arrays,
                   manifest=head["manifest"])
