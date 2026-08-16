# Sessione di esempio — Workflow Diario di Bordo

Esempio completo di sessione (output generato dal **backend mock**, deterministico
e offline; con il modello reale la qualità dell'estrazione migliora, ma il
contratto è lo stesso: solo fatti dell'utente, niente prosa).

## Input (appunti grezzi della squadriglia)

```
Campo base a Costigiola. Oggi, con Marco e Lucia abbiamo montato la tenda nord.
Pioveva e non riuscivamo con i picchetti, poi abbiamo usato delle pietre come
zavorra e ha funzionato. Stanchi ma molto felici.
```

## Eventi demo (modalità trasparenza — mai chain-of-thought)

```
🔧 Sto leggendo i tuoi appunti…
🔧 Sto estraendo i fatti — solo i tuoi, senza inventarne.
✅ Controllo struttura: niente prosa, niente invenzioni.
❓ Aggiungo le domande per approfondire.
✍️ Scaffold pronto. Ora il diario lo scrivi tu, partendo da qui.
```

## Output (scaffold strutturato)

Lo scaffold copre le **8 aree** del contratto (`people`, `places`, `events`,
`observations`, `emotions`, `problems`, `solutions`, `reflections`); le aree
senza fatti nell'input restano a `non specificato` — llm non inventa (o meglio non dovrebbe).

```
## Campo base a Costigiola.
Data: Oggi

### Persone
- Marco
- Lucia

### Luoghi
- Costigiola

### Eventi
- Campo base a Costigiola.
- con Marco e Lucia abbiamo montato la tenda nord.

### Osservazioni
- Oggi

### Emozioni
- Stanchi ma molto felici.

### Problemi
- Pioveva e non riuscivamo con i picchetti

### Soluzioni
- poi abbiamo usato delle pietre come zavorra e ha funzionato.

### Riflessioni
- non specificato

### Domande per approfondire
- Cosa vi ha fatto sentire così in quel momento?
- Cosa avreste fatto diversamente, se poteste?
- Cosa vi portate a casa da questa giornata?

### Check (chiarezza / ortografia)
- [clarity] Oggi: frase frammentaria, manca il soggetto o il verbo
```

## Come riprodurlo

```bash
cd laboratory
python3 -m backend.cli "Campo base a Costigiola. Oggi, con Marco e Lucia ..."
# oppure via servizio:
python3 -m backend.service &
curl -s -X POST localhost:8080/scaffold -H 'Content-Type: application/json' \
  -d '{"notes":"Campo base a Costigiola. Oggi, con Marco e Lucia ..."}'
# oppure dal gateway della pagina (topologia completa: make up / make demo):
curl -s -X POST localhost:8090/api/scaffold -H 'Content-Type: application/json' \
  -d '{"notes":"Campo base a Costigiola. Oggi, con Marco e Lucia ..."}'
```

Via HTTP la risposta JSON porta anche gli `events` demo e, col modello reale,
l'`usage` (token di prompt/completion, tappa ④).

La prosa del diario **non** è qui: il ragazzo la scrive partendo dallo scaffold.
