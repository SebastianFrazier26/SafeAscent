# Phase 2 runbook helpers (bash or zsh). From backend/:  . scripts/runbook_helpers.sh
# Plans 3-8 source this file; none of them redefines these names.
#
# TARGET_HOST picks the database and must be set explicitly (O4): a Neon branch host
# (ep-...), or the literal TARGET_HOST=production for the host already in the env file.
# Unset refuses, so a fresh shell that forgot the export never lands on prod by default.
#
# Passwords never reach argv or the terminal: env files are sourced only inside subshells,
# URLs travel through the environment or stdin, and split_pg_url moves the password into
# PGPASSWORD. psql always runs verify-full against the OS trust store.

# Homebrew's libpq is keg-only, so a login shell has no psql on PATH (final review 2a-1 C4).
# LIBPQ_BIN overrides the keg path; psql 16+ is needed for sslrootcert=system.
if ! command -v psql >/dev/null 2>&1 && [ -x "${LIBPQ_BIN:-/opt/homebrew/opt/libpq/bin}/psql" ]; then
  PATH="${LIBPQ_BIN:-/opt/homebrew/opt/libpq/bin}:$PATH"
  export PATH
fi

# Phase 1 Plan B Task 8 Step 1, with python3 in place of `uv run python3` (stdlib only).
split_pg_url() {
  { read -r PG_URL_NOPASS; read -r PGPASSWORD; } < <(SPLIT_PG_URL="$1" python3 -c '
import os
from urllib.parse import urlsplit, urlunsplit, quote, unquote
u = urlsplit(os.environ["SPLIT_PG_URL"])
# urlsplit does not decode; PGPASSWORD must be the literal characters, not the URL-escaped form.
username = quote(unquote(u.username), safe="") if u.username else None
netloc = (f"{username}@" if username else "") + (u.hostname or "") + (f":{u.port}" if u.port else "")
print(urlunsplit((u.scheme, netloc, u.path, u.query, u.fragment)))
print(unquote(u.password) if u.password else "")
')
  export PGPASSWORD
}

# Foundations Task 8 Step 1 (b4546cf): libpq spelling of PG_URL_NOPASS on verify-full.
# quote_via=quote, because urlencode's default "+" for a space is not a space to libpq.
verify_full_url() {
  PG_URL_NOPASS="$(VERIFY_FULL_URL="$PG_URL_NOPASS" python3 -c '
import os
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
u = urlsplit(os.environ["VERIFY_FULL_URL"])
query = [(k, v) for k, v in parse_qsl(u.query) if k not in ("ssl", "sslmode", "sslrootcert")]
query += [("sslmode", "verify-full"), ("sslrootcert", "system")]
print(urlunsplit(("postgresql", u.netloc, u.path, urlencode(query, quote_via=quote), u.fragment)))
')"
}

_need_psql() {
  command -v psql >/dev/null 2>&1 && return 0
  echo "psql not found: brew install libpq, or set LIBPQ_BIN to its bin directory and re-source" >&2
  return 1
}

# Foundations Task 8 Step 1: direct host of Neon branch $1, printing only the host name.
# ${1:?} matters: neonctl given an empty branch name returns the default branch's, i.e.
# production's, connection string.
branch_host() {
  neonctl connection-string "${1:?branch name}" --project-id still-morning-74008008 \
      --role-name neondb_owner --database-name neondb \
    | python3 -c '
import sys
from urllib.parse import urlsplit
host = urlsplit(sys.stdin.read().strip()).hostname or ""
# No pipefail here: a failed neonctl must print nothing, never "None".
if not host.startswith("ep-"):
    sys.exit("branch_host: no Neon endpoint host returned")
print(host)'
}

_need_target() {
  case "${TARGET_HOST:-}" in
    "") echo "TARGET_HOST is unset: export a Neon branch host, or TARGET_HOST=production for prod" >&2; return 1 ;;
    production) return 0 ;;
    *[!A-Za-z0-9.-]*) echo "TARGET_HOST must be a bare host name" >&2; return 1 ;;
  esac
}

# Prints $1 with its host replaced by TARGET_HOST (unchanged for production). Refuses an
# empty URL, or one with no host/path boundary to rewrite, rather than falling through to
# the env file's (prod) host. Any :port goes with the old host: Neon branches listen on
# the default 5432.
_retarget() {
  _need_target || return 1
  [ -n "$1" ] || { echo "_retarget: empty URL" >&2; return 1; }
  if [ "$TARGET_HOST" = production ]; then printf '%s' "$1"; return 0; fi
  local _out
  _out="$(printf '%s' "$1" | sed -E "s#@[^/?]+([/?])#@${TARGET_HOST}\1#")"
  case "$_out" in
    *"@${TARGET_HOST}/"* | *"@${TARGET_HOST}?"*) printf '%s' "$_out" ;;
    *) echo "_retarget: could not point the URL at ${TARGET_HOST}" >&2; return 1 ;;
  esac
}

# Call only inside a helper's subshell: the env file's variables must not outlive it.
# A malformed URL makes split_pg_url print an empty or user-less line; psql given that would
# fall back to local defaults instead of failing.
_split_verified() {
  split_pg_url "$1"
  case "$PG_URL_NOPASS" in
    *://*@*) ;;
    *) echo "split_pg_url: no user@host in the URL" >&2; return 1 ;;
  esac
  verify_full_url
}

_load() {
  [ -f "$1" ] || { echo "missing $1 (run from backend/)" >&2; return 1; }
  set -a; . "$1"; set +a
}

ING() { ( _need_target && _load ./.env.ingest || exit 1
  url="$(_retarget "${INGEST_DATABASE_URL:-}")" || exit 1
  INGEST_DATABASE_URL="$url" DATABASE_URL="$url" uv run python -m app.data.repair "$@" ); }

INGMOD() { ( _need_target && _load ./.env.ingest || exit 1
  url="$(_retarget "${INGEST_DATABASE_URL:-}")" || exit 1
  INGEST_DATABASE_URL="$url" DATABASE_URL="$url" uv run python -m "$@" ); }

# INGMOD with the llm dependency group (R10 LLM pass). ANTHROPIC_API_KEY passes through only if
# the operator exported it in this shell; the helper never reads or sets it.
INGLLM() { ( _need_target && _load ./.env.ingest || exit 1
  url="$(_retarget "${INGEST_DATABASE_URL:-}")" || exit 1
  INGEST_DATABASE_URL="$url" DATABASE_URL="$url" uv run --group llm python -m "$@" ); }

MIG() { ( _need_target && _load ./.env.migrator || exit 1
  url="$(_retarget "${MIGRATOR_DATABASE_URL:-}")" || exit 1
  MIGRATOR_DATABASE_URL="$url" uv run alembic "$@" ); }

OWNER_PSQL() { ( _need_target && _load ./.env.owner || exit 1
  url="$(_retarget "${OWNER_DATABASE_URL:-}")" || exit 1
  _split_verified "$url" && _need_psql || exit 1
  psql "$PG_URL_NOPASS" -X "$@" ); }

ANALYST_PSQL() { ( _need_target && _load ./.env.analyst || exit 1
  url="$(_retarget "${ANALYST_DATABASE_URL:-}")" || exit 1
  _split_verified "$url" && _need_psql || exit 1
  psql "$PG_URL_NOPASS" -X "$@" ); }

VERIFY() { ( _need_target && _load ./.env.analyst || exit 1
  url="$(_retarget "${ANALYST_DATABASE_URL:-}")" || exit 1
  VERIFY_DATABASE_URL="$url" uv run pytest -m db "$@" ); }

# save_once FILE CMD...: runs CMD and keeps its stdout in FILE, refusing if FILE exists.
# Runbook evidence (a pre-apply dry run, a pre-check read) is compared later; re-running
# a step in a fresh session must not replace it with post-apply output (final re-review
# 2a-1 m1, m6). A failed or empty run leaves no FILE behind, so the step can be retried.
save_once() {
  [ -n "${1:-}" ] && [ "$#" -ge 2 ] || { echo "usage: save_once FILE CMD..." >&2; return 1; }
  local _dest="$1" _tmp="$1.partial.$$"
  shift
  [ ! -e "$_dest" ] || { echo "$_dest exists: not overwriting (delete it deliberately to redo)" >&2; return 1; }
  if "$@" > "$_tmp" && [ -s "$_tmp" ]; then
    mv "$_tmp" "$_dest" && cat "$_dest"
  else
    rm -f "$_tmp"; echo "save_once: command failed or printed nothing; $_dest not written" >&2; return 1
  fi
}

# Task 13 prod gates (final review 2a-2 C1/C2). A gate block checks one thing and, only if it
# holds, leaves a marker; the apply block after it refuses without that marker. So an agent
# that runs every block regardless still cannot apply past a failed gate, and a helper that
# is missing in a fresh shell fails (command not found) instead of passing vacuously.
GATE_DIR="${GATE_DIR:-../data/review/gates}"

_gate_name_ok() {
  case "${1:-}" in
    "" | *[!A-Za-z0-9_-]*) echo "gate name must be [A-Za-z0-9_-]+" >&2; return 1 ;;
  esac
}

gate_pass() {
  _gate_name_ok "${1:-}" || return 1
  mkdir -p "$GATE_DIR" && : > "$GATE_DIR/$1.ok" || return 1
  echo "GATE OK: $1"
}

gate_require() {
  _gate_name_ok "${1:-}" || return 1
  [ -f "$GATE_DIR/$1.ok" ] || { echo "gate $1 has not passed: STOP" >&2; return 1; }
  echo "gate $1 passed"
}

# same_ids BRANCH_CSV PROD_CSV COLUMN...: the two exports list the same keys (the COLUMNs
# joined by "-"), as multisets. A missing file or column, or an export with no rows, is a
# failure, never a match: two empty lists would otherwise compare equal (C2).
same_ids() {
  [ "$#" -ge 3 ] || { echo "usage: same_ids BRANCH_CSV PROD_CSV COLUMN..." >&2; return 1; }
  python3 -c '
import csv, sys
from collections import Counter
paths, cols = sys.argv[1:3], sys.argv[3:]
keys = []
for p in paths:
    try:
        with open(p, newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            missing = [c for c in cols if c not in (reader.fieldnames or [])]
            if missing:
                sys.exit(f"same_ids: {p} has no column {missing[0]}")
            k = Counter()
            for n, r in enumerate(reader, start=2):
                vals = [(r[c] or "").strip() for c in cols]
                blank = [c for c, v in zip(cols, vals) if not v]
                if blank:
                    sys.exit(f"same_ids: {p} line {n} has a blank {blank[0]}")
                k["-".join(vals)] += 1
    except OSError as e:
        sys.exit(f"same_ids: cannot read {p}: {e.strerror}")
    if not k:
        sys.exit(f"same_ids: {p} has no rows (an empty export is never a match: stop and ask the owner)")
    keys.append(k)
a, b = keys
if a != b:
    only_a, only_b = sorted((a - b).elements()), sorted((b - a).elements())
    sys.exit(f"same_ids: MISMATCH {sum(a.values())} vs {sum(b.values())} rows; only in {paths[0]}: "
             f"{len(only_a)} {only_a[:5]}; only in {paths[1]}: {len(only_b)} {only_b[:5]}")
print(f"{sum(a.values())} ids match")' "$@"
}

# Long applies (I5): an agent tool call is capped at 10 minutes, and R3/R5/R12 on Neon run
# longer. bg_start runs CMD (a helper such as ING) under nohup with its output in LOG, from
# backend/, inheriting the exported TARGET_HOST; bg_wait polls LOG for the exit line.
# A kill rolls the apply back (one transaction), so a run is never retried while one lives.
# The job leads its own process group (setsid via python3: macOS has no setsid(1)), so
# bg_wait can tell a dead nohup wrapper from a dead apply: uv and python stay in the group.
# setsid() is skipped when the wrapper is already a group leader (EPERM otherwise): a
# backgrounded job run from an interactive top-level shell (job control on) already gets
# its own pgid from the shell before this code runs.
bg_start() {
  [ -n "${1:-}" ] && [ "$#" -ge 2 ] || { echo "usage: bg_start LOG CMD..." >&2; return 1; }
  _need_target || return 1
  local _log="$1"
  shift
  [ -f scripts/runbook_helpers.sh ] || { echo "bg_start: run from backend/" >&2; return 1; }
  [ ! -e "$_log" ] || { echo "$_log exists: that run already started; bg_wait it (delete the log deliberately to redo)" >&2; return 1; }
  mkdir -p "$(dirname "$_log")" && : > "$_log" || return 1
  nohup python3 -c 'import os, sys
if os.getpgrp() != os.getpid():
    os.setsid()
os.execvp(sys.argv[1], sys.argv[1:])' bash -c '. scripts/runbook_helpers.sh || exit 1
SECONDS=0; "$@"; rc=$?
echo "RUNBOOK_ELAPSED=${SECONDS}s"; echo "RUNBOOK_EXIT=$rc"' bg_start "$@" >> "$_log" 2>&1 < /dev/null &
  echo "$!" > "$_log.pid"
  echo "started pid $!; log $_log; poll with: bg_wait $_log"
}

# Any repair or pipeline process on this machine, whichever log started it. BG_GUARD_PATTERN
# exists for the tests; the bracket keeps pgrep from matching its own command line.
_bg_live_repairs() {
  pgrep -fl "${BG_GUARD_PATTERN:-app[.]data[.]repair|app[.]pipelines}" 2>/dev/null
}

# bg_wait LOG [SECONDS]: polls (every BG_POLL seconds, default 10) up to SECONDS (default
# 540, under the tool cap). Returns 0 when CMD exited 0; 1 when it failed, or when its whole
# process group is gone with no exit line and no repair process is running (killed: rolled
# back); 2 while anything of the run may still be live (call bg_wait again, never rerun).
bg_wait() {
  [ -n "${1:-}" ] || { echo "usage: bg_wait LOG [SECONDS]" >&2; return 1; }
  local _log="$1" _limit="${2:-540}" _waited=0 _pid _live
  [ -f "$_log" ] || { echo "bg_wait: no $_log (bg_start never ran)" >&2; return 1; }
  _pid="$(cat "$_log.pid" 2>/dev/null)"
  while :; do
    if grep -q '^RUNBOOK_EXIT=' "$_log"; then
      tail -n 25 "$_log"
      grep -qx 'RUNBOOK_EXIT=0' "$_log" && { echo "bg_wait: $_log finished ok"; return 0; }
      echo "bg_wait: $_log FAILED" >&2
      return 1
    fi
    # kill -0 covers the moment before the wrapper's setsid, when the group does not exist yet.
    if [ -z "$_pid" ] || { ! kill -0 "$_pid" 2>/dev/null && ! pgrep -g "$_pid" >/dev/null 2>&1; }; then
      grep -q '^RUNBOOK_EXIT=' "$_log" && continue
      if _live="$(_bg_live_repairs)"; then
        printf '%s\n' "$_live" >&2
        echo "bg_wait: the run for $_log left no exit line, but a repair process above is still running: do not rerun; wait for it to exit, then bg_wait again" >&2
        return 2
      fi
      tail -n 25 "$_log"
      echo "bg_wait: process group ${_pid:-?} is gone with no exit line (killed): its transaction rolled back. Before any rerun, confirm no repair is running: pgrep -fl 'app[.]data[.]repair|app[.]pipelines' prints nothing" >&2
      return 1
    fi
    [ "$_waited" -lt "$_limit" ] || { echo "bg_wait: still running after ${_waited}s: run bg_wait $_log again" >&2; return 2; }
    sleep "${BG_POLL:-10}"
    _waited=$((_waited + ${BG_POLL:-10}))
  done
}
