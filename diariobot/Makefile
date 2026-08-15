# Makefile — laboratorio web (diariobot).
# Comandi Docker che "fanno quello che serve", con i dettagli annosi già gestiti:
#  - il profilo `model` (senza, il container llama sopravvive e lascia reti stale)
#  - il GGUF da montare (MODEL_FILE)
#  - la pulizia delle reti dangling (risolve i "network ... not found")
#
# Uso tipico:
#   make up          avvia col modello (richiede il GGUF in models/)  [DEFAULT]
#   make demo        avvia in modalità demo (mock, senza modello)
#   make rebuild     ricostruisce le immagini e riparte col modello
#   make down        ferma tutto (pulendo anche llama)
#   make help        tutti i target

# --- Variabili (sovrascrivibili: make up MODEL_FILE=... ) ---------------------
# 1.5b = target mini PC (qualità migliore); 0.5b più leggero. Cambia con MODEL_FILE=.
MODEL_FILE ?= qwen2.5-1.5b-instruct-q4_k_m.gguf
COMPOSE     := docker compose
PROFILE     := --profile model

.DEFAULT_GOAL := help
.PHONY: help up pi-up demo down rebuild logs admin open status ps test clean pull

help:  ## mostra questo aiuto
	@echo "Laboratorio web — target disponibili:"
	@echo "  make up        avvia col MODELLO (MODEL_FILE=$(MODEL_FILE))  [default con modello]"
	@echo "  make demo      avvia in DEMO (mock, senza modello — chat 1/2/4 off, skill on)"
	@echo "  make pi-up     come up, ma per host Pi 3 (override: 0.5B obbligatorio)"
	@echo "  make rebuild   ricostruisce le immagini (codice Python) e riparte col modello"
	@echo "  make down      ferma e rimuove tutti i container (anche llama)"
	@echo "  make logs      tail dei log di tutti i service (segui un utente: grep '#<cid>')"
	@echo "  make admin     URL del pannello educatore (sessions, solo metadati)"
	@echo "  make open      URL della pagina laboratorio"
	@echo "  make ps        stato dei container"
	@echo "  make status    probe: modello attivo + n. utenti (/api/model-status)"
	@echo "  make test      suite di test (python unittest)"
	@echo "  make clean     ferma + pulisce container orfani e reti dangling"
	@echo "  make pull      scarica l'immagine llama.cpp"
	@echo ""
	@echo "Variabili:"
	@echo "  MODEL_FILE=qwen2.5-0.5b-instruct-q4_k_m.gguf   cambia GGUF (0.5b/1.5b)"

up:  ## avvia col modello (richiede il GGUF in models/)
	MODEL_FILE=$(MODEL_FILE) $(COMPOSE) $(PROFILE) up

pi-up:  ## avvia col modello per host Pi 3 (0.5B obbligatorio, vedi docker-compose.pi.yml)
	$(COMPOSE) -f docker-compose.yml -f docker-compose.pi.yml $(PROFILE) up

demo:  ## avvia in demo (mock, senza modello)
	$(COMPOSE) up web gateway skill

down:  ## ferma e rimuove tutti i container (sempre col profilo -> pulisce anche llama)
	$(COMPOSE) $(PROFILE) down --remove-orphans

rebuild: clean  ## ricostruisce le immagini e riparte col modello
	MODEL_FILE=$(MODEL_FILE) $(COMPOSE) $(PROFILE) up --build

logs:  ## tail dei log (-f)
	$(COMPOSE) logs -f --tail=50

ps:  ## stato dei container
	$(COMPOSE) ps

status:  ## probe model-status (modello attivo?)
	@curl -s http://localhost:8090/api/model-status; echo

admin:  ## URL del pannello educatore (sessions, solo metadati)
	@echo "Pannello educatore:  http://localhost:8090/admin"
	@echo "Sessions (JSON):     curl -s localhost:8090/api/sessions"

open:  ## URL della pagina laboratorio
	@echo "Pagina laboratorio:  http://localhost:8090"

test:  ## suite di test
	python3 -m unittest discover -s tests

clean:  ## ferma tutto + pulisce orfani e reti dangling (risolve 'network not found')
	-$(COMPOSE) $(PROFILE) down --remove-orphans
	docker network prune -f

pull:  ## scarica l'immagine llama.cpp (server CPU)
	$(COMPOSE) $(PROFILE) pull
