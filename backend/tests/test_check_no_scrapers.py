"""Tests for scripts/check_no_scrapers.py, the D9 no-scraper CI guard."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "check_no_scrapers", REPO_ROOT / "scripts" / "check_no_scrapers.py"
)
assert _spec is not None and _spec.loader is not None
guard = importlib.util.module_from_spec(_spec)
sys.modules["check_no_scrapers"] = guard
_spec.loader.exec_module(guard)


def rules(path: str, text: str | None = "") -> list[str]:
    return [v.rule for v in guard.check_file(path, text)]


@pytest.mark.parametrize(
    "path",
    ["tools/route_scraper.py", "scrape_routes.ts", "data/Scraper.ipynb", "x/SCRAPE.sh", "notes/scraper-notes.md"],
)
def test_name_rule_flags_scraper_file_names(path):
    assert "name" in rules(path, "")


@pytest.mark.parametrize("path", ["backend/app/services/openbeta_client.py", "frontend/src/escape.js"])
def test_name_rule_ignores_innocent_names(path):
    assert rules(path, "") == []


@pytest.mark.parametrize("suffix", [".py", ".ts", ".tsx", ".js", ".jsx", ".sh"])
def test_host_rule_flags_mountainproject_in_source(suffix):
    assert rules(f"src/thing{suffix}", 'URL = "https://www.MountainProject.com/route/1"') == ["host"]


def test_host_rule_ignores_non_source_files():
    assert rules("docs/sources.md", "Data from mountainproject.com is not redistributed.") == []


def test_host_rule_reads_notebook_cells():
    nb = json.dumps({"cells": [{"source": ["url = 'https://mountainproject.com/x'\n"]}]})
    assert rules("analysis/explore.ipynb", nb) == ["host"]


@pytest.mark.parametrize(
    "line",
    [
        "import bs4",
        "from bs4 import BeautifulSoup",
        "import lxml.html",
        "from lxml import etree, html",
        "import html5lib",
        "from selectolax.parser import HTMLParser",
        "import parsel",
        "import scrapy",
        "from selenium import webdriver",
        "from playwright.sync_api import sync_playwright",
        "    import bs4  # inside a function",
    ],
)
def test_import_rule_flags_python_html_and_browser_libs(line):
    assert rules("backend/app/x.py", f"{line}\n") == ["import"]


@pytest.mark.parametrize("line", ["import lxml.etree", "from lxml import etree", "import httpx", "# import bs4"])
def test_import_rule_ignores_allowed_python_imports(line):
    assert rules("backend/app/x.py", f"{line}\n") == []


@pytest.mark.parametrize(
    "line",
    [
        "import { chromium } from 'playwright';",
        'import { test } from "@playwright/test";',
        "const { Builder } = require('selenium-webdriver');",
        "const pw = await import('playwright-core');",
        "import 'playwright';",
    ],
)
def test_import_rule_flags_js_browser_automation(line):
    assert rules("frontend/src/x.ts", f"{line}\n") == ["import"]


def test_import_rule_ignores_ordinary_js_imports():
    assert rules("frontend/src/x.jsx", "import axios from 'axios';\nimport React from 'react';\n") == []


def test_import_rule_reads_notebook_cells():
    nb = json.dumps({"cells": [{"source": "from bs4 import BeautifulSoup"}]})
    assert rules("analysis/explore.ipynb", nb) == ["import"]


def test_lockfile_rule_flags_banned_python_dist():
    lock = 'version = 1\n\n[[package]]\nname = "beautifulsoup4"\nversion = "4.12.3"\n'
    assert rules("backend/uv.lock", lock) == ["lockfile"]


def test_lockfile_rule_allows_lxml_in_uv_lock():
    lock = 'version = 1\n\n[[package]]\nname = "lxml"\nversion = "5.3.0"\n'
    assert rules("backend/uv.lock", lock) == []


def test_lockfile_rule_flags_banned_npm_package():
    lock = json.dumps({"packages": {"": {}, "node_modules/@playwright/test": {}, "node_modules/react": {}}})
    assert rules("frontend/package-lock.json", lock) == ["lockfile"]


def test_exempt_paths_are_exact():
    text = "import bs4\nURL = 'mountainproject.com'\n"
    assert rules("scripts/check_no_scrapers.py", text) == []
    assert rules("backend/tests/test_check_no_scrapers.py", text) == []
    assert set(rules("other/check_no_scrapers.py", text)) == {"name", "host", "import"}


def test_allowlist_is_empty():
    assert guard.ALLOWLIST == frozenset()


def test_binary_file_only_gets_name_rule():
    assert rules("assets/scraper.png", None) == ["name"]


def _git_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for rel, content in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    return tmp_path


def test_main_fails_on_repo_with_scraper(tmp_path, capsys):
    repo = _git_repo(tmp_path, {"tools/route_scraper.py": "print('hi')\n", "README.md": "ok\n"})
    assert guard.main([str(repo)]) == 1
    assert "tools/route_scraper.py" in capsys.readouterr().err


def test_main_only_checks_tracked_files(tmp_path):
    repo = _git_repo(tmp_path, {"README.md": "ok\n"})
    (repo / "untracked_scraper.py").write_text("import bs4\n")
    assert guard.main([str(repo)]) == 0


def test_this_repository_is_clean():
    assert guard.find_violations(REPO_ROOT, guard.tracked_files(REPO_ROOT)) == []
