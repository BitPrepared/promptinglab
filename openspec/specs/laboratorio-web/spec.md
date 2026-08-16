## Purpose

Pagina web e server offline che fanno vivere ai ragazzi, sui loro Raspberry Pi, il percorso di prompting del laboratorio (§3.4), con accesso equivalente via CLI. Il mini PC ospita pagina, modello e servizio skill; i Pi sono client sottili su LAN cablata. È consumer della capability `diario-di-bordo`.

## Requirements

### Requirement: Pagina laboratorio con percorso guidato

La pagina web SHALL presentare il percorso di prompting del Flusso §3.4 articolato in cinque tappe — Context Injection, System Prompt, Skills, Workflow, Prompt Engineering — accompagnato dalla teoria (Oracolo/allucinazioni, "l'IA ti vuole lì", responsabilità del validatore) e da esempi. Il contenuto didattico SHALL essere fruibile anche senza interagire col modello.

#### Scenario: Ragazzo segue una tappa

- **WHEN** un ragazzo apre la pagina e naviga alla tappa "System Prompt"
- **THEN** la pagina mostra la spiegazione del concetto e un esempio, leggibili senza invocare il modello

#### Scenario: Percorso a cinque tappe

- **WHEN** un ragazzo consulta la navigazione del percorso
- **THEN** vede cinque tappe numerate in ordine: ① Context Injection, ② System Prompt, ③ Skills, ④ Workflow, ⑤ Prompt Engineering

### Requirement: Interazione con la skill dalla pagina

La pagina SHALL permettere di inserire gli appunti grezzi e ottenere lo scaffold + le domande + il check, invocando il servizio skill `diario-di-bordo`. L'output SHALL rispettarne il contratto (nessuna prosa, solo fatti dell'utente).

#### Scenario: Appunti trasformati in scaffold

- **WHEN** il ragazzo inserisce i propri appunti e avvia l'elaborazione
- **THEN** la pagina mostra lo scaffold strutturato, le domande di approfondimento e il check, senza prosa narrativa

### Requirement: Modalità demo/trasparenza nell'interfaccia

La pagina SHALL visualizzare gli eventi sintetici della skill (es. "🔧 sto estraendo i fatti…") durante l'elaborazione e MUST NOT mostrare la chain-of-thought privata del modello.

#### Scenario: Elaborazione visibile in modo sintetico

- **WHEN** la skill sta elaborando
- **THEN** la pagina mostra una spiegazione sintetica dell'azione in corso e, a fine, l'output pulito

### Requirement: Parità CLI

La CLI SHALL offrire le stesse capacità della pagina web: stesso percorso guidato e stessa interazione con la skill, producendo lo stesso tipo di output a parità di input.

#### Scenario: Stesso risultato da CLI e da web

- **WHEN** la CLI e la pagina web ricevono gli stessi appunti in ingresso
- **THEN** entrambe producono lo stesso tipo di scaffold, domande e check

### Requirement: Server unico offline sul mini PC

L'host (mini PC **oppure** Raspberry Pi 3 / 1 GB, come host alternativo) SHALL ospitare lo stack del laboratorio articolato in tre tier con responsabilità distinte: un server statico per la pagina (con reverse proxy verso le API), un gateway applicativo che ospita la business logic (proxy alla skill, chat normalizzata, stato modello, osservabilità) e il servizio modello. Il tutto SHALL operare su LAN cablata senza accesso a Internet. Il servizio modello SHALL essere raggiungibile in LAN sulla propria porta (consumer fidati: CLI, debug, sviluppo); la pagina del laboratorio MUST usare solo il gateway, dove vivono normalizzazione dei parametri e osservabilità.

#### Scenario: Servizi raggiungibili via LAN

- **WHEN** un client è collegato alla LAN cablata del campo
- **THEN** raggiunge pagina, API del gateway e servizio modello sull'host, senza traffico esterno

#### Scenario: Pagina solo tramite gateway

- **WHEN** la pagina laboratorio interagisce con il modello o con la skill
- **THEN** ogni richiesta passa dagli endpoint `/api/*` del gateway (same-origin), mai dal servizio modello diretto

#### Scenario: Servizio modello raggiungibile da consumer fidati

- **WHEN** una CLI o uno strumento di debug in LAN chiama direttamente l'endpoint compatibile OpenAI del servizio modello
- **THEN** il servizio risponde, con i parametri così come inviati (nessuna normalizzazione: quella vale solo per il percorso gateway)

### Requirement: Client sottili su Raspberry Pi 3

I client (browser e CLI) SHALL essere eseguibili su Raspberry Pi 3 / 1 GB come client sottili, puntando all'host (mini PC o Pi host) via LAN; il browser NON gira mai sullo stesso device che ospita lo stack.

#### Scenario: Client nel budget di 1 GB

- **WHEN** il ragazzo usa il browser kiosk o la CLI sul Pi 3
- **THEN** il client resta entro 1 GB di RAM invocando skill e modello sull'host

### Requirement: Stack completo su Raspberry Pi 3 come host

L'intero stack — servizio modello (taglia 0.5B), gateway e server statico, **senza browser** — SHALL poter girare su un singolo Raspberry Pi 3 / 1 GB come host alternativo al mini PC, entro il budget di RAM disponibile. Gli utenti si collegano da un altro device via browser o CLI.

#### Scenario: Pi 3 host dello stack completo

- **WHEN** lo stack viene avviato su un Pi 3 / 1 GB con modello 0.5B
- **THEN** RAM totale (modello + gateway + statico + OS) resta entro 1 GB e i client su altro device raggiungono pagina e API

#### Scenario: Sostituzione dell'host senza modifiche

- **WHEN** lo stesso stack deployato sul mini PC viene deployato su un Pi 3
- **THEN** client e pagina funzionano senza modifiche, cambiando solo l'indirizzo dell'host

### Requirement: Separazione delle responsabilità per tier

Il tier di presentazione (pagina statica) SHALL contenere solo file statici senza logica di backend né parametri del modello; il gateway SHALL possedere la normalizzazione dei parametri di chat (clamp di temperatura/token, limiti di turni) e l'osservabilità (sessioni, solo metadati); il servizio modello SHALL occuparsi solo di inferenza. La skill (`diario-di-bordo`) resta un contratto separato e invariato.

#### Scenario: Parametri difensivi nel gateway, non nel client

- **WHEN** un client invia una chat con parametri fuori limite (temperatura o token estremi, troppi turni)
- **THEN** il gateway li normalizza prima di inoltrare al servizio modello, indipendentemente da cosa la pagina ha richiesto

#### Scenario: Tier statico senza logica

- **WHEN** la pagina viene servita dal tier statico
- **THEN** riceve solo asset statici; nessuna elaborazione, nessun parametro del modello, nessuno stato risiedono nel tier di presentazione

### Requirement: Pagina leggera per browser su 1 GB

La pagina SHALL minimizzare JavaScript e CSS, non caricare risorse da CDN né servizi esterni, per girare fluidamente nel browser kiosk di un Raspberry Pi 3 / 1 GB.

#### Scenario: Fluidità sul Pi 3

- **WHEN** la pagina è caricata nel browser kiosk di un Pi 3 / 1 GB
- **THEN** la navigazione e l'interazione restano reattive senza saturare la memoria

### Requirement: Concorrenza come non-goal

Il sistema SHALL NOT garantire alto throughput concorrente. L'eventuale accesso simultaneo di più ragazzi SHALL essere gestito tramite il pacing del percorso e, opzionalmente, una coda con `demo_mode` che indica il turno.

#### Scenario: Più ragazzi, gestione a turni

- **WHEN** più ragazzi avviano elaborazioni nello stesso momento
- **THEN** il sistema le smista in modo ordinato (pacing/coda) senza crashare, anche se non in parallelo

### Requirement: Deploy kiosk sui Raspberry Pi

Ciascun Raspberry Pi SHALL potersi avviare in modalità kiosk (browser a schermo intero o CLI) puntando al mini PC, senza interazione di setup al campo.

#### Scenario: Avvio kiosk

- **WHEN** un Raspberry Pi viene acceso al campo
- **THEN** si presenta direttamente sulla pagina laboratorio (o sulla CLI), collegato al mini PC

### Requirement: Esperimento di memoria a due tab (tappa Context Injection)

La tappa "Context Injection" SHALL presentare due tab affiancabili in sequenza: "Senza memoria", dove ogni invio inoltra al modello solo l'ultimo messaggio dell'utente, e "Con memoria", dove ogni invio inoltra tutta la cronologia della conversazione. Il fallimento nella tab senza memoria SHALL essere accompagnato da un hint che spiega che l'IA ha ricevuto solo quel messaggio.

#### Scenario: Senza memoria l'IA dimentica il nome

- **WHEN** il ragazzo scrive «ciao mi chiamo Stefano» nella tab "Senza memoria", riceve risposta, poi chiede «come mi chiamo?»
- **THEN** la richiesta inoltrata contiene solo l'ultimo messaggio, il modello non può conoscere il nome, e la pagina mostra l'hint esplicativo accanto al fallimento

#### Scenario: Con memoria l'IA ricorda il nome

- **WHEN** il ragazzo ripete la stessa sequenza nella tab "Con memoria"
- **THEN** la richiesta inoltrata contiene tutta la cronologia e il modello risponde correttamente con il nome

### Requirement: Contatore token del contesto

Le chat delle tappe ①, ②, ③ e ⑤ SHALL mostrare un contatore del contesto in token reali ricevuti dal modello, nel formato `uso/limite` con barra di riempimento. Il limite mostrato SHALL corrispondere alla finestra di contesto effettivamente configurata sul servizio modello. La barra SHALL segnalare visivamente l'avvicinarsi del limite.

#### Scenario: Il contatore cresce a ogni turno

- **WHEN** il ragazzo invia un messaggio nella tab "Con memoria" e il modello risponde
- **THEN** il contatore si aggiorna al numero di token del prompt effettivamente inviato (cronologia cresciuta), superando il valore del turno precedente

#### Scenario: Il system prompt occupa contesto

- **WHEN** nella tappa ② è attivo un system prompt e il contatore tiene conto dei suoi token
- **THEN** il conteggio include il system prompt oltre alla cronologia, mostrando che condividono la stessa finestra

#### Scenario: La skill occupa contesto

- **WHEN** nella tappa ③ la skill si carica nella conversazione
- **THEN** il contatore cresce per i token della skill, mostrando che anche le istruzioni vivono nella stessa finestra di contesto

### Requirement: Contesto pieno gestito in modo amichevole

Quando l'esaurimento della finestra di contesto causa un errore del servizio modello, la chat SHALL mostrare un messaggio amichevole che spiega che la memoria è piena e invita a premere "Nuova conversazione", MUST NOT mostrare l'errore tecnico grezzo e MUST NOT perdere i messaggi già visualizzati.

#### Scenario: Finestra esaurita

- **WHEN** la cronologia cresce fino a esaurire la finestra di contesto e l'invio fallisce
- **THEN** la chat mostra «Contesto pieno — l'IA non ha più memoria. Premi "Nuova conversazione"» e la cronologia visibile resta intatta

### Requirement: Nuova conversazione

Ogni chat delle tappe ①, ② e ③ SHALL avere un pulsante "Nuova conversazione" che svuota la cronologia inviata al modello e il contatore di token. Il contenuto didattico della pagina MUST NOT essere cancellato dal reset.

#### Scenario: Reset della conversazione

- **WHEN** il ragazzo preme "Nuova conversazione" dopo alcuni turni
- **THEN** la chat si svuota, il contatore torna a zero (o al solo system prompt nella tappa ②) e il ragazzo può ripartire l'esperimento

### Requirement: Tappa System Prompt a soli preset con blob visibile

La tappa "System Prompt" SHALL permettere la scelta del system prompt solo tramite due pulsanti preset (Cavernicolo, 3 punti); NON SHALL essere offerta una modifica libera del testo. Il cambio di preset durante una conversazione SHALL avviare una nuova conversazione (cronologia e contatore azzerati): la cronologia costruita con un system prompt MUST NOT essere mischiata con quella di un altro. La tappa SHALL mostrare un'area "blob" che rappresenta il pacco completo inviato al modello: il blocco system in testa, seguito alla cronologia, per rendere visibile che sistema e dialogo condividono lo stesso contesto. La chat della tappa opera in modalità con memoria.

#### Scenario: Scelta del preset

- **WHEN** il ragazzo preme uno dei pulsanti preset
- **THEN** il system prompt attivo diventa quello del pulsante e l'area blob lo mostra in testa al pacco, prima della cronologia

#### Scenario: Cambio preset a conversazione avviata

- **WHEN** il ragazzo cambia preset dopo alcuni turni con un altro system
- **THEN** la chat si azzera (nuova conversazione) e il blob mostra il nuovo system in testa a una cronologia vuota — nessun turno del system precedente resta in circolazione

#### Scenario: Comportamento vincolato dal preset

- **WHEN** il ragazzo conversa con un preset attivo
- **THEN** le risposte del modello riflettono la personalità/vincolo del system prompt scelto, con la cronologia mantenuta tra i turni

### Requirement: Tappa Skills con caricamento visibile della skill

La tappa "Skills" SHALL presentare una skill fissa («Diario di Bordo», versione conversazionale) leggibile in pagina su due livelli: la descrizione (sempre visibile, è quella che "decide" il caricamento) e il corpo (le istruzioni complete). La tappa SHALL offrire una chat con memoria con area blob come la tappa ②. Quando il messaggio dell'utente soddisfa la regola di caricamento (dichiarata visibilmente sotto la chat), la skill SHALL entrare davvero nel contesto della chiamata — il comportamento del modello cambia di conseguenza — e l'attivazione SHALL essere evidenziata in tre punti: blocco ⚙ SKILL nel blob, divisore «Skill caricata» nel dialogo al momento dell'attivazione, e badge ⚙ sulle risposte prodotte mentre la skill è attiva. La skill MUST restare caricata per tutta la conversazione: messaggi successivi, anche fuori tema, non la scaricano. Il reset («Nuova conversazione») MUST scaricare la skill, lasciando il contesto vuoto fino a un nuovo innescamento. La pagina SHALL dichiarare che nel mondo reale la decisione di caricare la skill spetta all'agente, mentre qui è simulata da una regola deterministica.

#### Scenario: La skill è leggibile senza invocare il modello

- **WHEN** il ragazzo naviga alla tappa ③
- **THEN** la pagina mostra descrizione e corpo della skill, leggibili senza alcuna interazione col modello

#### Scenario: Attivazione su domanda pertinente

- **WHEN** il ragazzo chiede «come scrivo il diario di oggi?»
- **THEN** la richiesta inoltrata contiene il testo della skill, il blob mostra il blocco ⚙ SKILL, il dialogo mostra il divisore «Skill caricata», e la risposta — prodotta con le istruzioni della skill — reca il badge ⚙

#### Scenario: Domanda fuori tema non carica la skill

- **WHEN** il ragazzo scrive un messaggio che non soddisfa la regola di caricamento
- **THEN** la richiesta inoltrata non contiene la skill, il blob non mostra il blocco ⚙ e la risposta non reca il badge

#### Scenario: La skill resta caricata nella conversazione

- **WHEN** dopo l'attivazione il ragazzo scrive un messaggio fuori tema
- **THEN** la skill resta nel contesto (blob e contatore la riflettono) e la risposta successiva mantiene il badge ⚙

#### Scenario: Il reset scarica la skill

- **WHEN** il ragazzo preme "Nuova conversazione" con la skill attiva
- **THEN** il contesto torna vuoto (niente blocco ⚙ nel blob) e serve un nuovo messaggio pertinente per caricarla di nuovo

### Requirement: Tappa Workflow con pipeline visibile

L'esperienza «Diario di Bordo» (appunti → scaffold) SHALL essere presentata come quarta tappa «Workflow»: durante l'elaborazione la pipeline — controllo dell'ingresso, chiamata al modello, validazione dell'output — SHALL essere visibile come sequenza di stage sintetici (mai chain-of-thought del modello). Ogni stage SHALL dichiarare chi lo esegue — codice o modello — rendendo visibile che una sola riga è la chiamata all'LLM e tutto il resto è codice che controlla prima e valida dopo. A elaborazione completata la tappa SHALL mostrare i token consumati dalla chiamata al modello (ingresso e uscita) quando il servizio li fornisce; in assenza, la riga SHALL essere omessa senza errori. La risposta di `/api/scaffold` SHALL includere i conteggi token del backend quando disponibili (estensione retrocompatibile: i client che li ignorano continuano a funzionare). La tappa SHALL esplicitare il confronto con la tappa ③: stesso obiettivo, ma qui è il codice a orchestrare il modello (prompt fisso, contratto di output, validazione), non il modello a leggere istruzioni. Il contratto dell'interazione (appunti grezzi → scaffold + domande + check, senza prosa) resta invariato.

#### Scenario: Stage del workflow visibili durante l'elaborazione

- **WHEN** il ragazzo avvia l'elaborazione degli appunti
- **THEN** la pagina mostra gli stage del workflow che avanzano in sequenza (lettura, estrazione, controllo) prima di restituire l'output

#### Scenario: Ogni stage dichiara chi lo esegue

- **WHEN** la pipeline avanza
- **THEN** ogni riga è etichettata come codice o modello, e una sola riga — l'estrazione — è la chiamata all'LLM

#### Scenario: Token del workflow visibili a fine elaborazione

- **WHEN** l'elaborazione completa e il servizio ha fornito i conteggi token
- **THEN** la tappa mostra i token di ingresso e di uscita della chiamata al modello; se il servizio non li fornisce, la riga non appare e niente va in errore

#### Scenario: Confronto skill vs workflow esplicito

- **WHEN** il ragazzo legge la tappa ④
- **THEN** trova la distinzione tra il meccanismo della ③ (istruzioni che il modello legge) e quello della ④ (codice che orchestra il modello)

#### Scenario: Contratto dell'elaborazione invariato

- **WHEN** il ragazzo incolla appunti grezzi e avvia l'elaborazione
- **THEN** riceve scaffold strutturato, domande di approfondimento e check, senza prosa narrativa — come nella versione precedente della tappa

### Requirement: Limiti di generazione espliciti nell'interfaccia

Le chat delle tappe ①, ②, ③ e ⑤ SHALL dichiarare visivamente i limiti imposti dal server: il tetto di token della risposta (256, default difensivo del gateway) accanto alla finestra di contesto (2048), con l'indicazione che sono limiti server-side non modificabili dal client. I valori mostrati MUST corrispondere a quelli effettivamente applicati dal gateway.

#### Scenario: Il ragazzo vede entrambi i limiti

- **WHEN** il ragazzo apre una delle chat delle tappe ①, ②, ③ o ⑤
- **THEN** l'interfaccia mostra il limite di risposta (max 256 token) e quello di contesto (2048 token), etichettati come imposti dal server

#### Scenario: Risposta troncata riconoscibile

- **WHEN** una risposta si interrompe perché ha raggiunto il tetto di token in uscita
- **THEN** il ragazzo può ricondurre il truncation al limite dichiarato nell'interfaccia (non a un guasto o al contesto)

### Requirement: Usage token in risposta /api/chat

Il gateway SHALL includere nella risposta di `/api/chat` i conteggi di token forniti dal servizio modello (`prompt_tokens` e `completion_tokens`) e l'esito di generazione `finish_reason` (`"stop"`/`"length"`), senza alterarli. I client che ignorano questi campi MUST continuare a funzionare invariati (retrocompatibilità).

#### Scenario: Risposta con usage

- **WHEN** la pagina invia una chat e il servizio modello restituisce gli `usage`
- **THEN** la risposta del gateway contiene `reply` e `usage` con i valori invariati del servizio modello

#### Scenario: Risposta tagliata riconoscibile dal client

- **WHEN** il servizio modello interrompe la generazione al tetto di token (`finish_reason: "length"`)
- **THEN** la risposta del gateway riporta `finish_reason` invariato, permettendo alla pagina di mostrare la nota di truncation

#### Scenario: Retrocompatibilità

- **WHEN** un client esistente (CLI o versione precedente della pagina) usa la risposta di `/api/chat` senza leggere `usage`
- **THEN** il client funziona esattamente come prima
