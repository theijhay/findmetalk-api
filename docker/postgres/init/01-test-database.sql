-- Runs once, when the local Postgres volume is first created.
-- Integration tests use their own database so they never touch dev data.
CREATE DATABASE findmetalk_test OWNER findmetalk;
