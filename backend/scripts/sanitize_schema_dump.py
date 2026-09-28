"""Turn a raw `pg_dump --schema-only` into SQL that 0001_baseline can replay."""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Only column-0 statements are session settings; indented SETs live inside function bodies.
_DROP_LINE = re.compile(
    r"""^(
        \\.*                                          # psql meta-commands (\restrict, \connect)
      | SET\s+\w+\s*=.*;                              # session settings; some are version-specific
      | SELECT\s+pg_catalog\.set_config\('search_path'.*;
      | CREATE\s+SCHEMA\s+public;
      | COMMENT\s+ON\s+SCHEMA\s+public\s+IS\s+.*;
      | CREATE\s+EXTENSION\s+.*;                      # 0001_baseline creates extensions itself
      | COMMENT\s+ON\s+EXTENSION\s+.*;
    )\s*$""",
    re.VERBOSE,
)

_REJECT_LINE = re.compile(
    r"^(COPY\s|INSERT\s+INTO\s|GRANT\s|REVOKE\s|ALTER\s+DEFAULT\s+PRIVILEGES\s)|\sOWNER\s+TO\s|\bPASSWORD\b",
    re.IGNORECASE,
)


class DumpRejected(ValueError):
    """The dump holds data, ownership, privileges, or credentials and must be re-taken."""


def sanitize(dump: str) -> str:
    kept: list[str] = []
    for number, line in enumerate(dump.splitlines(), start=1):
        if _REJECT_LINE.search(line):
            raise DumpRejected(
                f"line {number} looks like data, ownership, a grant, or a password; "
                "re-dump with --schema-only --no-owner --no-privileges"
            )
        if _DROP_LINE.match(line):
            continue
        kept.append(line.rstrip())
    text = "\n".join(kept)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: python -m scripts.sanitize_schema_dump RAW_DUMP OUT_SQL", file=sys.stderr)
        return 2
    raw, out = Path(argv[1]), Path(argv[2])
    out.write_text(sanitize(raw.read_text(encoding="utf-8")), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
