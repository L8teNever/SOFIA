# Sofia – Digitaler Schulbegleiter

Eine Progressive Web App (PWA) fuer Schulklassen mit FastAPI-Backend.

## Features

- **Kalender** – Klassenarbeiten, Ausfluge, Termine
- **Hausaufgaben** – Eintragen, abhaken, Erinnerungen
- **Noten** – Private Notenverwaltung mit Durchschnitt
- **Stundenplan** – WebUntis-Integration
- **Chat** – Echtzeit-Chat mit Sprachnachrichten
- **QuickShare** – Temporaeres Datei-Teilen
- **Push-Benachrichtigungen** – Erinnerungen per Browser-Push
- **PWA** – Installierbar auf Mobil & Desktop
- **Cloudflare Zero Trust** – Zugang nur fuer freigeschaltete Nutzer

## Schnellstart (Docker)

`ash
# 1. .env anlegen
cp .env.example .env
# Werte in .env eintragen (mind. SUPER_ADMIN_EMAIL)

# 2. Starten
docker compose up -d

# App laeuft auf http://localhost:8000
`

Das Docker-Image wird automatisch von GitHub Container Registry geladen:
ghcr.io/l8tenever/sofia:latest

## Lokale Entwicklung

`ash
pip install -r requirements.txt
cp .env.example .env
# DEV_EMAIL in .env setzen (ueberspringt Cloudflare-Auth)
uvicorn backend.main:app --reload --port 8000
`

## Umgebungsvariablen (.env)

| Variable | Beschreibung |
|---|---|
| SUPER_ADMIN_EMAIL | E-Mail des Super-Admins |
| DEV_EMAIL | Lokale Entwicklung: feste Test-E-Mail |
| VAPID_PRIVATE_KEY | Push-Benachrichtigungen (privater Key) |
| VAPID_PUBLIC_KEY | Push-Benachrichtigungen (oeffentlicher Key) |
| ENCRYPTION_KEY | Fernet-Key fuer Untis-Passwoerter |
| SECRET_KEY | Interner App-Secret |

## Cloudflare Zero Trust

1. Tunnel auf Port 8000 zeigen lassen
2. Access Policy fuer zugelassene E-Mails einrichten
3. DEV_EMAIL aus .env entfernen

## GitHub Actions

Bei jedem Push auf main wird automatisch ein Docker-Image gebaut und in die GitHub Container Registry gepusht (ghcr.io/l8tenever/sofia:latest).