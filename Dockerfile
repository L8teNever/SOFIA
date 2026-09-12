FROM python:3.12-slim

WORKDIR /app

# German system time everywhere the app runs, regardless of the host —
# python-slim ships without zoneinfo data, so TZ alone does nothing until
# tzdata is installed and /etc/localtime points at it.
ENV TZ=Europe/Berlin
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && \
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

EXPOSE 8000

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]