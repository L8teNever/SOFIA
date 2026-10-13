#!/bin/sh
# Ein Dienst, zwei Seiten: Sofia (Port 8000) und sofianotes (Port 8001) im selben Container.
# sofianotes liest Sofias Daten direkt mit: dieselbe Maschine (127.0.0.1) und den
# Dienst-Schluessel aus Sofias Datenordner - nichts extra einzurichten.

if [ "${NOTES_ENABLED:-1}" != "0" ]; then
  (
    export SOFIA_API_BASE="${SOFIA_API_BASE:-http://127.0.0.1:8000/api/v1}"
    export SOFIA_TOKEN_FILE="${SOFIA_TOKEN_FILE:-/app/data/internal_service_token.txt}"
    cd /app/notes/backend
    # faellt sofianotes aus, startet es nach kurzer Pause neu (Sofia laeuft weiter)
    while true; do
      /opt/notes-venv/bin/uvicorn app.main:app --host 0.0.0.0 --port "${NOTES_PORT:-8001}"
      echo "sofianotes beendet - Neustart in 3 s" >&2
      sleep 3
    done
  ) &
fi

exec uvicorn backend.main:app --host 0.0.0.0 --port 8000
