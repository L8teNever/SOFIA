@echo off
echo Starting Sofia...

if not exist ".env" (
    copy .env.example .env
    echo Created .env from .env.example - please fill in the values!
    pause
)

pip install -r requirements.txt --quiet

echo.
echo Starting server on http://localhost:8000
echo.
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
