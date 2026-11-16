-- Phase 3 (D13/DP17): trainer gains LOGIN for the training job, run from the owner's Mac (.env.trainer; never a
-- GitHub secret). Run once as the owner that ran create_roles_phase2.sql, then grants_phase3.sql. The password
-- reaches psql only via \getenv, never argv, and the server as plaintext over verify-full TLS (stored SCRAM).
-- Run with psql -X; never with -L (the session log records statements with the password in them).
\set ON_ERROR_STOP on
\set VERBOSITY terse
\set SHOW_CONTEXT never

-- Only the guards before the password-shape check are secret-free, so a refused rerun or a missing
-- role never sends the password, and no failing ALTER ROLE ... PASSWORD lands in the server log
-- via log_min_error_statement.
SELECT to_regrole('trainer') IS NOT NULL AS trainer_exists \gset
\if :trainer_exists
\else
  DO $$ BEGIN RAISE EXCEPTION 'run create_roles_phase2.sql first (trainer must exist)'; END $$;
\endif
SELECT rolcanlogin AS already FROM pg_roles WHERE rolname = 'trainer' \gset
\if :already
  DO $$ BEGIN RAISE EXCEPTION 'trainer can already log in; do not rerun trainer_login_phase3.sql'; END $$;
\endif
SELECT pg_has_role(current_user, 'trainer', 'USAGE WITH ADMIN OPTION') AS can_alter_trainer \gset
\if :can_alter_trainer
\else
  DO $$ BEGIN RAISE EXCEPTION 'run as the owner that created trainer (it holds ADMIN on trainer)'; END $$;
\endif

-- -e/-a/-b would echo every statement after this point with the password interpolated.
\set ECHO none
\set ECHO_HIDDEN off
\getenv trainer_password TRAINER_PASSWORD
\if :{?trainer_password}
\else
  DO $$ BEGIN RAISE EXCEPTION 'TRAINER_PASSWORD is not set'; END $$;
\endif

-- A value shaped like a SCRAM or md5 hash is stored as that hash (Neon rejects it at COMMIT);
-- write_role_url --generate-password makes 64 hex chars.
SELECT length(:'trainer_password') >= 32
   AND :'trainer_password' !~ '^SCRAM-SHA-256\$'
   AND :'trainer_password' !~ '^md5[0-9a-f]{32}$' AS password_ok \gset
\if :password_ok
\else
  DO $$ BEGIN RAISE EXCEPTION 'TRAINER_PASSWORD must be a plaintext password of at least 32 characters, not a SCRAM or md5 verifier'; END $$;
\endif

ALTER ROLE trainer LOGIN CONNECTION LIMIT 4 PASSWORD :'trainer_password';
\echo 'trainer can log in'
