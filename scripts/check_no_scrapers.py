#!/usr/bin/env python3
"""Fail CI if any tracked file looks like scraper code (Phase 1 decision D9).

Scrapers live only in local, remote-less directories. Open-API clients
(OpenBeta, Open-Meteo, NOAA, ...) are fine and are not matched here.
Stdlib only, so the guard job needs no dependency install.
"""
from __future__ import annotations

import fnmatch
import json
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

# These two files must name every banned pattern to test it, so they are exempt
# from all rules by exact path. Nothing else may be added here without an
# owner-approved PR.
EXEMPT_PATHS = frozenset(
    {
        "scripts/check_no_scrapers.py",
        "backend/tests/test_check_no_scrapers.py",
    }
)
ALLOWLIST: frozenset[str] = frozenset()

NAME_GLOBS = ("scrape*", "*scraper*")
SOURCE_SUFFIXES = frozenset({".py", ".ts", ".tsx", ".js", ".jsx", ".ipynb", ".sh"})
BANNED_HOSTS = frozenset({"mountainproject.com", "thecrag.com", "8a.nu", "ukclimbing.com"})

BANNED_PY_MODULES = ("bs4", "lxml.html", "html5lib", "selectolax", "parsel", "scrapy", "selenium", "playwright")
BANNED_JS_MODULES = ("playwright", "playwright-core", "@playwright/test", "selenium-webdriver", "cheerio", "jsdom")
# Distribution names as they appear in lockfiles. lxml is deliberately absent:
# it is a common transitive dependency, and lxml.html use is caught by the
# import rule instead.
BANNED_PY_DISTS = frozenset({"beautifulsoup4", "bs4", "html5lib", "selectolax", "parsel", "scrapy", "selenium", "playwright"})
# Same treatment for jsdom as lxml above: it's a mainstream devDependency for
# a Node DOM test environment (this repo's own frontend/package-lock.json has
# it for Vitest), not itself a scraping tool. Its *use* in source is caught by
# the import rule; mere presence in a lockfile is not banned.
BANNED_NPM_PACKAGES = frozenset(BANNED_JS_MODULES) - {"jsdom"}

# macOS's case-insensitive filesystem resolves `import BS4` to the real bs4
# package, so both import regexes must match case-insensitively.
_PY_IMPORT = re.compile(
    r"^\s*(?:import|from)\s+(" + "|".join(re.escape(m) for m in BANNED_PY_MODULES) + r")\b"
    r"|^\s*from\s+lxml\s+import\s+(?:[^\n]*\b)?html\b",
    re.MULTILINE | re.IGNORECASE,
)
_JS_IMPORT = re.compile(
    r"""(?:\bfrom\s+|\brequire\(\s*|\bimport\(\s*|^\s*import\s+)['"]("""
    + "|".join(re.escape(m) for m in BANNED_JS_MODULES)
    + r""")(?:/[^'"]*)?['"]""",
    re.MULTILINE | re.IGNORECASE,
)
# Host must be preceded/followed by a non-word character (protocol slash, a
# dot before a subdomain, a quote, or start/end of string) so a literal
# domain like "8a.nu" doesn't misfire inside an unrelated token such as
# "v8a.number" (which contains "8a.nu" as a plain substring).
_HOST_PATTERN = re.compile(
    r"(?<!\w)(" + "|".join(re.escape(h) for h in BANNED_HOSTS) + r")(?!\w)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Violation:
    path: str
    rule: str
    detail: str

    def __str__(self) -> str:
        return f"{self.path}: [{self.rule}] {self.detail}"


def _notebook_source(text: str) -> str:
    try:
        cells = json.loads(text).get("cells", [])
    except (json.JSONDecodeError, AttributeError):
        return text
    parts: list[str] = []
    for cell in cells:
        src = cell.get("source", "")
        parts.append("".join(src) if isinstance(src, list) else str(src))
    return "\n".join(parts)


def _lockfile_violations(path: str, text: str) -> list[Violation]:
    name = PurePosixPath(path).name
    found: set[str] = set()
    if name == "uv.lock":
        for package in tomllib.loads(text).get("package", []):
            if str(package.get("name", "")).lower() in BANNED_PY_DISTS:
                found.add(str(package["name"]))
    elif name == "package-lock.json":
        for key in json.loads(text).get("packages", {}):
            package = key.rpartition("node_modules/")[2]
            if package in BANNED_NPM_PACKAGES:
                found.add(package)
    return [Violation(path, "lockfile", f"lists banned package {p}") for p in sorted(found)]


def check_file(path: str, text: str | None) -> list[Violation]:
    """Return violations for one tracked file. `text` is None for unreadable/binary files."""
    if path in EXEMPT_PATHS or path in ALLOWLIST:
        return []
    violations: list[Violation] = []
    pure = PurePosixPath(path)
    basename = pure.name.lower()
    if any(fnmatch.fnmatch(basename, glob) for glob in NAME_GLOBS):
        violations.append(Violation(path, "name", "file name matches scrape*/*scraper*"))
    if text is None:
        return violations
    suffix = pure.suffix.lower()
    if suffix in SOURCE_SUFFIXES:
        source = _notebook_source(text) if suffix == ".ipynb" else text
        hosts_found = {m.group(0).lower() for m in _HOST_PATTERN.finditer(source)}
        for host in sorted(hosts_found):
            violations.append(Violation(path, "host", f"source mentions {host}"))
        if suffix in {".py", ".ipynb"}:
            for match in _PY_IMPORT.finditer(source):
                violations.append(Violation(path, "import", match.group(0).strip()))
        elif suffix in {".ts", ".tsx", ".js", ".jsx"}:
            for match in _JS_IMPORT.finditer(source):
                violations.append(Violation(path, "import", match.group(0).strip()))
    if pure.name in {"uv.lock", "package-lock.json"}:
        violations.extend(_lockfile_violations(path, text))
    return violations


def tracked_files(repo_root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=repo_root, check=True, capture_output=True
    ).stdout
    return [p for p in out.decode().split("\0") if p]


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
        return None


def find_violations(repo_root: Path, paths: list[str]) -> list[Violation]:
    violations: list[Violation] = []
    for rel in paths:
        violations.extend(check_file(rel, _read(repo_root / rel)))
    return violations


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    repo_root = Path(args[0]) if args else Path.cwd()
    violations = find_violations(repo_root, tracked_files(repo_root))
    for violation in violations:
        print(violation, file=sys.stderr)
    if violations:
        print(
            f"\n{len(violations)} scraper-guard violation(s). Scraper code never goes in a "
            "GitHub repo (D9); keep it in ~/Developer/safeascent-private/.",
            file=sys.stderr,
        )
        return 1
    print("check_no_scrapers: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
