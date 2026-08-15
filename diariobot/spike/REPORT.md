# Spike di fattibilità — stack reale con modello GGUF (task 1.3–1.5)

Misura **end-to-end** dello stack `skill → llama-server → GGUF` con modelli reali
(per i task `openspec/changes/skill-diario-di-bordo/tasks.md` 1.3, 1.4, 1.5).
Risolve il blocco storico "richiede modello reale + rete": la rete era disponibile
questa sessione, i GGUF scaricati da HuggingFace, lo stack esercitato per davvero.

> **Riproducibilità.** Tutti gli script e gli input sono in `spike/`:
> `measure.sh` (harness di misura), `notes.txt` (appunti di prova),
> `probe.py` / `probe_rp.py` (probe diretti), `out_*.json` (scaffold generati,
> appendice). Modello e binari non sono committati (vedi `.gitignore`).

## Ambiente di misura

- **Host:** Intel i7-1185G7 @ 3.0 GHz, 8 thread logici, 31 GB RAM. Linux x86_64.
- **Engine:** `llama.cpp` build **b10405** (binario precompilato `ubuntu-x64` CPU,
  lo stesso dell'immagine Docker `ghcr.io/ggerganov/llama.cpp:server-cpu`).
- **Modelli** (da `Qwen/...-Instruct-GGUF`): Qwen2.5-**0.5B** Q4_K_M (469 MB),
  Qwen2.5-**1.5B** Q4_K_M (1,07 GB).
- **Topologia:** client `curl` → `diariobot.service:8080` (`POST /scaffold`,
  backend `llama`) → `llama-server:8081` → GGUF. **Identica al compose di campo.**
- **Docker NON disponibile** in questo ambiente → misura **nativa** con il binario
  precompilato (stesso engine del container). I numeri di tok/s e RAM sono legati
  all'architettura hardware, non a Docker.

> ⚠️ **Non è l'hardware di campo.** L'i7-1185G7 è un riferimento **rapido**
> (limite superiore). Il mini PC (x86 low-power) e il Pi 3 (ARM A53) sono più
> lenti: le stime sul target sono ragionate, la misura reale è rimandata al
> **gruppo 7** (come da pianificazione).

## Risultati misurati

Prompt di sistema + appunti = **861 token**. Generazione ~290–400 token/scaffold.
`repeat_penalty=1.1` (vedi sotto). Media di 3 run; `tok/s` di generazione stabile.

| Configurazione | prompt tok/s (cold) | **gen tok/s** | RAM idle (RSS) | **RAM peak (VmHWM)** | Scaffold |
|---|---|---|---|---|---|
| **0.5B @ 8 thread** | 246 | **45,6** | 587 MB | **657 MB** | VALIDO (qualità marginale) |
| **1.5B @ 8 thread** | 118 | **24,8** | 1 784 MB | **1 936 MB** | VALIDO (qualità buona) |
| **1.5B @ 4 thread** | 122 | **28,5** | 1 783 MB | **1 884 MB** | VALIDO (qualità buona) |

Wall-clock per scaffold (steady-state, prompt in cache): **0.5B ~5–9 s**, **1.5B
~12–16 s**. Accettabile per l'attività di 30 min (§3.4).

> Sul 1.5B, **4 thread ≈ 8 thread** in generazione (anzi leggermente meglio): per
> un modello piccolo, i thread oltre il sweet-spot aggiungono overhead. Un mini PC
> a 4 core non è penalizzato.

## Trovate chiave

### 1. `repeat_penalty` è necessario con il modello base (fix FASE-1)

Senza penalità di ripetizione, **entrambi i modelli base loopano** sugli array
della grammatica GBNF (che non impone un massimo di elementi) e saturano
`max_tokens` senza chiudere il JSON → il validatore scade nel fallback. Con
**`repeat_penalty ≈ 1.1`** il modello **termina naturalmente**
(`finish_reason=stop`) con scaffold valido. Valori più alti (1.3) degradano la
qualità (campi vuoti, domande degenerate).

→ **Azione fatta:** l'adapter `LlamaServerModel` (e `LocalLlamaBackend` per parità)
invia ora `repeat_penalty` di default 1.1, tunabile via `LLAMA_REPEAT_PENALTY`
nel service. Test offline con fake server in `tests/test_llama_backend.py`.

### 2. Qualità: 1.5B base usabile, 0.5B base marginale

- **1.5B** estrae correttamente persone, luoghi, eventi, emozioni; formula 2
  domande pertinenti sui **dati mancanti** (es. nome squadriglia, titolo canzone)
  e segnala la data ambigua — nessuna invenzione, italiano, niente prosa.
  → **Demo FASE-1 già usabile sul mini PC.**
- **0.5B** produce JSON strutturalmente valido ma **contenuto debole** (`people`
  vuoto, `questions=['...']`, rumore). → **Il path Pi ha bisogno del fine-tune
  FASE-2** per essere genuinamente utile (già pianificato, gruppo 4).

### 3. RAM: 0.5B entro 1 GB; 1.5B richiede ~2 GB

- **0.5B**: peak 657 MB → **sta ampiamente in 1 GB** (task 1.4 ✓) come processo.
- **1.5B**: peak ~1,9 GB → il mini PC vuole ≥ 3–4 GB RAM totali (modello + OS).

## Conclusione di fattibilità

| Target | Modello | Esito |
|---|---|---|
| **mini PC** (x86, LAN) | 1.5B base | **Fattibile per la demo FASE-1.** ~25 tok/s sull'i7; il mini PC sarà più lento (scala con banda memoria/core) ma la qualità del 1.5B è già buona. RAM ~1,9 GB → mini PC ≥ 4 GB. |
| **Pi 3 / 1 GB** (standalone) | 0.5B base | **RAM ok, qualità da fine-tune.** 657 MB entrano in 1 GB ( meglio OS minimale o path solo-CLI ). La qualità del base è marginale → serve il fine-tune FASE-2; la velocità reale (A53) si misura nel gruppo 7. |

### Gate (task 1.5): **PASSATO**
Lo stack gira end-to-end con modello reale e produce **scaffold strutturato
valido** (1.5B: valido e di qualità; 0.5B: valido, qualità marginale).
Grammatica GBNF applicata, adapter `LlamaServerModel` validato su hardware reale,
validatore della skill esercitato, niente crash. Il fallback graceful resta come
rete di sicurezza per output malformati.

## Limite e differimenti

- **Non misurato sull'hardware di campo.** I numeri sono su i7; la velocità su
  mini PC e (soprattutto) Pi 3 sarà inferiore. → **gruppo 7** (validazione hardware).
- **3B non misurato** (avrebbe aggravato ulteriormente RAM/tempo; 1.5B basta per
  il gate FASE-1).
- **Prompt 861 token** è dominato dal `SYSTEM_PROMPT` (~700 token): in FASE-2 si
  può accorciare per ridurre il costo di prompt-processing sui dispositivi deboli.
