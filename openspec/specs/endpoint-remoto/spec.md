## Purpose

Permette in tappa ⑤ il confronto reale tra il modello locale e modelli open-weight di taglia superiore serviti da un endpoint remoto (Inference API Hetzner), rispettando sempre i limiti d'uso del servizio tramite un circuito di protezione gestito dall'educatore, senza mai esporre il token al client.

## Requirements

### Requirement: Configurazione del token dell'endpoint reale

L'accesso all'endpoint remoto SHALL richiedere un token Bearer configurato in un file `.env` accanto al compose, passato al solo processo gateway e MAI incluso nel codice, nel repository o inviato al client. Il file del token SHALL essere ignorato da git e la sua configurazione documentata nel README. Senza token, il laboratorio SHALL comportarsi esattamente come prima del change: nessun selettore, tutte le funzioni esistenti intatte.

#### Scenario: Token assente

- **WHEN** lo stack parte senza token configurato
- **THEN** nessuna funzione dell'endpoint remoto è visibile o raggiungibile, e il laboratorio funziona come oggi (demo/mock compresi)

#### Scenario: Token presente solo nel gateway

- **WHEN** il token è configurato nel `.env`
- **THEN** arriva come variabile d'ambiente al solo service gateway; la pagina e gli altri tier non hanno modo di conoscerlo

#### Scenario: Il file del token resta fuori dal repository

- **WHEN** si crea il file `.env` con il token
- **THEN** git lo ignora, e il README documenta come crearlo e quale variabile aspetta

### Requirement: Selettore del modello in tappa ⑤

Il laboratorio codice (pagina dedicata della quinta tappa, step `"code"`) SHALL offrire un selettore tra il modello locale e i modelli remoti dell'endpoint (allowlist dichiarata lato server: `Qwen/Qwen3.6-35B-A3B-FP8` e `DeepSeek-V4-Flash-0731`; modelli che l'endpoint rifiuta per il token — es. Kimi-K2.7-Code, «model use not permitted», verificato al campo — MUST NOT restare nell'allowlist), visibile SOLO quando: token configurato, interruttore dell'educatore su ON, circuito di protezione non scattato. Il default SHALL essere il modello locale. Il cambio modello a conversazione avviata SHALL azzerare la conversazione (il prompt seme resta nell'input), come già accade per i preset di tappa ②. Il selettore SHALL mostrare il budget di richieste disponibile nella finestra corrente (es. «7/10»). Nessuna chat delle tappe ①–④ SHALL esporre modelli remoti.

#### Scenario: Condizioni soddisfatte

- **WHEN** il ragazzo apre il laboratorio codice da una postazione abilitata, con token configurato, interruttore ON e protezione non scattata
- **THEN** il selettore offre il modello locale (default) e i due modelli remoti dell'allowlist, con il budget richieste visibile; le opzioni remote dichiarano il PROVIDER (es. «Hetzner · Qwen3.6-35B»), come quella locale dichiara «Modello locale»

#### Scenario: Nessun token, nessun selettore

- **WHEN** il token non è configurato
- **THEN** nel laboratorio codice non esiste alcun selettore: si genera solo col modello locale

#### Scenario: Interruttore educatore OFF

- **WHEN** l'educatore spegne l'endpoint reale dal pannello admin
- **THEN** il selettore sparisce dal laboratorio codice e la chat torna al modello locale, anche a conversazione aperta

#### Scenario: Protezione scattata

- **WHEN** il circuito di protezione è scattato
- **THEN** il selettore è bloccato col motivo visibile e il ragazzo non può inviare richieste remote finché l'educatore non sblocca

#### Scenario: Cambio modello a conversazione avviata

- **WHEN** il ragazzo cambia modello nel selettore con una conversazione in corso
- **THEN** la conversazione si azzera e il prompt seme torna nell'input, pronto per essere riprovato sul nuovo modello

#### Scenario: Solo la tappa ⑤

- **WHEN** una chat delle tappe ①–④ invoca il gateway
- **THEN** non c'è modo di selezionare un modello remoto: l'offerta e l'enforcement vivono nel laboratorio codice e nel gateway

### Requirement: Chiamate all'endpoint reale tramite gateway

Le chat con modello remoto SHALL passare dal gateway, che valida il campo `model` contro l'allowlist, lo inoltra all'endpoint remoto con autenticazione Bearer e un body solo OpenAI standard (niente parametri propri del backend locale), e applica le stesse normalizzazioni difensive delle chat locali (clamp temperatura, tetto token per tappa, ruoli). Richieste con modello fuori allowlist o provenienti da una tappa diversa da quella del laboratorio codice SHALL essere rifiutate dal gateway; le richieste remote del laboratorio codice MUST ALSO rispettare il gate di accesso per IP della tappa. La risposta verso la pagina SHALL avere la stessa forma delle chat locali (reply, usage, finish_reason, trace `{ }`), con usage REALI dell'endpoint. Il token Bearer MUST NOT comparire in alcuna risposta, trace o log verso il client.

#### Scenario: Richiesta valida inoltrata

- **WHEN** il laboratorio codice invia una chat con un modello dell'allowlist, da una postazione abilitata
- **THEN** il gateway inoltra all'endpoint remoto un body standard (messages, temperature, max_tokens, stream=false) con il modello scelto, e la pagina riceve reply/usage/finish_reason/trace nella forma già nota

#### Scenario: Modello fuori allowlist

- **WHEN** una richiesta porta un `model` non presente nell'allowlist
- **THEN** il gateway la rifiuta con errore, senza contattare alcun endpoint esterno

#### Scenario: Modello remoto richiesto fuori dalla tappa ⑤

- **WHEN** una richiesta con `model` arriva con l'intestazione di una tappa diversa da quella del laboratorio codice
- **THEN** il gateway la rifiuta: il vincolo è del server, non del client

#### Scenario: Postazione non abilitata

- **WHEN** una richiesta remota arriva da un IP fuori dall'allowlist del laboratorio codice
- **THEN** il gateway la rifiuta come le richieste locali di quella tappa: nessuna chiamata parte verso l'endpoint esterno

#### Scenario: Il token non attraversa il confine

- **WHEN** un ragazzo apre la trace `{ }` di una chat remota o ispeziona qualsiasi risposta del gateway
- **THEN** il Bearer non compare da nessuna parte: la pagina conosce solo il nome del modello scelto

#### Scenario: Token reali in response

- **WHEN** una chat remota riceve risposta
- **THEN** gli usage esposti (prompt/completion token) sono quelli riportati dall'endpoint, non stime

### Requirement: Circuito di protezione sui limiti dell'endpoint

Il gateway SHALL far rispettare sempre i limiti dichiarati dall'endpoint remoto (10 richieste / 4M token in ingresso / 100k token in uscita, finestra 60 s per API key) contando richieste e token in una finestra scorrevole condivisa da tutto il laboratorio. Lo scatto SHALL essere predittivo: la richiesta che violerebbe un limite non parte MAI. Uno scatto SHALL anche seguire un eventuale 429 reale ricevuto dall'endpoint (es. stesso token usato altrove). Una volta scattato, il circuito resta OFF fino a sblocco manuale dell'educatore; la finestra invecchia naturalmente, quindi lo sblocco è onesto solo se le richieste vecchie sono uscite dai 60 s. L'esito verso la pagina SHALL essere distinto dall'overload locale: la pagina MUST NOT auto-ritentare, e mostra al ragazzo di avvisare l'educatore.

#### Scenario: Scatto predittivo al limite richieste

- **WHEN** nella finestra di 60 s sono già partite 10 richieste remote e arriva l'11ª
- **THEN** l'11ª non viene inoltrata: il circuito scatta PRIMA dell'invio e la risposta dice all'educatore perché

#### Scenario: Scatto su 429 reale

- **WHEN** l'endpoint remoto risponde 429 a una richiesta inoltrata
- **THEN** il circuito scatta e le successive richieste remote non partono, senza ulteriori tentativi automatici

#### Scenario: Sticky fino a sblocco

- **WHEN** il circuito è scattato e passano i 60 s (la finestra si svuota)
- **THEN** il circuito resta comunque OFF finché l'educatore non lo sblocca dal pannello admin

#### Scenario: Sblocco onesto

- **WHEN** l'educatore sblocca il circuito dopo che le richieste della finestra sono invecchiate
- **THEN** le chat remote ripartono; se invece la finestra è ancora piena, la prossima richiesta predittiva fa scattare di nuovo

#### Scenario: Nessun auto-retry dal client

- **WHEN** una chat remota riceve l'esito «circuito scattato»
- **THEN** la pagina non avvia countdown né retry (a differenza dell'overload locale) e dice al ragazzo di avvisare l'educatore

#### Scenario: Stato sopravvive al riavvio

- **WHEN** il gateway si riavve con il circuito scattato
- **THEN** il circuito risulta ancora scattato e la finestra 60 s è ricostruita dalle interazioni remote persistite

### Requirement: Comandi dell'educatore sull'endpoint reale

Il pannello admin SHALL ospitare un riquadro «Endpoint reale» con lo stato corrente (OFF / attivo / scattato, col motivo dello scatto: richieste, token o 429), il consumo della finestra 60 s in tempo reale (richieste e token contro i limiti), i TOTALI storici dell'endpoint (richieste e token scambiati dall'inizio, su tutte le sessioni) e due comandi: interruttore on/off per tutta la sala e sblocco del circuito. L'interruttore SHALL default a OFF. Entrambi gli stati (interruttore e circuito) SHALL sopravvivere a riavvii e rebuild del gateway.

#### Scenario: Riquadro di stato

- **WHEN** l'educatore apre il pannello admin con token configurato
- **THEN** il riquadro mostra stato, motivo eventuale, il consumo della finestra (es. richieste 7/10) e i totali dall'inizio (richieste e token), aggiornati

#### Scenario: Accensione per la sala

- **WHEN** l'educatore porta l'interruttore su ON
- **THEN** tutti i kiosk con la tappa ⑤ aperta offrono il selettore al refresh di stato successivo, senza ricaricare la pagina

#### Scenario: Spegnimento a laboratorio aperto

- **WHEN** l'educatore porta l'interruttore su OFF mentre ragazzi chattano con modello remoto
- **THEN** il selettore sparisce e le chat tornano al modello locale; nessuna richiesta remota parte più

#### Scenario: Stati persistenti

- **WHEN** il gateway viene riavviato o ricostruito
- **THEN** interruttore e stato del circuito mantengono il valore che avevano

### Requirement: Sessioni remote evidenziate nel pannello

Le interazioni svolte con modello remoto SHALL essere marcate come tali nell'osservabilità (persistenza inclusa) ed evidenziate nel pannello educatore: riga di timeline con badge dell'endpoint reale e nome del modello, e sessione marcata nella lista quando contiene almeno un'interazione remota. Le interazioni preesistenti al change (senza marca) continuano a intendersi locali.

#### Scenario: Riga di timeline evidenziata

- **WHEN** l'educatore apre la timeline di una sessione che ha usato un modello remoto
- **THEN** le interazioni remote si distinguono dalle locali con un badge che riporta l'endpoint reale e il nome del modello

#### Scenario: Sessione marcata nella lista

- **WHEN** una sessione contiene almeno un'interazione remota
- **THEN** nella lista sessioni del pannello la sessione è evidenziata come «con endpoint reale»

#### Scenario: Storico preesistente

- **WHEN** il pannello mostra sessioni registrate prima del change
- **THEN** nessuna risulta remota: l'assenza di marca significa locale

### Requirement: Misure locali non inquinate dalle chat remote

Le chat con modello remoto SHALL essere escluse dalle misure di ritmo del modello locale: non producono punti nella serie tokens/secondo del grafico, non alimentano la mediana di backpressure, e le chat remote a loro volta non sono bloccate dalla backpressure locale (i due servizi non condividono i colli di bottiglia). Le correttezza delle misure locali MUST rimanere invariata.

#### Scenario: Nessun punto remoto nel grafico t/s

- **WHEN** arrivano chat remote mentre il grafico tokens/secondo è visibile
- **THEN** il grafico mostra solo il ritmo delle chat locali: le remote non compaiono come punti

#### Scenario: La mediana di backpressure resta locale

- **WHEN** una raffica di chat remote veloci attraversa il gateway
- **THEN** la mediana di backpressure non cambia: un eventuale affanno del modello locale continua a produrre 429 locali

#### Scenario: L'affanno locale non blocca il cloud

- **WHEN** il modello locale è in backpressure (429 locali attivi) e arriva una chat remota valida
- **THEN** la chat remota procede per la sua strada: i due percorsi sono indipendenti

#### Scenario: Un errore remoto non spegne il locale

- **WHEN** una chat remota fallisce (rete verso l'endpoint, errore dell'endpoint, circuito scattato)
- **THEN** le chat locali restano attive e il banner di stato del modello locale non cambia
