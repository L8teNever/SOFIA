FROM python:3.12-slim

WORKDIR /app

# Deliberately isolated from the main app's image: the mcp package needs a
# newer Starlette than backend/requirements.txt is pinned to, so this runs
# as its own container with its own dependency set entirely.
COPY mcp_server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY mcp_server/ ./mcp_server/

EXPOSE 8100

CMD ["uvicorn", "mcp_server.server:app", "--host", "0.0.0.0", "--port", "8100"]
