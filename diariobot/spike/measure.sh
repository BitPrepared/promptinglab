#!/usr/bin/env bash
# Misura di fattibilità (task 1.3–1.5): tok/s + RAM dello stack reale.
# Topologia: client curl -> skill:8080 (POST /scaffold) -> llama-server:8081 -> GGUF.
# Tutto in una singola esecuzione: avvia, misura, smonta (niente processi orfani).
#
# Il service usa ora repeat_penalty=1.1 di default (adapter) che fa terminare il
# modello base con JSON valido invece di loopare. Tunabile via LLAMA_REPEAT_PENALTY.
#
# Uso:
#   ./measure.sh <model_path> <threads> <label>
set -u

DIARIOBOT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LLAMA_DIR="$DIARIOBOT_ROOT/.llamacpp/llama-b10405"
MODEL="${1:?model path required}"
THREADS="${2:?threads required}"
LABEL="${3:?label required}"
PORT_LLAMA=8081
PORT_SKILL=8080
NOTES_FILE="$DIARIOBOT_ROOT/spike/notes.txt"
LLAMA_LOG="$(mktemp)"
SKILL_LOG="$(mktemp)"
PAYLOAD="$(mktemp)"
OUT="$(mktemp)"

cd "$DIARIOBOT_ROOT"

python3 - "$NOTES_FILE" "$PAYLOAD" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    notes = f.read()
with open(sys.argv[2], "w", encoding="utf-8") as f:
    json.dump({"notes": notes}, f, ensure_ascii=False)
PY

cleanup() {
  [ -n "${LLAMA_PID:-}" ] && kill "$LLAMA_PID" 2>/dev/null
  [ -n "${SKILL_PID:-}" ] && kill "$SKILL_PID" 2>/dev/null
  wait 2>/dev/null
}
trap cleanup EXIT

echo "[$LABEL] model=$MODEL threads=$THREADS"

"$LLAMA_DIR/llama-server" \
  -m "$MODEL" --host 0.0.0.0 --port "$PORT_LLAMA" \
  -t "$THREADS" -c 2048 --temp 0.2 -np 1 \
  >"$LLAMA_LOG" 2>&1 &
LLAMA_PID=$!

ok=""
for i in $(seq 1 60); do
  if curl -sS -m 2 -o /dev/null "http://localhost:$PORT_LLAMA/health" 2>/dev/null; then ok=1; break; fi
  sleep 1
done
if [ -z "$ok" ]; then
  echo "[$LABEL] ERRORE: llama-server non parte:"; tail -n 15 "$LLAMA_LOG"; exit 1
fi

RSS_IDLE=$(awk '/^VmRSS:/{print $2}' /proc/$LLAMA_PID/status)

DIARIOBOT_BACKEND=llama LLAMA_URL="http://localhost:$PORT_LLAMA" DIARIOBOT_PORT="$PORT_SKILL" \
  python3 -m diariobot.service >"$SKILL_LOG" 2>&1 &
SKILL_PID=$!
for i in $(seq 1 30); do
  if curl -sS -m 2 "http://localhost:$PORT_SKILL/health" 2>/dev/null | grep -q '"ok":true'; then break; fi
  sleep 0.5
done

# warmup + 3 run misurate; tengo l'ultima come misura rappresentativa
for r in $(seq 1 3); do
  T0=$(date +%s.%N)
  HTTP=$(curl -sS -m 120 -o "$OUT" -w "%{http_code}" \
    -X POST "http://localhost:$PORT_SKILL/scaffold" \
    -H "Content-Type: application/json" --data-binary @"$PAYLOAD")
  T1=$(date +%s.%N)
  WALL=$(awk "BEGIN{printf \"%.2f\", $T1-$T0}")
  echo "[$LABEL] run $r: http=$HTTP wall=${WALL}s"
done

# parsing timing dal log. NOTA: llama-server cacha il prompt nello slot KV,
# quindi dalla 2a richiesta il prompt eval è quasi nullo (~1 token). Per il
# prompt-processing prendiamo la PRIMA richiesta (cold); la generazione è
# stabile, prendiamo l'ultima.
pline_cold=$(grep "prompt eval time" "$LLAMA_LOG" | head -n1)
gline=$(grep "eval time" "$LLAMA_LOG" | grep -v "prompt eval" | tail -n1)
PTOK=$(echo "$pline_cold"  | grep -oE '/ +[0-9]+ tokens' | grep -oE '[0-9]+')
PROMPT_TPS=$(echo "$pline_cold" | grep -oE '[0-9]+\.[0-9]+ tokens per second' | grep -oE '[0-9]+\.[0-9]+')
GTOK=$(echo "$gline"  | grep -oE '/ +[0-9]+ tokens' | grep -oE '[0-9]+')
GEN_TPS=$(echo "$gline" | grep -oE '[0-9]+\.[0-9]+ tokens per second'    | grep -oE '[0-9]+\.[0-9]+')

RSS_PEAK=$(awk '/^VmHWM:/{print $2}' /proc/$LLAMA_PID/status)
RSS_IDLE_MB=$(awk "BEGIN{printf \"%.0f\", ${RSS_IDLE:-0}/1024}")
RSS_PEAK_MB=$(awk "BEGIN{printf \"%.0f\", ${RSS_PEAK:-0}/1024}")

VALID=$(python3 - "$OUT" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
sc = d.get("scaffold", {})
shape_ok = all(k in sc for k in
    ("title","date","people","places","events","observations","emotions","problems","solutions","reflections"))
arrays = ("people","places","events","observations","emotions","problems","solutions","reflections")
items = sum(len(sc.get(k, [])) for k in arrays)
title = sc.get("title")
# valido = struttura ok, scaffold NON vuoto, title non è il fallback "non specificato"/empty
valid = shape_ok and items > 0 and title not in (None, "non specificato", "")
print(("VALID" if valid else "FALLBACK/EMPTY"),
      "| title=%r" % title, "| items=%d" % items,
      "| questions=%d" % len(d.get("questions", [])), "| checks=%d" % len(d.get("checks", [])))
PY
)

echo "---------------------------------------- RESULT [$LABEL]"
printf "  prompt_tokens       : %s\n" "${PTOK:-?}"
printf "  prompt_eval tok/s   : %s\n" "${PROMPT_TPS:-?}"
printf "  gen_tokens          : %s\n" "${GTOK:-?}"
printf "  gen tok/s (eval)    : %s\n" "${GEN_TPS:-?}"
printf "  RAM idle (RSS)      : %s MB\n" "$RSS_IDLE_MB"
printf "  RAM peak (VmHWM)    : %s MB\n" "$RSS_PEAK_MB"
printf "  scaffold (via skill): %s\n" "$VALID"
echo "----------------------------------------"

cp "$OUT" "$DIARIOBOT_ROOT/spike/out_${LABEL}.json"
rm -f "$LLAMA_LOG" "$SKILL_LOG" "$PAYLOAD" "$OUT"
echo "[$LABEL] done."
