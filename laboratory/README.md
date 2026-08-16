# Laboratorio di Prompting — Skill "Diario di Bordo"

Skill LLM **one-shot** che trasforma gli appunti grezzi di una squadriglia in uno
**scaffold strutturato** del diario di bordo (riempito **solo con i fatti forniti
dall'utente**), più 2–3 domande di approfondimento e un check di chiarezza.

**Non genera prosa**: la scrittura finale resta al ragazzo.
Principio educativo (Flusso Campo 2026 §3.4): **"IA = supporto, non sostituto"**.

Contratto e decisioni: vedi `openspec/changes/skill-diario-di-bordo/`.

## Architettura (zero-dipendenze)

```
appunti ──▶ DiarioSkill ──▶ [ModelBackend: adapter scambiabile] ──▶ validate ──▶ SkillOutput
                          ├─ MockModel      (deterministico, per test/demo offline)
                          └─ LlamaServerModel (modello reale via llama-server HTTP)
```

Il core usa **solo la libreria standard** Python (nessun framework, nessuna
dipendenza da installare). Il modello pesante (llama.cpp) vive dietro l'adapter:
questo rende tutta la logica testabile offline e il modello "swappabile"
(modello base → fine-tuned senza toccare la skill).

## Avvio rapido (offline, con backend mock)

```bash
cd laboratory
python3 -m backend.service              # servizio HTTP su :8080, backend mock
# in un altro terminale:
python3 -m backend.cli "Oggi con Marco e Lucia al campo. Abbiamo montato la tenda."
```

Nessuna installazione richiesta: il mock rispetta il contratto (solo fatti
dell'utente, niente prosa) e permette di sviluppare e demoare tutta la catena
senza modello.

> **Backend `auto`** (default nel `docker-compose`): `LAB_BACKEND=auto` usa
> `llama-server` se raggiungibile, altrimenti il mock — in modo **lazy e
> ritentabile** (a ogni generazione, finché llama non risponde, poi ci resta): non
> rimane incollato al mock se llama parte dopo il primo scaffold. Così la stessa
> immagine gira in demo (senza modello) o in reale (con modello) senza cambiare
> config. Valori: `mock` | `llama` | `local` | `auto`.

## Avvio con modello reale (verificato end-to-end, vedi `spike/REPORT.md`)

```bash
# 1) Scarica un GGUF piccolo, es. Qwen2.5-1.5B-Instruct Q4_K_M (target mini PC)
#    o Qwen2.5-0.5B-Instruct Q4_K_M (host Pi 3) da HuggingFace.
# 2) Avvia llama-server (la grammatica arriva per-request dall'adapter, non serve
#    --grammar-file):
llama-server -m <modello>.gguf --host 0.0.0.0 --port 8081 -t 4 -c 2048 --temp 0.2
# 3) Puntaci la skill:
export LAB_BACKEND=llama
export LLAMA_URL=http://localhost:8081
python3 -m backend.service
```

L'adapter invia di default `repeat_penalty=1.1` (tunabile via `LLAMA_REPEAT_PENALTY`):
**necessario** con il modello base — senza, il modello loopa sugli array della
grammatica e satura `max_tokens` senza chiudere il JSON; con ~1.1 il 1.5B base
termina naturalmente producendo uno scaffold valido.

> ⚠️ **`http://<host>:8081` è l'endpoint OpenAI-compat del modello GREZZO**
> (Qwen libero, niente grammatica né prompt scout): ragiona come ChatGPT e può
> loopare. **Non è la skill.** È pubblicato in LAN come **decisione dichiarata**
> per *consumer fidati* (CLI, debug, sviluppo): i parametri arrivano così come
> inviati, nessuna normalizzazione. La pagina del laboratorio usa solo il
> **gateway** (`:8090/api/*`), dove vivono normalizzazione e osservabilità.
> Per il diario di bordo usa la CLI/`service` della skill (`:8080`), che
> vincola l'output a JSON strutturato. La skill è un estrattore **one-shot di
> appunti**: incolla i fatti grezzi della giornata, non domande (una domanda o
> un input vuoto → fallback guidato, non un scaffold).

Vedi `docker-compose.yml` per la versione containerizzata. Topologia a 3 tier:
nginx (statici + proxy `/api/*`) → gateway (`backend.gateway`) → skill/llama.
L'host è sostituibile: **mini PC** (modello 1.5B, `make up`) **oppure Pi 3 /
1 GB** (0.5B obbligatorio, `make pi-up` — override `docker-compose.pi.yml`);
i client cambiano solo l'indirizzo. `spike/REPORT.md` ha i numeri di
fattibilità misurati (tok/s, RAM).

## Test

```bash
cd laboratory
python3 -m unittest discover -s tests
```

## Contratto del servizio (per `laboratorio-web`)

`POST /scaffold` body `{"notes": "..."}` → `200` con `SkillOutput` in JSON
(`scaffold`, `questions`, `checks`, `inferences`, `message`). `GET /health` →
`{"ok": true}`. `message` (opzionale) è un messaggio di UI/onboarding: presente
quando l'input non sono appunti (domanda colloquiale, input vuoto) e lo scaffold
è vuoto — il client lo mostra al posto dello scaffold. Mai la chain-of-thought.

## FASE 2 — Fine-tune (enhancement swappabile, NON blocca il campo)

Vedi `openspec/changes/skill-diario-di-bordo/tasks.md` gruppo 4: dataset
(golden + distillazione sintetica) → Unsloth/QLoRA su Colab → GGUF → drop-in
via adapter. Il modello fine-tuned sostituisce il base senza modificare la skill.
