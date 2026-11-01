#!/bin/sh
set -eu

if [ "${1:-}" = "migrate" ]; then
    # Wait for postgres to be ready
    for i in 1 2 3 4 5 6 7 8 9 10; do
        if python -c "import socket; socket.create_connection(('postgres', 5432), timeout=2)" 2>/dev/null; then
            echo "Postgres is ready"
            break
        fi
        echo "Waiting for postgres... attempt $i/10"
        sleep 1
    done
    
    alembic upgrade head
    alembic -n logs upgrade head
    exit 0
fi

exec "$@"
