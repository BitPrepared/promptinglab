## Purpose

Pagina web e server offline che fanno vivere ai ragazzi, sui loro Raspberry Pi, il percorso di prompting del laboratorio (§3.4), con accesso equivalente via CLI. Il mini PC ospita pagina, modello e servizio skill; i Pi sono client sottili su LAN cablata. È consumer della capability `diario-di-bordo`.

## Requirements

### Requirement: Pagina laboratorio con percorso guidato

La pagina web SHALL presentare il percorso di prompting del Flusso §3.4 articolato in quattro tappe — Context Injection, System Prompt, Skills, Workflow — accompagnato dalla teoria (Oracolo/allucinazioni, "l'IA ti vuole lì", responsabilità del validatore) e da esempi. Il contenuto didattico SHALL essere fruibile anche senza interagire col modello. La quinta tappa (Prompt Engineering) NON vive in questa pagina: è il laboratorio codice, su pagina dedicata con propria capability, raggiungibile solo dalle postazioni abilitate dall'educatore; il percorso della pagina laboratorio MUST NOT mostrarne link o riferimenti (scelta dichiarata: l'accesso lo dà l'educatore, che apre la pagina dedicata dal pannello).

#### Scenario: Ragazzo segue una tappa

- **WHEN** un ragazzo apre la pagina e naviga alla tappa "System Prompt"
- **THEN** la pagina mostra la spiegazione del concetto e un esempio, leggibili senza invocare il modello

#### Scenario: Percorso a cinque tappe

- **WHEN** un ragazzo consulta la navigazione del percorso sulla pagina laboratorio
- **THEN** vede quattro tappe numerate in ordine (① Context Injection, ② System Prompt, ③ Skills, ④ Workflow) senza link né riferimenti alla quinta: la quinta tappa, Prompt Engineering, vive nel laboratorio codice su pagina dedicata

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

Il tier di presentazione (pagina statica) SHALL contenere solo file statici senza logica di backend né parametri del modello; il gateway SHALL possedere la normalizzazione dei parametri di chat (clamp di temperatura/token, limiti di turni) e l'osservabilità delle sessioni (metadati: chi, quando, tappa, esito, durata — e contenuto completo delle interazioni: input e output); il servizio modello SHALL occuparsi solo di inferenza. La skill (`diario-di-bordo`) resta un contratto separato e invariato.

#### Scenario: Parametri difensivi nel gateway, non nel client

- **WHEN** un client invia una chat con parametri fuori limite (temperatura o token estremi, troppi turni)
- **THEN** il gateway li normalizza prima di inoltrare al servizio modello, indipendentemente da cosa la pagina ha richiesto

#### Scenario: Tier statico senza logica

- **WHEN** la pagina viene servita dal tier statico
- **THEN** riceve solo asset statici; nessuna elaborazione, nessun parametro del modello, nessuno stato risiedono nel tier di presentazione

#### Scenario: Osservabilità con contenuti

- **WHEN** una interazione (chat o scaffold) attraversa il gateway
- **THEN** questa viene registrata con metadati e contenuto completo di input e output, senza modalità di registrazione ridotta: il contenuto è sempre persistito nell'archivio e consultabile dal pannello educatore

### Requirement: Pagina leggera per browser su 1 GB

La pagina SHALL minimizzare JavaScript e CSS, non caricare risorse da CDN né servizi esterni, per girare fluidamente nel browser kiosk di un Raspberry Pi 3 / 1 GB. Le icone della pagina SHALL essere SVG inline (sprite nel documento stesso): il font del kiosk non copre i glifi emoji, che quindi non si vedono.

#### Scenario: Fluidità sul Pi 3

- **WHEN** la pagina è caricata nel browser kiosk di un Pi 3 / 1 GB
- **THEN** la navigazione e l'interazione restano reattive senza saturare la memoria

#### Scenario: Icone visibili nel kiosk

- **WHEN** la pagina usa un'icona (stato, avvisi, righe della pipeline)
- **THEN** è uno SVG inline del documento o un glifo coperto dal font di sistema, mai un'emoji

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

Le chat delle tappe ①, ②, ③ e del laboratorio codice SHALL mostrare un contatore del contesto in token reali ricevuti dal modello, nel formato `uso/limite` con barra di riempimento. Il limite mostrato SHALL corrispondere alla finestra di contesto effettivamente configurata sul servizio modello. La barra SHALL segnalare visivamente l'avvicinarsi del limite.

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

Le chat delle tappe ①, ②, ③ e del laboratorio codice SHALL dichiarare visivamente i limiti imposti dal server: il tetto di token della risposta e la finestra di contesto del servizio che risponde, con l'indicazione che sono limiti server-side non modificabili dal client. Per le chat delle tappe ①–③ il tetto è quello difensivo del gateway; per il laboratorio codice il tetto è quello dedicato, più alto, dichiarato dalla sua capability. I valori mostrati MUST corrispondere a quelli effettivamente applicati dal gateway.

#### Scenario: Il ragazzo vede entrambi i limiti

- **WHEN** il ragazzo apre una delle chat delle tappe ①, ② o ③
- **THEN** l'interfaccia mostra il limite di risposta e quello di contesto, etichettati come imposti dal server

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

### Requirement: Pannello educatore consultabile

Il pannello educatore (`/admin`) SHALL mostrare l'elenco delle sessioni con identificativo client e indirizzo IP, SHALL permettere di scegliere la finestra temporale dell'elenco (5, 10, 15, 30 minuti oppure tutto lo storico persistente) e SHALL permettere di filtrare l'elenco per IP. La timeline di una sessione SHALL mostrare tutte le interazioni registrate (senza troncamenti) e, al click su una interazione, SHALL espandere il contenuto completo di input e output. L'elenco di sessioni esposto dal gateway (`GET /api/sessions`) SHALL accettare la finestra e il filtro IP come parametri, applicando vincoli lato server sui valori ammessi.

#### Scenario: Finestra selezionabile

- **WHEN** l'educatore sceglie una finestra tra 5, 10, 15, 30 minuti
- **THEN** l'elenco mostra le sessioni con attività nella finestra scelta, e la didascalia dell'elenco la riporta

#### Scenario: Finestra "tutto"

- **WHEN** l'educatore sceglie "Tutto"
- **THEN** l'elenco mostra ogni sessione presente nell'archivio persistente, anche senza attività recente o dopo un riavvio del gateway

#### Scenario: IP nel riquadro sessione

- **WHEN** l'elenco mostra una sessione
- **THEN** il riquadro riporta identificativo client e indirizzo IP da cui la sessione ha interagito

#### Scenario: Filtro per IP

- **WHEN** l'educatore filtra per un IP
- **THEN** l'elenco mostra tutte le sessioni con almeno una interazione proveniente da quell'IP, e il filtro attivo è visibile e rimovibile

#### Scenario: Interazione espandibile al click

- **WHEN** l'educatore clicca una voce della timeline di una sessione
- **THEN** la voce espande il contenuto completo dell'interazione (testo in ingresso e in uscita), riportando anche i metadati già visibili

#### Scenario: Storico di sessione non troncato

- **WHEN** una sessione accumula più di 200 interazioni nella vita del processo
- **THEN** la timeline le mostra tutte, senza troncamento

#### Scenario: Memoria trasportata visibile

- **WHEN** una interazione di chat viene registrata
- **THEN** la riga dichiara quanti messaggi la richiesta trasportava (turni): la chat senza memoria dell'esperimento a due tab resta a 1 turno, quella con memoria cresce

#### Scenario: Vista conversazione

- **WHEN** l'educatore attiva la vista conversazione della timeline
- **THEN** le interazioni di chat vengono presentate come transcript ricostruito (messaggi in ingresso e risposte in ordine cronologico), con le altre interazioni come separatori espandibili

### Requirement: Archivio sessioni persistente e pulibile

Lo storico delle sessioni e delle interazioni SHALL sopravvivere ai riavvii del gateway, incluso il rebuild degli container: all'avvio il gateway SHALL ricaricare l'archivio e il pannello SHALL mostrare elenco e timeline come prima del riavvio. Il progetto SHALL fornire un comando dedicato (`make clean-sessions`) che azzera l'archivio delle sessioni.

#### Scenario: Storico consultabile dopo un riavvio

- **WHEN** il gateway viene riavviato (o la stack ricostruita) dopo aver registrato interazioni
- **THEN** l'elenco con finestra "Tutto" e le timeline delle sessioni mostrano le interazioni registrate prima del riavvio

#### Scenario: Pulizia dell'archivio

- **WHEN** si esegue il comando dedicato alla pulizia e il gateway riparte
- **THEN** l'archivio è vuoto: nessuna sessione o interazione precedente risulta consultabile dal pannello

### Requirement: Trace della chiamata al modello visibile

Per ogni dialogo con il modello — le chat delle tappe ① Context Injection, ② System Prompt, ④ Workflow e lo scaffold della tappa ③ Skills — la pagina SHALL permettere di aprire, con un pulsante dedicato sul dialogo, una vista che mostra il JSON esattamente inviato all'endpoint del modello e il JSON restituito. Per le chat la trace riflette ciò che il gateway inoltra dopo la normalizzazione (messaggi e parametri applicati); per lo scaffold la chiamata interna della skill al modello, incluso l'eventuale vincolo di schema strutturato. Il gateway SHALL includere la trace nelle risposte delle rotte coinvolte e SHALL persistere richiesta e risposta per ogni interazione, così che il pannello educatore offra la stessa vista sulle righe della timeline e della vista conversazione, tramite un endpoint di dettaglio per interazione.

#### Scenario: Popup dal turno di chat

- **WHEN** l'utente apre il pulsante del turno nella pagina
- **THEN** la vista mostra la request (body inoltrato al servizio modello: messaggi e parametri applicati) e la response (payload grezzo restituito), pretty-printed e senza elaborazione

#### Scenario: Trace dello scaffold

- **WHEN** l'utente apre il pulsante sul risultato della skill
- **THEN** la vista mostra la chiamata al modello effettuata dalla skill — messaggi, parametri e vincolo di schema quando presente — e il payload grezzo ricevuto, non solo appunti e SkillOutput

#### Scenario: Stessa vista nel pannello educatore

- **WHEN** un'interazione con trace registrata compare nella timeline del pannello
- **THEN** la riga offre il pulsante e lo apre sulla stessa vista request/response servita dal dettaglio persistito

#### Scenario: Assenza onesta

- **WHEN** un dialogo non ha una chiamata al modello da mostrare (percorso di onboarding, modalità demo)
- **THEN** il pulsante non compare, senza placeholder né trace sintetiche

#### Scenario: Errore mostrato per ciò che è

- **WHEN** la chiamata al modello fallisce con una risposta d'errore
- **THEN** la vista mostra il body d'errore ricevuto come response

### Requirement: Simulazione di carico e grafico dei token al secondo

Il progetto SHALL fornire un comando (`make loadtest`) che simula N sessioni concorrenti di ragazzi, ognuna con una conversazione a più tappe sulle chat del gateway, producendo un report di esiti e latenze; le sessioni simulate SHALL risultare nell'osservabilità come sessioni normali. Il gateway SHALL registrare i token di prompt e completion di ogni interazione quando il servizio modello li espone, e il pannello educatore SHALL mostrare un grafico della serie tokens/secondo nel tempo, così che il degrado delle performance sotto carico sia visibile. Il grafico SHALL rappresentare la serie aggregata per fasce temporali come DUE linee — la media e il minimo di ogni fascia — senza il punta-punta di ogni singola chat raccolta. L'asse del tempo del grafico SHALL essere ancorato all'istante corrente e avanzare anche in assenza di nuove chat; la finestra del grafico SHALL corrispondere alla finestra temporale selezionata nel pannello (5m/10m/15m/30m/tutto); i 429 di backpressure SHALL essere rappresentati nel grafico come serie distinta da quella delle chat completate.

#### Scenario: Lancio della simulazione

- **WHEN** si esegue il comando di simulazione con N sessioni e T turni ciascuna
- **THEN** le conversazioni attraversano il gateway come chat normali e il report mostra esiti e latenze per sessione

#### Scenario: Le sessioni simulate sono osservabili

- **WHEN** la simulazione è in corso o appena terminata
- **THEN** il pannello educatore mostra le sessioni sintetiche come sessioni normali, con le loro interazioni

#### Scenario: Token registrati per chat

- **WHEN** una chat riceve dal servizio modello gli usage (token di prompt e completion)
- **THEN** l'interazione registrata li riporta e li persiste

#### Scenario: Grafico dei token al secondo

- **WHEN** l'educatore apre il pannello e ci sono chat con token registrati
- **THEN** il grafico mostra l'andamento tokens/secondo nel tempo come due linee (media e minimo per fascia) con legenda, senza librerie esterne

#### Scenario: Il tempo avanza anche senza chat

- **WHEN** non arrivano nuove chat (modello lento, laboratorio fermo o sotto backpressure)
- **THEN** la finestra del grafico scorre comunque con l'istante corrente: i punti escono a sinistra e il bordo destro è sempre «adesso», non il grafico congelato sull'ultima richiesta

#### Scenario: Il grafico segue la finestra selezionata

- **WHEN** l'educatore seleziona una finestra dell'elenco (5m, 10m, 15m, 30m o tutto)
- **THEN** il grafico mostra esattamente quella finestra, ridisegnato subito, con fasce di aggregazione proporzionate alla larghezza

#### Scenario: I 429 sono visibili nel grafico

- **WHEN** il gateway rifiuta chat per backpressure (429)
- **THEN** il grafico li mostra come serie separata (tacche), così il carico che non è passato è visibile quanto il ritmo di quello passato

### Requirement: Riquadro dei consumi locale vs frontiera

La pagina del laboratorio SHALL mostrare, per la sessione del ragazzo corrente, un riquadro in **forma tabellare** che confronta i consumi stimati dell'attività svolta: per riga le metriche (energia in kWh, acqua in litri, costo in €), per colonna l'esecuzione locale (acqua ≈ 0 perché il calcolo sta nel locale del campo) e la stessa attività su UN modello di frontiera (energia, acqua e costo compresi). Le stime SHALL derivare da un modello di costo dichiarato e semplificato, con costanti raccolte in un unico punto modificabile e fonti indicate.

Quando la sessione contiene anche interazioni su un endpoint remoto, il confronto locale-vs-frontiera SHALL essere calcolato sulle SOLE interazioni locali (i secondi e i token del data center remoto non sono consumi del mini PC). Quando il ragazzo si trova nel laboratorio codice con un modello remoto selezionato, il riquadro SHALL mostrare DUE tabelle: la prima per il modello scelto — i token REALI dell'usage e il costo API calcolato a LISTINO STANDARD del modello, mai a prezzo sperimentale/scontato dell'offerta (il fatto che l'endpoint sia gratis oggi non è la lezione) — e la seconda per la sessione in locale (il confronto locale-vs-frontiera di sempre, calcolato sulle sole interazioni locali). Anche per i modelli remoti le costanti di prezzo SHALL vivere nello stesso unico modulo delle altre. Il banner dello stato del modello e il riquadro dei consumi SHALL stare in fondo alla pagina, dopo il riquadro di input e prima della navigazione (non in testa, dove spingevano giù spiegazione e chat).

#### Scenario: Riquadro per la sessione del ragazzo

- **WHEN** il ragazzo riceve una risposta del modello, completa un'elaborazione della skill, o scade l'aggiornamento periodico
- **THEN** il riquadro tabellare nella pagina si aggiorna con i consumi stimati della propria sessione: locali e sul modello di frontiera, per energia, acqua e costo, calcolati sui token effettivi (chat e scaffold)

#### Scenario: Stime dichiarate e modificabili

- **WHEN** si vuole cambiare un dato di partenza (watt, costo dell'energia, prezzo o acqua del modello di frontiera, listino di un modello remoto)
- **THEN** basta modificare le costanti nell'unico modulo dedicato, senza toccare pagina o gateway

#### Scenario: Sessione senza token registrati

- **WHEN** la sessione selezionata non ha token registrati
- **THEN** il riquadro non mostra numeri fuorvianti

#### Scenario: Sessione mista: il confronto resta onesto

- **WHEN** la sessione ha chat locali e chat su endpoint remoto
- **THEN** la tabella locale-vs-frontiera è calcolata solo sulle interazioni locali: i consumi remoti non gonfiano i secondi/energia «qui, sul mini PC»

#### Scenario: Tappa ⑤ con modello remoto: due tabelle

- **WHEN** il ragazzo è nel laboratorio codice con un modello remoto selezionato
- **THEN** il riquadro mostra la tabella del modello scelto (token reali in ingresso/uscita e costo a listino) e la tabella della sessione in locale (confronto locale-vs-frontiera)

#### Scenario: Modello scelto senza risposte ancora

- **WHEN** il ragazzo ha selezionato un modello remoto ma non ha ancora ricevuto risposte da esso
- **THEN** la prima tabella lo dice senza mostrare numeri fuorvianti, e resta visibile la tabella della sessione in locale

#### Scenario: Costo a listino standard, mai sperimentale

- **WHEN** la tabella del modello scelto calcola il costo API
- **THEN** usa il listino standard dichiarato per quel modello (costanti nel modulo unico), non il prezzo dell'offerta sperimentale (che sarebbe € 0)

#### Scenario: Stato in fondo alla pagina

- **WHEN** il ragazzo scorre una qualunque tappa o il laboratorio codice
- **THEN** il banner dello stato del modello e il riquadro dei consumi stanno dopo il riquadro di input e prima della navigazione, non in testa alla pagina

#### Scenario: Tornando alle altre tappe

- **WHEN** il ragazzo riporta il selettore del laboratorio codice sul modello locale
- **THEN** il riquadro torna alla sola forma di confronto locale-vs-frontiera (calcolata sulle sole interazioni locali)

### Requirement: Backpressure con 429 al degrado del ritmo

Quando la cadenza di generazione recente (token/secondo visti dal gateway) scende sotto la soglia di sovraccarico, il gateway SHALL rispondere alle nuove chat con 429 indicando il tempo di attesa consigliato (`retry_after`). La pagina SHALL avvisare il ragazzo del sovraccarico e ritentare automaticamente la richiesta dopo l'attesa indicata, senza perdere il turno; il retry NON è una tantum: finché il gateway risponde 429 la pagina continua a ritentare, con l'attesa resa visibile da un countdown. La soglia MUST NOT scattare a freddo (poche osservazioni) né rimanere bloccata: il verdetto si basa sulle osservazioni di una finestra TEMPORALE, non sugli ultimi N punti — le osservazioni oltre l'età massima non contano e il cancello si riapre da solo, entro l'età massima della finestra dall'ultima chat lenta completata, anche se i 429 non producono nuove osservazioni. Il cancello si applica alle chat delle tappe ①–④ e alla skill: le richieste della tappa del laboratorio codice MUST NOT essere rifiutate per degrado del ritmo, e le loro osservazioni di velocità MUST NOT alimentare il verdetto del cancello (vivono in un contenitore separato, previsto dalla capability `laboratorio-code`).

#### Scenario: Degrado → 429 con retry_after

- **WHEN** le ultime chat completate sono sotto la soglia token/s e arriva una nuova richiesta
- **THEN** il gateway risponde 429 con `overload` e `retry_after` (header e corpo), invece di accodare un'altra attesa

#### Scenario: Cadenza sana o freddo → 200

- **WHEN** la cadenza recente è sana oppure non ci sono abbastanza osservazioni recenti
- **THEN** la chat procede normalmente, nessun 429

#### Scenario: Avviso e retry automatico nella pagina

- **WHEN** la risposta della chat è un 429 di sovraccarico
- **THEN** la pagina avvisa il ragazzo ("il laboratorio è sovraccarico") e ritenta da sola dopo l'attesa indicata; il turno non si perde e l'attesa resta visibile nel tempo di risposta mostrato

#### Scenario: Sovraccarico sostenuto → retry a ripetizione

- **WHEN** anche il tentativo successivo riceve 429 di sovraccarico
- **THEN** la pagina non abbandona il turno con un errore: mostra il countdown dell'attesa e riprova di nuovo, finché il gateway non risponde 200

#### Scenario: Self-healing

- **WHEN** le osservazioni lente invecchiano oltre l'età massima della finestra temporale
- **THEN** la soglia torna a non scattare finché nuove chat non dimostrino il contrario: niente lockout permanente — dopo la fine del carico i 429 cessano entro l'età massima della finestra, non restano a vita

#### Scenario: Le generazioni del laboratorio codice non alimentano il cancello

- **WHEN** il laboratorio codice completa generazioni lente (pagine intere a bassa cadenza)
- **THEN** le chat delle tappe ①–④ non subiscono 429 per colpa di quelle osservazioni: il verdetto del cancello non le vede
