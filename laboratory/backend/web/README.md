# Laboratorio Web — topologia a 3 tier (§3.4)

Consumer della capability `diario-di-bordo`. L'host (mini PC **oppure**
Raspberry Pi 3 / 1 GB come host alternativo) ospita tre tier con responsabilità
nette; i client (browser kiosk o CLI) sono **sottili**, su **LAN cablata offline**.

```
              host: mini PC oppure Pi 3 (IP fisso, LAN cablata)
 ┌───────────────────────────────────────────────────────────────────┐
 │  tier 1  nginx :8090 — statici puri (index/admin)                 │
 │            └──/api/*──▶ tier 2  gateway :8090 (interno)           │
 │                              ├──▶ skill :8080 (tappa 3)           │
 │                              └──▶ llama :8081 (chat 1/2/4,        │
 │                                   model-status)                   │
 │  tier 3  llama :8081 (anche pubblicata — consumer fidati)         │
 └───────────────────────────────────────────────────────────────────┘
        ▲                              ▲
        │ browser kiosk (:8090)        │ consumer fidati: CLI/debug (:8081)
   ┌────┴──────────────┐          ┌────┴─────────────┐
   │ Raspberry Pi 3/1GB │          │ Raspberry Pi 3    │
   │ (pagina, parità)   │          │ CLI --remote      │
   └────────────────────┘          │ (:8080, skill)    │
                                   └───────────────────┘
```

**Ownership degli endpoint:**

| Tier        | Possiede                                                            |
| ----------- | -------------------------------------------------------------------- |
| nginx       | file statici (`/`, `/admin`, favicon) — zero logica, zero parametri  |
| **gateway** | normalizzazione chat (clamp temp/token, repeat_penalty, max turni), proxy `/api/scaffold`, `/api/model-status`, osservabilità (`/api/sessions`) |
| llama       | solo inferenza (API compatibile OpenAI)                               |
| skill       | contratto `diario-di-bordo` (invariato, import-indipendente dal gateway) |

**llama in LAN (decisione dichiarata):** la porta `8081` è pubblicata per
**consumer fidati** (CLI, debug, sviluppo) che parlano OpenAI-compat diretto,
con i parametri così come inviati — nessuna normalizzazione. Non è un vincolo
di rete ma una **convenzione dell'applicazione**: la pagina del laboratorio usa
solo il gateway (`:8090/api/*` via nginx, same-origin), dove vivono
normalizzazione e osservabilità. Chi bypassa il gateway le perde — ed è una
sua scelta da consumer fidato. (LAN offline fidata: niente auth.)

### Endpoint (contratto invariato per la pagina)

| Metodo+path            | Owner/servizio     | Uso                           |
| ---------------------- | ------------------ | ----------------------------- |
| `GET /`                | nginx (static)     | pagina wizard §3.4            |
| `GET /admin`           | nginx (static)     | pannello educatore            |
| `GET /api/health`      | gateway → skill `/health` | smoke test             |
| `GET /api/model-status`| gateway → llama `/health` | banner "modello attivo?" |
| `GET /api/sessions(<id>)` | gateway        | osservabilità (finestra `?window=`, filtro `?ip=`) |
| `POST /api/scaffold`   | gateway → skill `/scaffold` | **tappa 3** (SkillOutput) |
| `POST /api/chat`       | gateway → llama `/v1/chat/completions` | **tappe 1/2/4** (chat libera, body normalizzato) |

Env del gateway: `SKILL_URL` (skill), `LLAMA_URL` (llama), `GATEWAY_PORT`
(8090), `LAB_SESSIONS_DIR`, `LAB_ACTIVE_WINDOW`.
Config nginx: [`../../nginx.conf`](../../nginx.conf).

## Avvio rapido (locale, senza Docker)

```sh
cd laboratory
# terminale 1: skill service (backend "auto": usa llama se attivo, fallback mock)
LAB_BACKEND=auto python3 -m backend.service
# terminale 2: gateway (solo /api/*)
SKILL_URL=http://localhost:8080 LLAMA_URL=http://localhost:8081 python3 -m backend.gateway
# terminale 3 (opzionale, preview della pagina senza API):
cd backend/web/static && python3 -m http.server 8090
```

Per l'esperienza completa (stessa origine, proxy `/api/*`) usare il compose.
Apri <http://localhost:8090>. **Senza modello**: la pagina mostra il banner
"Modello NON attivo", le **chat delle tappe 1/2/4 sono disabilitate**, mentre la
**skill (tappa 3)** funziona (mock). Per le chat live avvia il modello e la skill
lo rileva da sola (`auto`):

```sh
# (dove c'è rete) scarica un GGUF in models/, poi avvia llama-server:
llama-server -m models/qwen2.5-1.5b-instruct-q4_k_m.gguf --host 0.0.0.0 --port 8081 -t 4 -c 2048 --temp 0.2 &
# → banner "Modello attivo", tutte le tappe live (chat 1/2/4 + skill su modello)
```

### In Docker (replica esatta della topologia di campo)

Il compose usa già `LAB_BACKEND=auto` di default: basta il profilo model,
niente env da impostare a mano.

```sh
docker compose up                                      # demo: mock automatico (chat off, skill on)
MODEL_FILE=qwen2.5-1.5b-instruct-q4_k_m.gguf \
  docker compose --profile model up                    # reale: tutte le tappe live
docker compose -f docker-compose.yml -f docker-compose.pi.yml \
  --profile model up                                   # host Pi 3: 0.5B obbligatorio (o: make pi-up)
```
(`LAB_BACKEND` è `mock|llama|auto`; con `auto` la skill prova llama e torna
a mock se non lo trova.)

## Host sostituibile: mini PC *oppure* Pi 3

Lo **stesso stack** (senza browser) gira su entrambi gli host; i client non
cambiano di una riga, **cambiano solo l'indirizzo** a cui puntano:

| Host                     | Modello    | Comando                                               |
| ------------------------ | ---------- | ----------------------------------------------------- |
| mini PC (principale)     | 1.5B (o FT)| `make up` (o `MODEL_FILE=... docker compose --profile model up`) |
| Raspberry Pi 3 / 1 GB    | **0.5B** (obbligatorio) | `make pi-up` (override `docker-compose.pi.yml`) |

Sul Pi-host il browser NON gira (budget RAM: stack+OS entro ~860 MB, misurati
in [`spike/REPORT.md`](../spike/REPORT.md)); gli utenti si collegano da un
altro device. La verifica misurata (RAM, tempi, multi-postazione) spetta al
change `validazione-campo` (task 4.4).

## Parità CLI ↔ web

La **skill (tappa 3)** ha parità piena: pagina e CLI parlano lo stesso JSON
(`SkillOutput`):

| client    | endpoint                          | rendering               |
| --------- | --------------------------------- | ----------------------- |
| pagina    | `POST <host>:8090/api/scaffold` (nginx → gateway) | JS inline (specchia `to_text`) |
| CLI/kiosk | `POST <host>:8080/scaffold` (`--remote`) | `SkillOutput.to_text` |

Le **tappe 1/2/4 sono chat multi-turno browser-first** (guidate dall'educatore);
una CLI `--chat` è follow-up opzionale, non blocca il campo.

Verificata dai test [`../tests/test_web.py`](../tests/test_web.py)
(`test_parity_cli_equals_web`, `test_parity_web_equals_skill_direct` +
`ChatBridgeTest` per `/api/chat` e `/api/model-status`).

```sh
python3 -m unittest discover -s tests    # 65 test (parità + bridge chat + tier/nginx)
```

## Deploy sul campo — riepilogo

1. **host** (mini PC o Pi 3, IP fisso es. `192.168.1.10`): `make up` (mini PC)
   o `make pi-up` (Pi 3, GGUF 0.5B in `models/`). Espone `:8090` (pagina),
   `:8080` (CLI) e `:8081` (llama, consumer fidati).
2. **Raspberry Pi 3** (client): kiosk browser o CLI che puntano a `LAB_HOST`.
   Setup completo in [`kiosk/README.md`](../kiosk/README.md) (systemd/autostart).
3. **LAN cablata**, niente WiFi/Internet. Verifica dal client:
   `curl -s http://192.168.1.10:8090/api/health` → `{"ok": true, ...}`.

> La fluidità del browser su Pi 3 reale e il deploy fisico si validano nel
> **gruppo 4/5 del change `validazione-campo`**; qui si preparano immagine +
> config.

## Stima tempi — 30 min (§3.4, G3 sera)

La pagina è fruibile anche **senza** invocare il modello (teoria + esempi), così
l'educatore detta il pacing:

| Fase                                          | Min | Modalità        |
| --------------------------------------------- | --- | --------------- |
| Intro + teoria (Oracolo / «L'IA ti vuole lì») | 5–6 | pagina, no model |
| Tappa 1 — Context Injection (chat multi-turno)| 5   | chat live*       |
| Tappa 2 — System Prompt (preset + custom)     | 5   | chat live*       |
| Tappa 3 — Skills: Diario di Bordo (hands-on)  | 8–10| **skill** (qui)  |
| Tappa 4 — Prompt Engineering (card + anteprima)| 5  | chat live*       |
| Chiusura + regola cardine                     | 3   | —                |

\* Le tappe 1/2/4 sono **chat live col modello** integrate nella pagina e
richiedono `--profile model`. Senza modello restano disabilitate (banner) ma la
teoria e la skill (tappa 3) restano fruibili.

## Osservabilità

Per seguire cosa fanno i ragazzi collegati (e una persona in particolare):

- **Log terminale strutturati** (`make logs`): ogni richiesta con client-ID, tappa,
  esito, durata. Segui uno: `make logs | grep '#<cid>'`.
  `[2026-08-13 22:05:11] #marco-123 POST /api/scaffold -> 200 (1ms) [kind=scaffold in=70 out=1267]`
- **Pannello educatore** → <http://localhost:8090/admin>: elenco sessioni con
  finestra selezionabile (5/10/15/30 min o tutto lo storico del processo), IP
  visibile e filtrabile, timeline con interazioni espanse al click (contenuti
  completi). Auto-refresh 3s.
- **API**: `GET /api/sessions` (`?window=<sec>|all`, `?ip=<addr>`),
  `GET /api/sessions/<cid>` (timeline con contenuti e flag `has_trace`),
  `GET /api/sessions/<cid>/<ts>` (dettaglio con la trace LLM),
  `GET /api/tps` (serie token/s per il grafico del pannello),
  `GET /api/consumi/<cid>` (stime didattiche locale vs frontiera),
  `GET /api/model-status` (`{model_active, model?, clients}`).
- **Storage sessioni** (supervisione a posteriori): ogni interazione → riga nel
  DB **sqlite3** `sessions/sessions.db` (volume bind `./sessions`), con metadati,
  IP e testi in/out completi. Lo storico sopravvive a riavvii e rebuild (il
  gateway ricarica l'archivio all'avvio); si azzera con `make clean-sessions`.
  Il vecchio `sessions.jsonl` resta archivio legacy, non più scritto.

**Supervisione**: log, pannello, API e storage contengono metadati (ID, IP,
quando, tappa, esito, durata) **e il contenuto completo** delle interazioni
(ultimo messaggio utente + risposta per la chat; appunti + struttura per la
skill) — la postura "solo metadati" è stata rimossa nel change
`admin-osservabilita`.

```sh
make logs                  # log live
make admin                 # URL del pannello educatore
curl -s localhost:8090/api/sessions     # elenco con finestra/filtro
```

## File

- [`../gateway.py`](../gateway.py) — **il gateway** (tier 2, stdlib): SOLO
  endpoint `/api/*` — proxy `/api/scaffold` (skill), `/api/chat` (llama, body
  normalizzato), `/api/model-status` (probe + modello + n. utenti),
  osservabilità (log, tracker thread-safe, storage sqlite3 persistente,
  `/api/sessions(<id>)`).
- [`../../nginx.conf`](../../nginx.conf) — **tier 1**: statici + reverse proxy
  `/api/*` → gateway (same-origin, no CORS).
- `client.py` — client condiviso pagina/CLI (POST → `SkillOutput` JSON).
- **Trace LLM (change trace-llm)**: ogni dialogo porta un pulsante `{ }` che
  apre il popup con la wire JSON esatta verso l'endpoint del modello —
  request normalizzata e response grezza per le chat, chiamata interna della
  skill (vincolo di schema incluso) per lo scaffold. In pagina arriva sempre
  con la risposta; nel pannello educatore è persistita (`req`/`resp` su
  sqlite) e servita dal dettaglio al click. Senza chiamata al modello
  (onboarding, demo) il pulsante non compare.

- `static/index.html` — **la pagina wizard**: Intro + 4 tappe, ognuna con la sua
  toolbox di dialogo (chat per 1/2/4, skill per la 3), banner arricchito (modello
  + n. utenti), gestione errori (offline/timeout/retry), anteprima HTML sandbox
  in tappa 4. Un solo file, CSS/JS inline, zero CDN.
- `static/admin.html` — pannello educatore (finestra selezionabile, filtro IP,
  interazioni con contenuti, zero CDN).
- `static/favicon.svg` — favicon (servita su `/favicon.ico` da nginx).
