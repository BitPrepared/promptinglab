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
L'host è sostituibile: **mini PC** (modello 1.5B + coder, `make up`), **Pi 3 /
1 GB** (0.5B obbligatorio, `make pi-up` — override `docker-compose.pi.yml`,
senza coder) **oppure server GPU in LAN** (`make gpu-up`, vedi sotto); i client
cambiano solo l'indirizzo. `spike/REPORT.md` ha i numeri di fattibilità
misurati (tok/s, RAM).

### Avvio su server GPU (`make gpu-up`)

Terzo host alternativo: un server in LAN con GPU NVIDIA (in campo: RTX A2000).
Stack, modelli (**INVARIATI**: 1.5B main + coder 1.5B Q4_K_M), porte e pagina
sono quelli del mini PC — cambia solo dove gira l'inferenza. Il main serve su
**slot paralleli** (`-np 8`, contesto totale 16384 = 8 × 2048: la finestra per
ragazzo resta quella dichiarata ai client, `LLAMA_CTX=2048` — la pagina non
cambia una riga): tutta la classe invia insieme senza coda. Il coder resta a
slot singolo (`-c 8192`).

```bash
# 1) prerequisiti sull'host (una tantum): driver NVIDIA funzionante
#    (`nvidia-smi` risponde) + nvidia-container-toolkit installato
# 2) scaricare le immagini PRIMA del campo: quella CUDA pesa GB (contro
#    ~200 MB della CPU); il build delle immagini Python (gateway/skill)
#    avviene al primo avvio. Stesse -f di gpu-up:
docker compose -f docker-compose.yml -f docker-compose.gpu.yml \
  --profile model --profile coder pull
# 3) avvio (GGUF in models/, come al solito):
make gpu-up
# 4) prova dell'offload: nei log dei due llama deve comparire
#    «offloaded N/N layers to GPU» — la prova è questa, non nvidia-smi
#    nel container (nelle immagini runtime può mancare):
docker compose -f docker-compose.yml -f docker-compose.gpu.yml \
  --profile model --profile coder logs llama llama-coder | grep -i offloaded
```

I client si collegano come per gli altri host cambiando solo l'indirizzo:
pagina `http://<gpu-host>:8090`, CLI `--remote http://<gpu-host>:8080`, modello
grezzo per consumer fidati su `:8081`. Su un host privo di driver NVIDIA o di
`nvidia-container-toolkit` l'avvio dei due servizi modello fallisce **subito**
con errore Docker esplicito — `could not select device driver "nvidia" with
capabilities: [[gpu]]` — mai un degrado silenzioso su CPU. Budget VRAM
dichiarato: **~< 4 GB al
picco** (pesi ~2,1 GB + KV ~690 MB + contesti CUDA) — ci sta nella A2000 da
6 GB con margine; da confermare con `nvidia-smi` **sull'host** durante
`make loadtest N=8 URL=http://<gpu-host>:8090` (se stretto: ridurre `-np` in
`docker-compose.gpu.yml`). Fermare tutto con `make down` — anche i container
nati dall'override (`--remove-orphans`).

## Registry LAN per le immagini (zot)

Il campo è offline: le immagini si preparano dove c'è rete e si distribuiscono
sulla LAN con un registry self-hosted. Va bene qualunque registry
OCI-compliant; qui [zot](https://zotregistry.dev) (sandbox CNCF). I GGUF
**non** passano dal registro (restano montati da `./models/`): ci finiscono
solo le immagini Python (gateway/skill) e, se attivate, le immagini llama.cpp
in mirror.

```bash
# 1) avvio zot su un host sempre acceso della LAN (es. mini PC):
docker run -d --name zot -p 5000:5000 \
  -v /srv/zot/storage:/var/lib/registry \
  -v ./config.json:/etc/zot/config.json:ro \
  ghcr.io/project-zot/zot-linux-amd64:latest
#    la porta 5000 parla HTTP PLAIN: ogni host che fa PULL dichiara il mirror
#    in /etc/docker/daemon.json →
#    { "insecure-registries": ["mirror.costigiola.bitprepared.it:5000"] }
# 2) sull'host che PUBBLICA, una tantum: crea il builder buildx col trasporto
#    HTTP del registro dichiarato:
make init
# 3) pubblica le immagini del laboratorio (builda gateway/skill e le spinge;
#    destinazione/tag sono variabili: REGISTRY=... PUSH_TAG=campo-2026 make push):
make push
# 4) sugli altri host: riprendile e ri-tagga coi nomi che i compose si aspettano
#    (il push conserva il namespace bitprepared/: si scambia solo il prefix):
docker pull mirror.costigiola.bitprepared.it:5000/bitprepared/diariobot-gateway:latest
docker tag  mirror.costigiola.bitprepared.it:5000/bitprepared/diariobot-gateway:latest bitprepared/diariobot-gateway:latest
docker pull mirror.costigiola.bitprepared.it:5000/bitprepared/diariobot-skill:latest
docker tag  mirror.costigiola.bitprepared.it:5000/bitprepared/diariobot-skill:latest bitprepared/diariobot-skill:latest
```

Note operative:

- **HTTP plain, dichiarato in due posti**: la porta 5000 non parla TLS — i
  daemon dei client hanno il mirror in `insecure-registries` (serve al pull),
  e `make init` scrive `http = true` nel config del builder buildx `zotbuilder`
  (serve al push). Se un domani mettete TLS davanti a zot, bastano due righe
  da togliere.
- **Manifest OCI**: questo zot rifiuta i manifest schema2 mandati dallo store
  del demone («manifest invalid») — per questo `make push` passa da buildx con
  `--output type=image,oci-mediatypes=true` invece di un semplice `docker push`.
- **Mirror dell'immagine llama.cpp** — consigliato per il profilo GPU (GB):
  l'estensione *sync* di zot replica upstream (es. `ghcr.io/ggml-org`) dentro
  il registro su schedule. La scarichi una volta sola dove c'è rete, poi tutti
  gli host fanno pull dalla LAN — coerente col vincolo «il download va fatto
  dove c'è rete».
- **Push chiuso, pull aperto**: zot gira anche senza autenticazione; nella LAN
  fidata è accettabile, ma conviene almeno proteggere il push (htpasswd o API
  key nel config).
- **Multi-arch**: zot supporta OCI image index — se mai servisse pre-pubblicare
  un'immagine arm/v7 per il Pi:
  `docker buildx build --platform linux/arm/v7 --push`. Oggi il Pi builda in
  loco (python-slim è multi-arch, vedi sopra): non serve.
- **Errori tipici, cura rapida** (verificati al primo setup):
  «manifest invalid» spingendo col demone (`docker push` diretto) → non è la
  via: quel zot vuole manifest OCI, si usa `make push`;
  «server gave HTTP response to HTTPS client» durante `make push` → il builder
  non conosce il trasporto del registro: rilanciare `make init` (obbligatorio
  anche dopo un cambio di `REGISTRY`);
  pull rifiutato dal daemon di un client → manca la voce in
  `insecure-registries`.

## Test

```bash
cd laboratory
python3 -m unittest discover -s tests
```

## Laboratorio codice (pagina `code.html`)

La vecchia tappa «⑤ Prompt Engineering» è un laboratorio autonomo su pagina
dedicata, **aperto solo dalle postazioni che l'educatore abilita**: il modello
scrive una **pagina HTML completa** — HTML e CSS in un **file unico** — e il
ragazzo se la porta via con **Copia**, **Scarica .html** o **Apri**, con
anteprima in sandbox. Dalla pagina del percorso guidato (`index.html`, ora
①–④) non ci sono link: l'accesso lo dà l'educatore, che apre `code.html` dal
pannello `/admin`.

**Un solo comando** (`make up`) porta su DUE llama: il modello principale
(tappe ①–④ e skill, `-c 2048` come sempre) e il **coder dedicato**
`qwen2.5-coder-1.5b` con `-c 8192` (la pagina intera ha bisogno di contesto;
KV ~+250 MB su 24 GB verificati). Prerequisito: il GGUF del coder in `models/`
— **senza, `make up` non parte** (il fallback resta `make demo`). Il Pi 3 non
attiva il coder: `make pi-up` resta com'è e il codice gira sul modello
principale, che però tronca le pagine a `-c 2048` (fallback dichiarato
dall'etichetta). Cambiare file: `CODER_FILE=... make up`.

```bash
# 1) GGUF in models/: qwen2.5-1.5b (main) + qwen2.5-coder-1.5b (coder)
# 2) make up
# 3) dal pannello /admin, riquadro «Laboratorio codice»: scrivi gli IP delle
#    postazioni abilitate (le chips propongono quelli già visti) e [Salva] —
#    vale da subito, senza riavvii, e sopravvive ai restart
```

Come funziona (policy nel gateway, header `X-Step: code`):

- **Gate IP**: allowlist di IP **esatti** nel KV del gateway; chi non è in
  lista riceve un 403 chiaro e la pagina lo dice in modo amichevole. La lista
  non è mai esposta ai client: `model-status` riporta solo l'esito di chi
  chiede.
- **Tetto dedicato**: 4096 token di risposta (le chat ①②④ restano a
  256/768; la ③, la tappa della skill, a 512); se la pagina esce troncata
  (`finish_reason: length`) la pagina
  lo dice e il ragazzo lo riconduce al limite dichiarato.
- **Una generazione alla volta** in locale: le richieste concorrenti ricevono
  429 con `retry_after` e la pagina riprova da sola (countdown, turno non
  perso). La lentezza del coder NON è un rifiuto: niente backpressure su
  questa tappa, e i suoi punti token/s vivono in un pool separato che non
  inquina il cancello delle chat ①–④.
- **Attese lunghe**: 4096 token a ~4,5 t/s ≈ 15 minuti — la catena di timeout
  di questa tappa è estesa (gateway 900 s, client 930 s, nginx 960 s); oltre,
  un 504 JSON onesto. Lo spinner mostra i secondi che passano e la finestra
  di contesto dichiarata è quella di chi risponde (8192 col coder, 2048 col
  principale).
- **Fallback dichiarato**: col coder attivo genera lui; se è giù (o cade a
  metà richiesta), il modello principale — e la tendina dichiara sempre chi
  risponde davvero.
- Il **selettore del modello remoto** (endpoint reale, vedi sotto) e i
  **consumi a due tabelle** vivono in questa pagina.

## Endpoint reale nel laboratorio codice (Hetzner Inference API) — opzionale

Il laboratorio codice può confrontare il modello locale del campo con modelli
open-weight di taglia superiore serviti dall'[Inference API di Hetzner](https://inference.hetzner.com)
(sperimentale, gratuita; OpenAI-compat). Tutto passa **sempre dal gateway**:
il token non arriva mai al browser.

```bash
# 1) Crea laboratory/.env (gitignored — modello in .env.example):
echo 'HETZNER_API_KEY=<il-tuo-token>' > .env
# 2) Avvia come al solito (docker compose legge il .env da solo):
make up        # o make demo
# 3) Dalla pagina /admin: riquadro «Endpoint reale» → [Attiva]
```

Comportamento e vincoli (sono loro stessi parte della lezione):

- **Selettore solo nel laboratorio codice**, e solo se: token configurato,
  interruttore dell'educatore su ON (default OFF, si comanda da `/admin`),
  circuito di protezione non scattato. Cambiare modello azzera la
  conversazione (il prompt seme resta). Vale anche il gate IP della
  postazione: le richieste remote da IP non abilitato non partono.
- **I modelli grandi sul tier gratuito sono LENTI**: risposta non in streaming
  e in coda — misurati anche minuti per una generazione. Non è un errore: lo
  spinner avvisa il ragazzo. Oltre 240 s il gateway risponde con un errore
  chiaro (mai una pagina HTML di proxy).
- **Modelli offerti**: `Qwen/Qwen3.6-35B-A3B-FP8` e `DeepSeek-V4-Flash-0731`.
  `Kimi-K2.7-Code` non è in allowlist: col nostro token l'endpoint risponde
  «model use not permitted» (verificato al campo — forse va abilitato in
  console Hetzner). Quando torna, basta una riga in `_REMOTE_MODELS`.
- **Limiti per API key** (finestra 60 s, condivisa da tutta la sala):
  10 richieste · 4M token in ingresso · 100k in uscita. Il gateway li fa
  rispettare **predittivamente**: la richiesta che li violerebbe non parte
  mai. Quando si supera la linea il circuito **scatta e resta OFF** finché
  l'educatore non preme [Sblocca] in `/admin` (onesto: la finestra invecchia
  da sola, lo sblocco funziona solo se le richieste vecchie sono uscite).
- **Le sessioni con endpoint reale** sono evidenziate nel pannello (badge
  nuvola + nome modello); il grafico token/s resta quello del modello locale.
- **Consumi**: nel laboratorio codice con modello remoto il riquadro mostra
  DUE tabelle — token **reali** dell'usage e costo API a **listino standard**
  del modello scelto (costanti didattiche in `backend/costi.py`, non il
  prezzo dell'offerta sperimentale), più il confronto locale-vs-frontiera di
  sempre, calcolato sulle sole chat locali.

Senza `.env` il laboratorio è identico a prima: nessun selettore, nessuna
richiesta esterna. Per cambiare i modelli offerti o i listini: allowlist in
`backend/gateway.py` (`_REMOTE_MODELS`) e `backend/costi.py`
(`REMOTO_LISTINO_EUR_PER_MTOKEN`).

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
