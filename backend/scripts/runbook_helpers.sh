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

_need_target() {
  case "${TARGET_HOST:-}" in
    "") echo "TARGET_HOST is unset: export a Neon branch host, or TARGET_HOST=production for prod" >&2; return 1 ;;
    production) return 0 ;;
    *[!A-Za-z0-9.-]*) echo "TARGET_HOST must be a bare host name" >&2; return 1 ;;
  esac
}

# Prints $1 with its host replaced by TARGET_HOST (unchanged for production). Refuses an
# empty URL, or one with no host/path boundary to rewrite, rather than falling through to
# the env file's (prod) host.
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
  split_pg_url "$url"; verify_full_url
  [ -n "$PG_URL_NOPASS" ] || { echo "OWNER_PSQL: empty URL after split" >&2; exit 1; }
  psql "$PG_URL_NOPASS" -X "$@" ); }

ANALYST_PSQL() { ( _need_target && _load ./.env.analyst || exit 1
  url="$(_retarget "${ANALYST_DATABASE_URL:-}")" || exit 1
  split_pg_url "$url"; verify_full_url
  [ -n "$PG_URL_NOPASS" ] || { echo "ANALYST_PSQL: empty URL after split" >&2; exit 1; }
  psql "$PG_URL_NOPASS" -X "$@" ); }

VERIFY() { ( _need_target && _load ./.env.analyst || exit 1
  url="$(_retarget "${ANALYST_DATABASE_URL:-}")" || exit 1
  VERIFY_DATABASE_URL="$url" uv run pytest -m db "$@" ); }
