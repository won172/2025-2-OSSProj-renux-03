#!/bin/sh
set -e

app_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
mkdir -p "$app_dir/artifacts"
# Avoid scanning and mutating every bind-mounted artifact on each start.
chown appuser:appuser "$app_dir" "$app_dir/artifacts" "$app_dir/rag_database.db" 2>/dev/null || true

# Resolve persisted recovery state as the same user that will read it at runtime.
# Only the child process environment changes; pointer files and indexes stay intact.
pointer_file="$(gosu appuser python "$app_dir/scripts/select_collection_pointer.py" --artifact-dir "$app_dir/artifacts")"
if [ -n "$pointer_file" ]; then
    export RAG_COLLECTION_POINTER_FILE="$pointer_file"
fi

exec gosu appuser "$@"
