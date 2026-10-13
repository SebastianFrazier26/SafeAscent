-- Run as the database owner before migration 0008. ltree is a trusted extension, but creating
-- it needs CREATE on the database, which migrator deliberately lacks.
\set ON_ERROR_STOP on
CREATE EXTENSION IF NOT EXISTS ltree;
\echo 'ltree present'
