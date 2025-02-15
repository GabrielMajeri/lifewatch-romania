#!/usr/bin/env bash

# PSQL='psql'
PSQL='env DOCKER_CLI_HINTS=false docker exec -it lifewatch-database-1 psql -U postgres'

$PSQL --host=localhost --port=5432 --username=dev --no-password --dbname=lifewatch
