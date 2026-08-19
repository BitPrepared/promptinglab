# laboratorio-code Specification

## Purpose

La tappa «⑤ Prompt Engineering» del percorso di prompting diventa un laboratorio autonomo su pagina dedicata (`code.html`): il modello genera una pagina web completa — HTML e CSS in un unico file — che il ragazzo porta via con un click, su postazioni abilitate dall'educatore tramite allowlist di IP configurabile dal pannello.

## Requirements

### Requirement: Pagina dedicata del laboratorio codice

La pagina `code.html` SHALL offrire l'esperienza completa della generazione di codice: chat con prompt seme orientato a una pagina web completa, anteprima dell'HTML generato e azioni rapide sull'artefatto (copiare, scaricare, aprire). L'anteprima SHALL eseguire l'HTML generato in una sandbox senza script eseguibili e senza accesso same-origin. La pagina MUST rispettare i vincoli delle altre pagine del laboratorio: nessuna risorsa da CDN o servizi esterni (salvo endpoint remoti dichiarati dal gateway), icone SVG inline mai emoji (il font del kiosk non le copre), leggerezza per il browser kiosk del campo. Ogni interazione col modello SHALL passare dagli endpoint `/api/*` del gateway.

#### Scenario: Ragazzo genera una pagina

- **WHEN** un ragazzo su una postazione abilitata invia il prompt seme (o il proprio prompt)
- **THEN** riceve in risposta una pagina HTML completa e può guardarla nell'anteprima prima di portarla via

#### Scenario: Anteprima in sandbox

- **WHEN** l'anteprima è attiva
- **THEN** l'HTML generato gira in un iframe sandbox: nessuno script eseguito, nessun accesso same-origin al laboratorio

#### Scenario: Pagina leggera e offline

- **WHEN** la pagina è aperta nel browser kiosk del campo
- **THEN** non carica risorse esterne e le sue icone sono SVG inline del documento, mai emoji

### Requirement: Parametri espliciti nel laboratorio codice

La chat del laboratorio codice SHALL esporre un controllo di temperatura regolabile entro i limiti ammessi dal gateway (0–1.5), con valore predefinito basso orientato all'aderenza, e SHALL inviare il valore selezionato con ogni richiesta. La pagina SHALL dichiarare i limiti effettivi della tappa — tetto di token della risposta e finestra di contesto del servizio che risponde — come imposti dal server e non modificabili dal client; i valori mostrati MUST corrispondere a quelli effettivamente applicati dal gateway. I valori applicati dopo la normalizzazione restano osservabili nella trace della chiamata.

#### Scenario: Il ragazzo regola la temperatura

- **WHEN** il ragazzo sposta il controllo e invia un messaggio
- **THEN** la richiesta porta la temperatura selezionata, visibile nella trace della chiamata

#### Scenario: Default di aderenza

- **WHEN** il laboratorio si apre
- **THEN** il controllo parte da un valore basso di aderenza, non dal default alto delle chat libere

#### Scenario: Limiti dichiarati = limiti applicati

- **WHEN** il ragazzo consulta i limiti dichiarati nella pagina
- **THEN** corrispondono a quelli che il gateway applica a quella tappa (tetto token dedicato e contesto del servizio), visibili anche in trace

### Requirement: Accesso riservato alle postazioni abilitate

Le richieste di generazione del laboratorio codice SHALL essere accettate solo se l'indirizzo IP del client compare in un'allowlist di indirizzi esatti, configurabile a runtime dal pannello educatore e persistita (sopravvive ai riavvii del gateway). La policy SHALL vivere nel gateway: la pagina al massimo mostra. Un client non abilitato SHALL ricevere un rifiuto chiaro, senza che alcuna generazione parta, e la pagina SHALL dichiararlo in modo amichevole. Lo stato esposto ai client generici SHALL riportare solo l'esito per chi chiede (abilitato sì/no), MAI la lista degli IP.

#### Scenario: Postazione abilitata

- **WHEN** un client il cui IP è nell'allowlist invia una richiesta di generazione
- **THEN** il gateway la inoltra al servizio modello come di consueto

#### Scenario: Postazione non abilitata

- **WHEN** un client il cui IP non è nell'allowlist invia una richiesta di generazione
- **THEN** il gateway rifiuta con un errore chiaro, nessuna chiamata parte verso il modello, e la pagina spiega che il laboratorio non è attivo per quella postazione

#### Scenario: Modifica a runtime

- **WHEN** l'educatore aggiunge o rimuove un IP dal pannello
- **THEN** la nuova policy vale dal salvataggio, senza riavvii

#### Scenario: Allowlist persistente

- **WHEN** il gateway si riavvia
- **THEN** l'allowlist salvata è ancora in vigore

#### Scenario: La lista non arriva ai client

- **WHEN** un client qualunque interroga lo stato del laboratorio
- **THEN** conosce solo il proprio esito (abilitato sì/no), mai l'elenco degli IP abilitati

### Requirement: Pagina web completa in un file unico

Il laboratorio codice SHALL chiedere al modello una pagina HTML completa, con il CSS incorporato in un blocco `<style>` dentro lo stesso file: un unico artefatto autonomo, senza riferimenti ad asset esterni, apribile offline dal browser. Il gateway SHALL applicare alla tappa del laboratorio codice un tetto di generazione dedicato (4096 token), superiore al soffitto delle altre chat, che resta invariato per le tappe ①–④. La pagina MUST segnalare quando la risposta è stata troncata dal limite (`finish_reason: "length"`), riconducendola al tetto dichiarato.

#### Scenario: Artefatto in un solo file

- **WHEN** una generazione si completa
- **THEN** l'artefatto è un file HTML autonomo con il CSS incorporato: salvandolo e aprendolo offline, la pagina si presenta completa, senza asset mancanti

#### Scenario: Tetto dedicato

- **WHEN** la chat del laboratorio codice chiede al gateway senza specificare un tetto token
- **THEN** il gateway applica il tetto dedicato della tappa (4096), dichiarato nell'interfaccia e visibile nella trace; le chat delle tappe ①–④ mantengono il tetto basso

#### Scenario: Troncamento riconoscibile

- **WHEN** la generazione si interrompe al tetto token (`finish_reason: "length"`)
- **THEN** la pagina lo segnala e il ragazzo può ricondurre il taglio al limite dichiarato, non a un guasto

### Requirement: Copia rapida dell'artefatto

La pagina SHALL affiancare alla risposta generata un'azione di copia che riporta negli appunti l'intero artefatto, senza selezione manuale del testo, funzionante anche quando la pagina è servita su LAN HTTP (contesto non sicuro in cui l'API clipboard del browser non è disponibile). L'azione SHALL dare un feedback immediato e visibile dell'avvenuta copia. La pagina SHALL offrire anche lo scarico dell'artefatto come file `.html` ed eventualmente la sua apertura in nuova scheda: tre strade per lo stesso file unico.

#### Scenario: Copia in un click

- **WHEN** il ragazzo preme l'azione di copia dopo una generazione
- **THEN** l'intero artefatto finisce negli appunti senza che debba selezionare nulla, anche su LAN HTTP

#### Scenario: Feedback della copia

- **WHEN** la copia è avvenuta
- **THEN** l'azione lo dichiara visibilmente (con icona/testo, mai emoji) prima di tornare allo stato normale

#### Scenario: Scarico del file

- **WHEN** il ragazzo scarica l'artefatto
- **THEN** ottiene un file `.html` che, aperto dal browser anche offline, mostra la pagina completa

### Requirement: Una generazione alla volta, a qualsiasi velocità

Il gateway SHALL ammettere al massimo una generazione locale alla volta per la tappa del laboratorio codice: le richieste in eccesso ricevono 429 con `retry_after`, e la pagina avvisa il ragazzo e riprova da sola senza perdere il turno. Il gateway MUST NOT rifiutare le generazioni di questa tappa per lentezza del modello: qualsiasi cadenza di generazione (anche inferiore a 1 token/s) procede fino a un timeout dedicato, esteso oltre quello delle altre chat; se anche quello scade, la risposta SHALL essere un errore chiaro in JSON (mai una pagina HTML di proxy). Le osservazioni di velocità di questa tappa SHALL restare in un contenitore separato e MUST NOT influenzare il cancello di backpressure delle chat delle altre tappe.

#### Scenario: Seconda richiesta mentre si genera

- **WHEN** una generazione è in corso e un'altra postazione abilitata invia la propria
- **THEN** la seconda riceve 429 con `retry_after` e la pagina mostra l'attesa, senza perdere il turno

#### Scenario: La lentezza non è un rifiuto

- **WHEN** il modello genera a cadenza bassissima (anche <1 token/s)
- **THEN** la generazione prosegue comunque: nessun 429 per lentezza su questa tappa

#### Scenario: Il cancello delle altre tappe resta pulito

- **WHEN** il laboratorio codice completa generazioni lente
- **THEN** le chat delle tappe ①–④ non vengono rifiutate per backpressure: il verdetto del cancello non vede i tempi di questa tappa

#### Scenario: Timeout onesto

- **WHEN** una generazione supera anche il timeout dedicato
- **THEN** il ragazzo riceve un errore JSON chiaro che invita a riprovare, e la riga resta registrata nell'osservabilità

### Requirement: Modello che risponde, con fallback e scelta dichiarati

Le chat locali della tappa del laboratorio codice SHALL essere instradate al servizio modello dedicato al codice quando attivo; le altre tappe e la skill continuano sul modello principale, che non cambia percorso. Se il servizio dedicato non è attivo o irraggiungibile, la tappa SHALL ricadere sul modello principale senza errori visibili al ragazzo (fallback trasparente, anche a richiesta già partita se la connessione cade). L'opzione «Modello locale» del selettore SHALL dichiarare quale modello locale risponde davvero (il dedicato quando attivo, il principale altrimenti).

#### Scenario: Instradamento al dedicato

- **WHEN** il laboratorio codice manda una chat locale col servizio dedicato attivo
- **THEN** la richiesta arriva al modello dedicato (coi parametri locali di sempre) e il principale non viene toccato

#### Scenario: Le altre tappe non cambiano percorso

- **WHEN** una chat delle tappe ①–④ arriva col servizio dedicato attivo
- **THEN** viene servita dal modello principale, come sempre

#### Scenario: Fallback a freddo

- **WHEN** il servizio dedicato non è attivo
- **THEN** il laboratorio codice usa il modello principale senza alcun errore per il ragazzo

#### Scenario: Etichetta onesta nella tendina

- **WHEN** il selettore mostra l'opzione locale
- **THEN** dichiara il nome del modello che risponde davvero in quella tappa
