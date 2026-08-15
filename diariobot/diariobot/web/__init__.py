"""Laboratorio web — tier di presentazione (§3.4).

File statici puri (`static/`: pagina wizard + pannello educatore) serviti da
nginx, e il client condiviso pagina/CLI (`client.py`). La business logic vive
nel **gateway** (`diariobot.gateway`): proxy alla skill, chat normalizzata,
model-status, osservabilità. Il modello (llama-server) sta dietro il gateway
per il percorso pagina (`/api/*`, same-origin via reverse proxy nginx) ed è
raggiungibile direttamente in LAN solo dai consumer fidati (CLI, debug).

Zero dipendenze: solo libreria standard, coerente col resto del progetto.
"""
