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
