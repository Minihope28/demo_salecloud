FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data

# Tesseract (OCR) : lecture des PDF scannés ou dont le texte interne est mal encodé
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-fra \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY config ./config
COPY samples ./samples

RUN useradd --create-home --uid 10001 dossier \
    && mkdir -p /data && chown dossier /data
USER dossier

EXPOSE 8000
# Derrière le reverse proxy HTTPS de Volvo : --proxy-headers pour les URL de redirection correctes.
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
