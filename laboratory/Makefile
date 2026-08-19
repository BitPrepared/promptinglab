# Makefile — laboratorio web.
# Comandi Docker che "fanno quello che serve", con i dettagli annosi già gestiti:
#  - i profili `model` e `coder` (senza, i container llama sopravvivono e
#    lasciano reti stale)
#  - i GGUF da montare (MODEL_FILE, CODER_FILE)
#  - la pulizia delle reti dangling (risolve i "network ... not found")
#
# Uso tipico:
#   make up          un comando, due llama (main + coder) e due laboratori
#                    [DEFAULT; richiede i GGUF in models/, vedi README]
#   make demo        avvia in modalità demo (mock, senza modelli)
#   make rebuild     ricostruisce le immagini e riparte coi modelli
#   make down        ferma tutto (pulendo anche i llama)
#   make help        tutti i target

# --- Variabili (sovrascrivibili: make up MODEL_FILE=... ) ---------------------
# 1.5b = target mini PC (qualità migliore); 0.5b più leggero. Cambia con MODEL_FILE=.
MODEL_FILE ?= qwen2.5-1.5b-instruct-q4_k_m.gguf
CODER_FILE ?= qwen2.5-coder-1.5b-instruct-q4_k_m.gguf
COMPOSE     := docker compose
# Un solo comando (design laboratorio-code, D7): `up` attiva ENTRAMBI i
# profili — il main delle tappe ①–④ e il coder del laboratorio codice.
# Il Pi 3 non attiva il coder: pi-up dichiara il profilo da solo, sotto.
PROFILE     := --profile model --profile coder
PROFILE_PI  := --profile model
URL     ?= http://localhost:8090
N       ?= 8
TURNS   ?= 4
RETRY   ?= 0

.DEFAULT_GOAL := help
.PHONY: help up pi-up demo down rebuild logs admin open status ps test clean clean-sessions loadtest pull

help:  ## mostra questo aiuto
	@echo "Laboratorio web — target disponibili:"
	@echo "  make up        UN comando, DUE llama: main (tappe ①–④) + coder (laboratorio codice)"
	@echo "                 [richiede MODEL_FILE=$(MODEL_FILE) e CODER_FILE=$(CODER_FILE) in models/]"
	@echo "  make demo      avvia in DEMO (mock, senza modello — chat ①–④ off, skill on)"
	@echo "  make pi-up     come up ma per host Pi 3 (0.5B obbligatorio, SENZA coder)"
	@echo "  make rebuild   ricostruisce le immagini (codice Python) e riparte coi modelli"
	@echo "  make down      ferma e rimuove tutti i container (anche i due llama)"
	@echo "  make clean-sessions  azzera lo storico delle sessioni (ferma il gateway)"
	@echo "  make loadtest   simula N ragazzi in chat (N=8 TURNS=4 URL=...)"
	@echo "  make logs      tail dei log di tutti i service (segui un utente: grep '#<cid>')"
	@echo "  make admin     URL del pannello educatore (sessions, finestra/filtro IP, allowlist codice)"
	@echo "  make open      URL della pagina laboratorio"
	@echo "  make ps        stato dei container"
	@echo "  make status    probe: modello attivo + n. utenti (/api/model-status)"
	@echo "  make test      suite di test (python unittest)"
	@echo "  make clean     ferma + pulisce container orfani e reti dangling"
	@echo "  make pull      scarica l'immagine llama.cpp"
	@echo ""
	@echo "Variabili:"
	@echo "  MODEL_FILE=qwen2.5-0.5b-instruct-q4_k_m.gguf   cambia GGUF main (0.5b/1.5b)"
	@echo "  CODER_FILE=...                                 cambia GGUF del coder"

up:  ## avvia con ENTRAMBI i modelli (main + coder; richiede i GGUF in models/)
	MODEL_FILE=$(MODEL_FILE) CODER_FILE=$(CODER_FILE) $(COMPOSE) $(PROFILE) up

pi-up:  ## avvia col modello per host Pi 3 (0.5B obbligatorio, vedi docker-compose.pi.yml; niente coder)
	$(COMPOSE) -f docker-compose.yml -f docker-compose.pi.yml $(PROFILE_PI) up

demo:  ## avvia in demo (mock, senza modelli)
	$(COMPOSE) up web gateway skill

down:  ## ferma e rimuove tutti i container (sempre coi profili -> pulisce anche i llama)
	$(COMPOSE) $(PROFILE) down --remove-orphans

rebuild: clean  ## ricostruisce le immagini e riparte coi modelli
	MODEL_FILE=$(MODEL_FILE) CODER_FILE=$(CODER_FILE) $(COMPOSE) $(PROFILE) up --build

logs:  ## tail dei log (-f)
	$(COMPOSE) logs -f --tail=50

ps:  ## stato dei container
	$(COMPOSE) ps

status:  ## probe model-status (modello attivo?)
	@curl -s http://localhost:8090/api/model-status; echo

admin:  ## URL del pannello educatore (sessions, finestra/filtro IP, allowlist codice)
	@echo "Pannello educatore:  http://localhost:8090/admin"
	@echo "Laboratorio codice:  http://localhost:8090/code.html (postazioni abilitate da /admin)"
	@echo "Sessions (JSON):     curl -s localhost:8090/api/sessions"

open:  ## URL della pagina laboratorio
	@echo "Pagina laboratorio:  http://localhost:8090"

test:  ## suite di test
	python3 -m unittest discover -s tests

loadtest:  ## simula N ragazzi in chat (N=8 TURNS=4 RETRY=0 URL=http://localhost:8090)
	python3 -m loadtest --url $(URL) --n $(N) --turns $(TURNS) --retry $(RETRY)

clean:  ## ferma tutto + pulisce orfani e reti dangling (risolve 'network not found')
	-$(COMPOSE) $(PROFILE) down --remove-orphans
	docker network prune -f

clean-sessions:  ## azzera lo storico delle sessioni (ferma il gateway, rimuove il DB)
	-$(COMPOSE) stop gateway
	rm -f sessions/sessions.db
	@echo "Archivio sessioni azzerato. Riparti con: make up"

pull:  ## scarica l'immagine llama.cpp (server CPU)
	$(COMPOSE) $(PROFILE) pull
