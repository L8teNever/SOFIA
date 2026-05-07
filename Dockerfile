FROM python:3.12-slim

WORKDIR /app

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