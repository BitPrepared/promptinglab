# -*- coding: utf-8 -*-
"""Fine-tune FASE 2 del modello "Diario di Bordo" (task 4.3).
#
# DA ESEGUIRE SU GOOGLE COLAB (GPU T4/free basta per 0.5B e 1.5B con QLoRA).
# In questo ambiente NON si può lanciare (niente GPU). Produce un GGUF drop-in
# che sostituisce il modello base senza modificare la skill (task 4.5).
#
# Workflow:
#   1. Su Colab: Runtime -> GPU (T4). Carica laboratory/finetune/dataset/golden.jsonl
#      e questo script.
#   2. Esegui le celle in ordine. Alla fine scarica il GGUF Q4_K_M.
#   3. Copia il GGUF in laboratory/models/ e punta MODEL_FILE li' (compose) o
#      MODEL_PATH (CLI local). La skill e l'adapter NON cambiano.
#   4. Valida con: python3 finetune/eval.py --backend llama --label <ft> ...
#      e confronta con finetune/baseline_*-base.json (gate: no-inv 100%, ecc.).
#
# Lo script e' strutturato a celle (marker '# %% [markdown]'/[code]'): incollale
# in celle separate di Colab, oppure usa jupytext / VSCode interactive.
"""

# %% [markdown]
# # Diario di Bordo — fine-tune (Unsloth + QLoRA)
# Scegli il modello target in CONFIG: `0.5b` (path Pi standalone) o `1.5b`
# (path mini PC). L'altro si ottiene cambiando una riga e ri-eseguendo.

# %% [code]  ------------------------------------------------------------- config
import json
from pathlib import Path

# === Scegli il target ===
TARGET = "1.5b"   # "0.5b" (Pi) | "1.5b" (mini PC) | "3b" (mini PC, piu' pesante)

MODEL_SOURCES = {
    "0.5b": "unsloth/Qwen2.5-0.5B-Instruct",
    "1.5b": "unsloth/Qwen2.5-1.5B-Instruct",
    "3b":   "unsloth/Qwen2.5-3B-Instruct",
}
OUT_DIR     = Path(f"./out_diario_{TARGET}")
GOLDEN_JSONL = Path("./golden.jsonl")   # carica laboratory/finetune/dataset/golden.jsonl su Colab
MAX_SEQ_LEN  = 2048
EPOCHS       = 3
LR           = 2e-4
LORA_R       = 16
BATCH        = 2
GRAD_ACCUM   = 4
MAX_NEW      = 512   # per il sanity check

# Prompt IDENTICI a backend/prompts.py (mantenere sincronizzati).
SYSTEM_PROMPT = (
    "Sei la skill \"Diario di Bordo\". Il tuo compito: trasformare gli appunti grezzi "
    "di uno scout in uno SCAFFOLD STRUTTURATO del suo diario di bordo, piu' alcune "
    "domande e un check. NON scrivi il diario al posto suo: sei un supporto, non un "
    "sostituto.\nREGOLE: solo fatti dell'utente, nessuna invenzione; i campi mancanti "
    "vanno 'non specificato'; nessuna prosa, solo campi strutturati + domande + check; "
    "rispondi in italiano.\nFORMATO JSON: {title, date, scaffold{people,places,events,"
    "observations,emotions,problems,solutions,reflections}, questions[], checks[]}."
)

def build_user_message(notes: str) -> str:
    return ("Trasforma questi appunti grezzi nello scaffold strutturato (JSON):\n\n"
            f"--- APPUNTI ---\n{notes.strip()}\n--- FINE APPUNTI ---")

# %% [code]  -------------------------------------------------- dataset -> chat
def load_chat_dataset():
    """golden.jsonl -> lista di conversazioni ChatML (system/user/assistant)."""
    rows = [json.loads(l) for l in GOLDEN_JSONL.read_text(encoding="utf-8").splitlines() if l.strip()]
    convs = []
    for ex in rows:
        target = json.dumps(ex["output"], ensure_ascii=False)   # JSON compatto = target
        convs.append([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_message(ex["notes"])},
            {"role": "assistant", "content": target},
        ])
    print(f"{len(convs)} esempi golden -> chat")
    return convs

conversations = load_chat_dataset()

# %% [code]  --------------------------------------------------------- install
# (Cella Colab) Esegui una volta.
# # %%capture
# !pip install "unsloth[colab-new] @ git+https://github.com/unslothai/unsloth.git"
# !pip install --upgrade --no-cache-dir "trl<0.9.0" peft accelerate bitsandbytes

# %% [code]  -------------------------------------------------------------- model
from unsloth import FastLanguageModel
import torch

model, tokenizer = FastLanguageModel.from_pretrained(
    model_name=MODEL_SOURCES[TARGET],
    max_seq_length=MAX_SEQ_LEN,
    dtype=None,           # auto (bf16 su T4 moderno)
    load_in_4bit=True,    # QLoRA
)

model = FastLanguageModel.get_peft_model(
    model,
    r=LORA_R,
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                    "gate_proj", "up_proj", "down_proj"],
    lora_alpha=LORA_R,
    lora_dropout=0.0,
    bias="none",
    use_gradient_checkpointing="unsloth",
    random_state=3407,
)

# %% [code]  --------------------------------------------------------------- train
from datasets import Dataset
from trl import SFTTrainer, SFTConfig

def to_text(conv):
    return {"text": tokenizer.apply_chat_template(conv, tokenize=False, add_generation_prompt=False)}

ds = Dataset.from_list([to_text(c) for c in conversations])

trainer = SFTTrainer(
    model=model,
    tokenizer=tokenizer,
    train_dataset=ds,
    dataset_text_field="text",
    max_seq_length=MAX_SEQ_LEN,
    args=SFTConfig(
        per_device_train_batch_size=BATCH,
        gradient_accumulation_steps=GRAD_ACCUM,
        warmup_steps=10,
        num_train_epochs=EPOCHS,
        learning_rate=LR,
        fp16=not torch.cuda.is_bf16_supported(),
        bf16=torch.cuda.is_bf16_supported(),
        logging_steps=5,
        optim="adamw_8bit",
        weight_decay=0.01,
        lr_scheduler_type="cosine",
        seed=3407,
        output_dir=str(OUT_DIR / "ckpts"),
        save_strategy="epoch",
        report_to="none",
    ),
)
trainer_stats = trainer.train()

# %% [code]  --------------------------------------------------------- sanity check
FastLanguageModel.for_inference(model)
from transformers import TextStreamer

_sample = conversations[0]
_msgs = _sample[:2]   # system + user (senza assistant)
_inputs = tokenizer.apply_chat_template(_msgs, tokenize=True, add_generation_prompt=True,
                                        return_tensors="pt").to("cuda")
print("=== generazione di controllo (deve essere JSON valido, solo fatti) ===")
_ = model.generate(input_ids=_inputs, max_new_tokens=MAX_NEW,
                   temperature=0.2, do_sample=True, repetition_penalty=1.1,
                   streamer=TextStreamer(tokenizer, skip_prompt=True))

# %% [code]  ----------------------------------------------------------- export GGUF
# Salva il modello merged (HuggingFace) e poi esporta in GGUF quantizzato.
# Unsloth scarica/compila llama.cpp per la conversione la prima volta.
OUT_DIR.mkdir(parents=True, exist_ok=True)

model.save_pretrained_merged(str(OUT_DIR / "merged"), tokenizer, save_method="merged_16bit")
print("merged salvato in", OUT_DIR / "merged")

# Esporta GGUF Q4_K_M (pronto per llama.cpp / laboratory). Metodi alternativi in
# quantization_method se servono altri formati.
model.save_pretrained_gguf(
    str(OUT_DIR / "gguf"),
    tokenizer,
    quantization_method=["q4_k_m"],
)
print("GGUF pronto in", OUT_DIR / "gguf")
print("\nScarica il file .gguf, copialo in laboratory/models/ (es. diario-<TARGET>-q4_k_m.gguf),")
print("imposta MODEL_FILE (compose) / MODEL_PATH (CLI) e valuta con finetune/eval.py.")
