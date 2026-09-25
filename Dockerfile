FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data

WORKDIR /app
COPY requirements.txt .
# OCR intégré via pip (RapidOCR). Sur serveur, OpenCV « headless » évite toute bibliothèque graphique système.
RUN pip install --no-cache-dir -r requirements.txt \
    && pip uninstall -y opencv-python \
    && pip install --no-cache-dir opencv-python-headless

COPY app ./app
COPY config ./config
COPY samples ./samples

RUN useradd --create-home --uid 10001 dossier \
    && mkdir -p /data && chown dossier /data
USER dossier

EXPOSE 8000
# Derrière le reverse proxy HTTPS de Volvo : --proxy-headers pour les URL de redirection correctes.
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
