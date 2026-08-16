## Purpose

Skill LLM locale e offline che trasforma gli appunti grezzi di una squadriglia in uno **scaffold strutturato** del diario di bordo (riempito solo con i fatti forniti dall'utente), accompagnato da domande di approfondimento e un check di chiarezza. Non genera prosa: la scrittura finale resta al ragazzo. È esposta come servizio, con due target di esecuzione (mini PC e Raspberry Pi 3 standalone) e un modello scambiabile.

## Requirements

### Requirement: Generazione one-shot dello scaffold

Il sistema SHALL accettare in ingresso il testo degli appunti grezzi della squadriglia e SHALL restituire, in una singola elaborazione, un output strutturato composto da: (1) scaffold del diario con i campi dello schema, (2) da 2 a 3 domande di approfondimento, (3) un check di chiarezza/ortografia. Il sistema MUST NOT avviare un ciclo interattivo di domande-risposte: l'elaborazione è one-shot.

#### Scenario: Appunti sufficienti

- **WHEN** la squadriglia fornisce appunti grezzi che contengono persone, eventi e un luogo
- **THEN** il sistema restituisce lo scaffold con quei campi popolati, 2–3 domande pertinenti e un check di chiarezza, in un'unica risposta strutturata

#### Scenario: Una sola elaborazione

- **WHEN** viene fornito l'input
- **THEN** il sistema produce l'output completo in una sola chiamata al modello, senza richiedere ulteriori interazioni per completarlo

### Requirement: Solo fatti dell'utente, nessuna invenzione

Il sistema MUST NOT inventare eventi, persone, luoghi, emozioni o dettagli non presenti nell'input. Ogni campo dello scaffold SHALL essere riempito esclusivamente con contenuto rintracciabile nell'input dell'utente. Le informazioni assenti SHALL essere marcate esplicitamente come mancanti (es. "non specificato"), non generate. Le inferenze SHALL essere etichettate come tali e tenute distinte dai fatti.

#### Scenario: Informazione assente nell'input

- **WHEN** l'input menziona un evento ma nessuna emozione collegata
- **THEN** il campo emozioni dello scaffold risulta marcato come mancante, non viene prodotta alcuna emozione inventata

#### Scenario: Inferenza distinta dal fatto

- **WHEN** il sistema deduce un'ipotesi non esplicitamente scritta dall'utente
- **THEN** quell'ipotesi è etichettata come inferenza e non appare come fatto nello scaffold

### Requirement: Nessuna generazione di prosa

Il sistema MUST NOT produrre prosa narrativa, paragrafi di diario redatto o testo discorsivo che sostituisca la scrittura del ragazzo. L'output è composto unicamente da: campi strutturati, domande e check. La composizione della prosa finale è un non-goal esplicito.

#### Scenario: Richiesta implicita di prosa

- **WHEN** l'input contiene appunti dettagliati che potrebbero essere trasformati in un discorso
- **THEN** il sistema restituisce comunque solo lo scaffold strutturato e non genera un testo narrativo del diario

### Requirement: Domande di approfondimento pertinenti e non ridondanti

Le domande prodotte SHALL essere brevi, comprensibili, pertinenti alle informazioni già raccolte e MUST NOT chiedere nuovamente dettagli già presenti nell'input. Almeno una domanda SHOULD favorire la riflessione personale.

#### Scenario: Nessuna ridondanza

- **WHEN** l'input ha già dichiarato i partecipanti e il luogo
- **THEN** nessuna delle domande prodotte richiede nuovamente partecipanti o luogo

### Requirement: Check di chiarezza e ortografia

Il sistema SHALL segnalare passaggi ambigui o poco chiari dell'input e eventuali problemi ortografici evidenti, senza riscrivere il testo al posto dell'utente.

#### Scenario: Passaggio ambiguo

- **WHEN** l'input contiene una frase con soggetto non chiaro
- **THEN** il check segnala il punto come ambiguo e indica dove, senza riscriverlo

### Requirement: Funzionamento offline sulla rete locale

Il sistema SHALL funzionare integralmente senza accesso a Internet, operando sulla rete locale cablata del campo. Nessuna funzionalità della skill richiede rete esterna.

#### Scenario: Campo senza Internet

- **WHEN** il campo non dispone di connessione Internet (solo LAN cablata locale)
- **THEN** la skill produce comunque scaffold, domande e check, con il modello che gira localmente sul mini PC (o, nel path standalone, sul Raspberry Pi)

### Requirement: Deployment e target di esecuzione

Il sistema SHALL supportare due target di esecuzione dietro la stessa interfaccia: (a) **mini PC come host principale**, che esegue il modello e serve tutti i client via LAN; (b) **Raspberry Pi 3 / 1 GB come target standalone opzionale**, che esegue un modello fine-tuned minuscolo per uso singolo offline. I client (browser/CLI) SHALL essere eseguibili su Raspberry Pi 3 / 1 GB come client sottili, senza eseguire il modello localmente salvo il path standalone. Il sistema MUST NOT richiedere GPU, database vettoriali, embedding né servizi in background.

#### Scenario: Client leggero sul Pi

- **WHEN** un ragazzo usa il client su un Raspberry Pi 3 / 1 GB collegato al mini PC
- **THEN** il client invoca la skill sul mini PC e il Pi non esegue il modello, restando entro il limite di 1 GB di RAM

#### Scenario: Standalone sul Pi senza mini PC

- **WHEN** il mini PC non è disponibile e si usa il path standalone
- **THEN** la skill gira sul Raspberry Pi 3 / 1 GB con un modello fine-tuned 0.5B, entro il budget di 1 GB di RAM

### Requirement: Accessibilità come servizio locale

La skill SHALL essere accessibile come servizio sulla rete locale cablata, tramite un'interfaccia stabile, in modo che client diversi (CLI e pagina web) invochino la stessa identica capacità.

#### Scenario: CLI e web usano la stessa skill

- **WHEN** un ragazzo usa la CLI e un altro usa la pagina web con lo stesso input
- **THEN** entrambi invocano la stessa skill con lo stesso contratto di input/output e ottengono lo stesso tipo di risultato

### Requirement: Modello specializzato e scambiabile

Il sistema SHALL usare un modello **specializzato sul task del diario di bordo** (preferibilmente fine-tuned), trattato come componente scambiabile dietro un'interfaccia stabile. La sostituzione o l'aggiornamento del modello (incluso il passaggio da modello base a fine-tuned) SHALL avvenire senza modificare la logica della skill.

#### Scenario: Aggiornamento a modello fine-tuned

- **WHEN** si sostituisce il modello base con il modello fine-tuned sul task del diario
- **THEN** la skill continua a funzionare senza modifiche al codice che orchestra l'elaborazione, con qualità migliorata

### Requirement: Modalità trasparenza (demo)

Il sistema SHALL offrire una modalità demo che, a scopo educativo, mostra all'utente una spiegazione sintetica e leggibile delle azioni compiute (es. "sto estraendo i fatti", "sto formulando le domande"). In questa modalità il sistema MUST NOT esporre la catena di pensiero privata del modello.

#### Scenario: Azione resa visibile in modo sintetico

- **WHEN** la modalità demo è attiva durante l'elaborazione
- **THEN** l'utente vede una riga sintetica che descrive l'azione in corso, senza vedere i token interni del ragionamento del modello

### Requirement: Interazione in italiano

Il sistema SHALL operare in italiano: prompt, output strutturato, domande e check sono prodotti in lingua italiana.

#### Scenario: Output in italiano

- **WHEN** la squadriglia fornisce appunti in italiano
- **THEN** scaffold, domande e check sono restituiti in italiano

### Requirement: Trasparenza della chiamata al modello

Quando la skill invoca il modello, la risposta di `/scaffold` SHALL includere un campo opzionale `trace` contenente il JSON inviato all'endpoint del modello (messaggi, parametri, eventuale `response_format` con lo schema) e il JSON grezzo restituito. Il campo è retrocompatibile: i consumer che non lo leggono ricevono lo stesso `SkillOutput` di prima (stesso trattamento del campo `events` della demo). Il campo MUST NOT essere presente quando la risposta è stata prodotta senza chiamare il modello (percorso di onboarding o backend demo).

#### Scenario: Chiamata tracciata

- **WHEN** la skill elabora appunti con una chiamata al modello
- **THEN** la risposta contiene `trace.request` con il body inoltrato al servizio modello e `trace.response` con il payload grezzo ricevuto

#### Scenario: Assenza senza modello

- **WHEN** la risposta viene prodotta senza chiamata al modello
- **THEN** la risposta non contiene il campo `trace`

#### Scenario: Retrocompatibilità dei consumer

- **WHEN** un consumer esistente (CLI o validatore) legge la risposta ignorando `trace`
- **THEN** ottiene esattamente lo stesso `SkillOutput` e lo stesso esito di validazione di prima
