#!/bin/sh
set -e

mkdir -p /app/artifacts
chown -R appuser:appuser /app/artifacts

exec gosu appuser "$@"
