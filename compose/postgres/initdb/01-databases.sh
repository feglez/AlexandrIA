#!/bin/sh
# Runs once, when the PostgreSQL data volume is empty.
# Creates one role and one database per component. Each role owns its own
# database and nobody else can connect to it.
set -eu

create_database() {
    psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
        -v name="$1" -v password="$2" <<'SQL'
CREATE ROLE :"name" LOGIN PASSWORD :'password';
CREATE DATABASE :"name" OWNER :"name";
REVOKE ALL ON DATABASE :"name" FROM PUBLIC;
SQL
}

create_database nextcloud "$NEXTCLOUD_DB_PASSWORD"
create_database watchman "$WATCHMAN_DB_PASSWORD"
create_database librarian "$LIBRARIAN_DB_PASSWORD"
create_database observer "$OBSERVER_DB_PASSWORD"

# pgvector has to be created by a superuser.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname librarian \
    -c 'CREATE EXTENSION IF NOT EXISTS vector;'
