# FASE 2 — Fine-tune del modello "Diario di Bordo"

Enhancement **swappabile**: produce un GGUF fine-tuned che sostituisce il modello
base **senza modificare la skill** (drop-in via adapter, task 4.5). **Non blocca
il campo** — la FASE 1 (demo base) gira comunque; il fine-tune migliora qualità
(specie sul path Pi/0.5B) e velocità.

> **Cosa si fa qui vs altrove.** Dataset, validatore, distillazione, eval e
> baseline si fanno **in locale** (questa cartella). Il **training (4.3) va su
> Colab** (serve GPU; qui non c'è). L'**eval post-training** si rifà in locale.

## Numeri di partenza (baseline, modelli base)

Eval su 18 esempi golden (`eval.py`). **Gate qualità**: no-invenzioni 100%,
no-prosa 100%, JSON valido 100%. Entrambi i base **falliscono** → giustifica la
FASE 2. Dettagli in `baseline_*-base.json`.

| Modello | JSON valido | no-invenzioni | no-prosa | domande ok | fact_recall | Gate |
|---|---|---|---|---|---|---|
| **0.5B base** | 72% | 61% | 100% | 11% | 0.27 | FAIL |
| **1.5B base** | 83% | 44% | 89% | 72% | 0.38 | FAIL |

Target del fine-tune: portare **no-invenzioni e JSON-valido al ~100%** e alzare
la fact_recall (estrazione più completa), mantenendo italiano e niente prosa.

## Pipeline

```
golden.jsonl ──▶ validate_dataset.py ──(ok)──▶ train_colab.py [Colab/GPU] ──▶ GGUF
   ▲                                              │
   │  curation                                    └─ drop-in: diariobot/models/*.gguf
seeds.txt ──▶ distill.py ──▶ candidates.jsonl ────────▶ eval.py ──▶ confronto baseline
```

### 4.1 Dataset golden
`dataset/golden.jsonl` — 18 esempi a mano, vari (ricco, povero, ambiguo, prosa,
ortografia, emozioni, problema/soluzione, servizio, cucina, escursione, gioco,
sera, meteo, soprannomi, riflessione, negativo, senza-persone, denso). Conformi
al contratto: solo fatti dell'utente, niente prosa, `non specificato` per gli
assenti, domande 2-3, check clarity/orthography.

```bash
python3 finetune/validate_dataset.py            # 18 esempi, 0 violazioni
```

Da espandere: aggiungere esempi (id `gNN`) e ri-validare. La qualità del golden
è il limite superiore del modello.

### 4.2 Distillazione sintetica + curation
`distill.py` genera **candidati** da appunti seed usando un llama-server
(idealmente un modello forte; con il 1.5B base fa da bootstrap). Ogni candidato è
`needs_review`: va **curato a mano** (togliere invenzioni/prosa, completare i
`non specificato`) e promosso in `golden.jsonl`.

```bash
# llama-server up (vedi spike/README.md o README.md principale)
python3 finetune/distill.py --url http://localhost:8081 --seeds dataset/seeds.txt --label 1.5b-base
# -> dataset/candidates.jsonl  (curare a mano -> golden.jsonl, poi validate_dataset.py)
```

### 4.3 Training (Colab)
`train_colab.py` — Unsloth + QLoRA, 0.5B/1.5B/3B parametrici. Formatta i golden
in ChatML, addestra, fonde ed esporta **GGUF Q4_K_M**. Istrioni e celle pronte
nell'intestazione dello script. **Da eseguire su Colab con runtime GPU.**

### 4.4 Eval (locale, prima e dopo)
`eval.py` — metriche per esempio + aggregate + gate. Baseline già acquisite
(`baseline_0.5b-base.json`, `baseline_1.5b-base.json`). Dopo il training, rifare
l'eval sul GGUF fine-tuned e **confrontare**:

```bash
# llama-server che serve il GGUF fine-tuned
python3 finetune/eval.py --backend llama --url http://localhost:8081 --label 1.5b-ft --out baseline_1.5b-ft.json
```

### 4.5 Drop-in
Il modello fine-tuned sostituisce il base **senza toccare la skill**:
- compose (mini PC): `MODEL_FILE=diariobot-diario-1.5b-q4_k_m.gguf`
- CLI standalone (Pi): `MODEL_PATH=models/diariobot-diario-0.5b-q4_k_m.gguf DIARIOBOT_BACKEND=local`

L'adapter (`LlamaServerModel`/`LocalLlamaBackend`) è invariato; `repeat_penalty`
resta attivo. Verifica qualità con `eval.py` (gate PASS) prima di portarlo in campo.

## File
- `dataset/golden.jsonl` — dataset golden (4.1)
- `dataset/seeds.txt` — appunti seed per la distillazione (4.2)
- `dataset/candidates.jsonl` — candidati da curare (4.2, generato)
- `validate_dataset.py` — validatore del golden (4.1)
- `distill.py` — generazione candidati (4.2)
- `eval.py` — harness di valutazione + gate (4.4)
- `train_colab.py` — training Unsloth/QLoRA + export GGUF (4.3, Colab)
- `baseline_{0.5b,1.5b}-base.json` — baseline misurate (4.4)
