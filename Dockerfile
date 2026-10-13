FROM python:3.12-slim

WORKDIR /app

# German system time everywhere the app runs, regardless of the host —
# python-slim ships without zoneinfo data, so TZ alone does nothing until
# tzdata is installed and /etc/localtime points at it.
ENV TZ=Europe/Berlin
# hunspell: Rechtschreibpruefung fuer sofianotes
RUN apt-get update && apt-get install -y --no-install-recommends tzdata hunspell hunspell-de-de && \
    ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone && \
    rm -rf /var/lib/apt/lists/*

# Install dependencies first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app source
COPY backend/ ./backend/
COPY pages/ ./pages/
COPY static/ ./static/

# Uploads dir (overridden by volume at runtime)
RUN mkdir -p uploads

# ---- sofianotes: zweite Seite im selben Dienst (Port 8001) ----
# eigene Python-Umgebung, weil sofianotes andere FastAPI-/Starlette-Versionen braucht
COPY notes/backend/requirements.txt ./notes/backend/requirements.txt
RUN python -m venv /opt/notes-venv && /opt/notes-venv/bin/pip install --no-cache-dir -r notes/backend/requirements.txt
COPY notes/ ./notes/
# Version/Commit fuer die Anzeige in sofianotes (vom Workflow gesetzt)
ARG GIT_COMMIT=
ARG NOTES_VERSION=
RUN GIT_COMMIT="$GIT_COMMIT" APP_VERSION="$NOTES_VERSION" python3 notes/scripts/bake-version.py && mkdir -p notes/data

COPY start.sh ./start.sh

EXPOSE 8000 8001

CMD ["sh", "/app/start.sh"]