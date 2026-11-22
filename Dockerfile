# Bauanleitung für das Home-Assistant-Add-on. Der Supervisor baut das Bild auf dem NUC selbst.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    VINTER_DATEN=/data \
    VINTER_ANMELDUNG=passwort \
    HF_HOME=/data/cache/huggingface

WORKDIR /app
COPY requirements.txt .
# PyTorch ohne Grafikkarten-Teile (viel kleiner), danach der Rest.
RUN pip install --index-url https://download.pytorch.org/whl/cpu "torch==2.15.0" \
 && pip install -r requirements.txt

COPY app ./app
COPY vorlagen ./vorlagen
COPY run.sh /run.sh
RUN sed -i 's/\r$//' /run.sh && chmod a+x /run.sh

EXPOSE 8099
CMD ["/run.sh"]
