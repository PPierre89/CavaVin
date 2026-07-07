# Contexte de build = racine du repo (pour inclure frontend/ ET backend/).

# --- Étape 1 : build du SPA React (Vite) ---
FROM node:22-alpine AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- Étape 2 : runtime Django ---
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./

# SPA buildé servi par WhiteNoise (WHITENOISE_ROOT) ; l'index view renvoie spa/index.html.
COPY --from=frontend /frontend/dist ./spa

EXPOSE 8000

# Le Container Manager du NAS (et docker ps) voit si l'app répond vraiment.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/', timeout=4)"

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
