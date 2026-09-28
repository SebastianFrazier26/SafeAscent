import pytest

from scripts.sanitize_schema_dump import DumpRejected, sanitize

RAW = """--
-- PostgreSQL database dump
--

\\restrict abc123

-- Dumped from database version 17.5
-- Dumped by pg_dump version 17.6

SET statement_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SELECT pg_catalog.set_config('search_path', '', false);
SET default_tablespace = '';

CREATE SCHEMA public;
COMMENT ON SCHEMA public IS 'standard public schema';
CREATE EXTENSION IF NOT EXISTS postgis WITH SCHEMA public;
COMMENT ON EXTENSION postgis IS 'PostGIS geometry and geography spatial types';


CREATE TABLE public.accidents (
    accident_id integer NOT NULL,
    note text DEFAULT 'SET x = 1;'::text
);

CREATE FUNCTION public.set_coords() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  SET LOCAL search_path = public;
  RETURN NEW;
END $$;

\\unrestrict abc123
"""


def test_strips_psql_meta_commands_and_session_settings():
    out = sanitize(RAW)
    assert "\\restrict" not in out
    assert "\\unrestrict" not in out
    assert "SET statement_timeout" not in out
    assert "SET transaction_timeout" not in out
    assert "set_config('search_path'" not in out
    assert "SET default_tablespace" not in out


def test_strips_schema_and_extension_statements():
    out = sanitize(RAW)
    assert "CREATE SCHEMA public;" not in out
    assert "COMMENT ON SCHEMA public" not in out
    assert "CREATE EXTENSION" not in out
    assert "COMMENT ON EXTENSION" not in out


def test_keeps_ddl_and_indented_set_inside_function_bodies():
    out = sanitize(RAW)
    assert "CREATE TABLE public.accidents (" in out
    assert "'SET x = 1;'::text" in out
    assert "  SET LOCAL search_path = public;" in out


def test_collapses_blank_runs():
    assert "\n\n\n" not in sanitize(RAW)


@pytest.mark.parametrize(
    "line",
    [
        "COPY public.accidents (accident_id) FROM stdin;",
        "INSERT INTO public.accidents VALUES (1);",
        "ALTER TABLE public.accidents OWNER TO neondb_owner;",
        "GRANT SELECT ON TABLE public.accidents TO analyst;",
        "CREATE ROLE x PASSWORD 'y';",
    ],
)
def test_rejects_data_owners_grants_and_passwords(line):
    with pytest.raises(DumpRejected):
        sanitize(RAW + line + "\n")
