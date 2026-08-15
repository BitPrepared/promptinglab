# DiarioBot — immagine unica stdlib-only per skill service e gateway.
# Il service `skill` usa il CMD di default; il service `gateway` (compose) lo
# override con `python3 -m diariobot.gateway`. Il tier statico è nginx
# (immagine ufficiale, non questa). Nessun pip install.
FROM python:3.11-slim

WORKDIR /app

# Nessun pip install: il core è stdlib-only. Include anche diariobot/web/.
COPY diariobot/ ./diariobot/

ENV PYTHONUNBUFFERED=1
ENV DIARIOBOT_BACKEND=mock
# Per il modello reale: DIARIOBOT_BACKEND=llama  e  LLAMA_URL=http://llama:8081
ENV DIARIOBOT_PORT=8080

EXPOSE 8080 8090

CMD ["python3", "-m", "diariobot.service"]
