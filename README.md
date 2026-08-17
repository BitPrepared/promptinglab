# Laboratorio di Prompting

Un laboratorio didattico **offline** per far vivere ai ragazzi (scout, campo
estivo) i concetti fondamenti dell'ingegneria dei prompt: contesto e memoria,
system prompt, skill, workflow — con un modello linguistico locale, in LAN,
senza Internet e senza account.

Il percorso è una pagina web a cinque tappe — ① Context Injection, ② System
Prompt, ③ Skills, ④ Workflow, ⑤ Prompt Engineering.

## Architettura in tre righe

```
browser dei ragazzi ──▶ nginx (statici + /api/*) ──▶ gateway (business logic)
                                                        ├── skill service
                                                        └── llama-server (modello)
```

Tutto gira su un mini PC (o un Raspberry Pi 3 host con modello 0.5B) in LAN
cablata; i ragazzi usano browser kiosk su Raspberry Pi 3. Zero dipendenze
esterne a runtime: solo Python stdlib.

## Provarlo su una macchina sola

Tutti i comandi partono dalla cartella `laboratory/` e usano `make`
(vedi `make help`). Requisito: Python 3.11+ per i test; Docker + compose per
lo stack.

### Percorso 1 — senza modello, zero download (`make demo`)

```sh
cd laboratory
make demo      # skill ON con backend mock, chat OFF (nessun modello)
```

Apri <http://localhost:8090> : il percorso si naviga, la tappa ③ (skill)
funziona col backend simulato. È il modo più rapido per vedere tutto senza
scaricare nulla. Il pannello educatore sta su <http://localhost:8090/admin>.

### Percorso 2 — con il modello vero

Le dipendenze da scaricare sono due: l'immagine Docker di llama.cpp e il
modello (un file GGUF).

```sh
cd laboratory
make pull      # immagine llama.cpp (server CPU, compatibile OpenAI)

# il modello: GGUF quantizzato q4_k_m da HuggingFace, in laboratory/models/
#   1.5B (consigliato, ~1 GB)  — target del mini PC
curl -L -o models/qwen2.5-1.5b-instruct-q4_k_m.gguf \
  "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf"
#   0.5B (più leggero, per Raspberry Pi 3)
curl -L -o models/qwen2.5-0.5b-instruct-q4_k_m.gguf \
  "https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf"

make up        # avvia lo stack col modello (MODEL_FILE=... per cambiare GGUF)
```

Apri <http://localhost:8090> e rifai le tappe col modello vero; il banner in
pagina dice se il modello è attivo.

### Comandi utili

| Comando | Cosa fa |
| --- | --- |
| `make demo` | stack senza modello (mock) |
| `make up` / `make down` | stack col modello / ferma tutto |
| `make rebuild` | ricostruisce le immagini e riparte col modello |
| `make status` | il modello risponde? quanti ragazzi collegati? |
| `make admin` | URL del pannello educatore (`/admin`) |
| `make logs` | log live (`grep '#<cid>'` segue un ragazzo) |
| `make test` | suite di test (unittest) |
| `make loadtest N=8 TURNS=4` | simula 8 ragazzi in chat (vedi sotto) |
| `make clean-sessions` | azzera lo storico delle sessioni |

## Simulare un'aula piena

`make loadtest N=8 TURNS=4` lancia 8 "ragazzi" sintetici, ognuno con una
conversazione a più turni sulle chat del gateway (sessioni `load-*`,
visibili nel pannello educatore come quelle vere). Utile per vedere come
lo stack regge il carico: nel pannello, il grafico **token/secondo** mostra
il ritmo di generazione nel tempo — se degrada con tanti ragazzi
contemporanei, si vede.

## Consumi: locale vs modello di frontiera

La pagina del laboratorio mostra a ogni ragazzo, sotto il banner, il costo
della **propria sessione**: i consumi stimati *qui, sul mini PC* (energia,
costo, acqua ≈ 0) accanto a quelli della stessa sessione *su un modello di
frontiera* (costo e acqua). Sono **stime didattiche semplificate** — i dati
di partenza (watt, €/kWh, prezzo e acqua del modello di frontiera) stanno in
un unico file modificabile, `laboratory/backend/costi.py`. La lezione: in
locale non hai modelli di frontiera, ma ogni domanda ti costa quasi niente;
la frontiera la paghi in acqua e denaro anche per le domande semplici.

## Test

```sh
cd laboratory && make test
```

## Licenza

Vedi [LICENSE](LICENSE).
