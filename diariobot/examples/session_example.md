# Sessione di esempio — Skill Diario di Bordo

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

### Emozioni
- Stanchi ma molto felici.

### Problemi
- Pioveva e non riuscivamo con i picchetti

### Soluzioni
- poi abbiamo usato delle pietre come zavorra e ha funzionato.

### Domande per approfondire
- Cosa vi ha fatto sentire così in quel momento?
- Cosa avreste fatto diversamente, se poteste?
- Cosa vi portate a casa da questa giornata?
```

## Come riprodurlo

```bash
cd diariobot
python3 -m diariobot.cli "Campo base a Costigiola. Oggi, con Marco e Lucia ..."
# oppure via servizio:
python3 -m diariobot.service &
curl -s -X POST localhost:8080/scaffold -H 'Content-Type: application/json' \
  -d '{"notes":"Campo base a Costigiola. Oggi, con Marco e Lucia ..."}'
```

La prosa del diario **non** è qui: il ragazzo la scrive partendo dallo scaffold.
