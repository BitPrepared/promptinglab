"""Modello di costo didattico del laboratorio (change readme-loadtest-consumi).

Stime SEMPLIFICATE e DICHIARATE per la lezione: in locale non hai modelli di
frontiera, ma ogni domanda ti costa quasi niente — e nessun data center beve
acqua per te. La frontiera la paghi in euro e in litri anche per le domande
semplici.

Tutti i dati di partenza stanno QUI: chi insegna aggiusta i numeri del suo
campo senza toccare pagina o gateway. Fonti: ordini di grandezza semplificati
dalla letteratura pubblica sui consumi energetici e idrici dei data center per
modelli linguistici di grandi dimensioni (es. "Making AI Less Thirsty", Li
et al.) e listini pubblici dei modelli di frontiera — ridotti a costanti
didattiche, non contabilità.
"""
from __future__ import annotations

# --- locale: il mini PC del campo --------------------------------------------
WATT = 35.0            # assorbimento stimato sotto carico (mini PC, misura da campo)
EURO_KWH = 0.25        # costo dell'energia elettrica

# --- frontiera: UN modello di riferimento -------------------------------------
FRONTIERA_MODELLO = "Fable 5"
# prezzo medio per milione di token, blend ingresso+uscita (semplificato)
FRONTIERA_EUR_PER_MTOKEN = 10.0
# litri d'acqua per milione di token GENERATI: costo idrico del data center
# (raffreddamento + evaporazione), ordine di grandezza dalla letteratura
FRONTIERA_ACQUA_L_PER_MTOKEN = 0.03
# kWh per milione di token GENERATI: energia di inferenza nel data center,
# overhead di raffreddamento incluso (PUE semplificato). Le stime pubbliche
# per modelli di frontiera coprono un intervallo ampio (~2–20 kWh/MLL token a
# seconda di dimensioni, redundanza e efficienza): 3.0 è il nostro ordine di
# grandezza didattico, dichiarato e modificabile
FRONTIERA_KWH_PER_MTOKEN = 3.0

# --- endpoint reale (Hetzner): LISTINO STANDARD per modello ------------------
# EUR per milione di token (ingresso, uscita) ai prezzi di listino pubblici
# delle API dei modelli open-weight (change endpoint-remoto-hetzner). NON è
# il prezzo dell'offerta sperimentale Hetzner, che oggi è gratuita: la lezione
# voluta dall'educatore è il listino standard, non la promozione. Ordini di
# grandezza dichiarati e modificabili da qui, come le altre costanti.
REMOTO_LISTINO_EUR_PER_MTOKEN = {
    # listino della serie Qwen3 30B/35B-A3B (Alibaba Cloud / DashScope)
    "Qwen/Qwen3.6-35B-A3B-FP8": (0.10, 0.40),
    # listino della serie DeepSeek flash (13B attivi: la taglia «economa»)
    "DeepSeek-V4-Flash-0731": (0.07, 0.25),
    # Kimi NON in allowlist: l'endpoint risponde «model use not permitted»
    # col nostro token (campo, 2026-08-17) — riga pronta per quando torna:
    # "Kimi-K2.7-Code": (0.55, 2.20),
}


def stima(tok_in: int, tok_out: int, secondi: float) -> dict:
    """Stima dei consumi di un'attività: locale (energia/costo/acqua ≈ 0)
    vs la stessa attività su modello di frontiera (costo API + acqua)."""
    kwh = secondi * WATT / 3_600_000.0
    return {
        "tok_in": tok_in,
        "tok_out": tok_out,
        "secondi": round(secondi, 3),
        "locale": {
            # 9/6 decimali: i consumi locali di UNA chat sono honestamente
            # minuscoli (10^-8 kWh) — la lezione è proprio questa
            "kwh": round(kwh, 9),
            "euro": round(kwh * EURO_KWH, 6),
            # il calcolo sta nel locale del campo: il "costo idrico" locale è
            # quello dell'energia elettrica già contato in euro, non aggiungiamo
            # litri che non evaporano per te
            "acqua_l": 0.0,
        },
        "frontiera": {
            "modello": FRONTIERA_MODELLO,
            # si paga tutto ciò che viaggia: prompt e risposta
            "euro": round((tok_in + tok_out) / 1_000_000.0 * FRONTIERA_EUR_PER_MTOKEN, 4),
            # acqua ed energia seguono i token GENERATI (l'inferenza nel data
            # center): il confronto col locale dev'essere completo
            "acqua_l": round(tok_out / 1_000_000.0 * FRONTIERA_ACQUA_L_PER_MTOKEN, 6),
            "kwh": round(tok_out / 1_000_000.0 * FRONTIERA_KWH_PER_MTOKEN, 6),
        },
    }


def stima_remota(modello: str, tok_in: int, tok_out: int) -> dict:
    """Costo API a listino STANDARD dei token REALI consumati sull'endpoint
    remoto (change endpoint-remoto-hetzner): qui niente è stimato tranne il
    prezzo, che è il listino dichiarato per il modello scelto. Un modello
    fuori listino costa 0.0 dichiarato, mai un prezzo inventato."""
    eur_in, eur_out = REMOTO_LISTINO_EUR_PER_MTOKEN.get(modello, (0.0, 0.0))
    return {"modello": modello, "tok_in": tok_in, "tok_out": tok_out,
            "euro": round(tok_in / 1_000_000.0 * eur_in
                          + tok_out / 1_000_000.0 * eur_out, 6)}
