#!/bin/sh
set -eu

psql \
    --username "$POSTGRES_USER" \
    --dbname "$POSTGRES_DB" \
    --set=test_db="$POSTGRES_TEST_DB" \
    --set=ON_ERROR_STOP=1 <<'SQL'
SELECT format('CREATE DATABASE %I', :'test_db')
WHERE NOT EXISTS (
    SELECT 1
    FROM pg_database
    WHERE datname = :'test_db'
)
\gexec
SQL
