# Kiosk — Raspberry Pi 3 (client sottile)

Il Raspberry Pi 3 / 1 GB è un **client sottile**: non fa girare il modello.
Punta al **mini PC** (che ospita pagina + skill + modello) via **LAN cablata**.
Due modalità, con la stessa parità di funzioni:

- **Browser kiosk** (consigliato): a schermo intero sulla pagina laboratorio.
- **CLI interattiva** (fallback, più leggera): se il browser fatica sui 1 GB.

Entrambe puntano al mini PC tramite `LAB_HOST` (IP fisso sulla LAN).

> Validazione della fluidità su Pi 3 reale: **gruppo 7 (fase finale)**.
> Qui si preparano script + config, testati in locale.

## 1. Browser kiosk

```sh
# sul Pi, dopo aver installato chromium:
sudo apt install -y chromium
LAB_HOST=192.168.1.10 sh kiosk/kiosk-browser.sh
```

`kiosk-browser.sh` lancia chromium `--kiosk --app=http://<LAB_HOST>:8090/`.
Punta sempre alla **porta 8090 dell'host** (mini PC oppure Pi 3 come host:
cambia solo l'indirizzo), mai alla `:8081` del modello — quella è riservata ai
consumer fidati (CLI/debug) e bypassa normalizzazione e osservabilità del
gateway (vedi README di laboratory).

### Avvio automatico al boot

Due opzioni (sceglierne una):

**A) systemd** — più robusto, si riavvia da solo:
```sh
sudo cp kiosk-browser.sh /usr/local/bin/ && sudo chmod +x /usr/local/bin/kiosk-browser.sh
sudo cp kiosk/lab-kiosk.service /etc/systemd/system/
sudo systemctl edit lab-kiosk.service   # imposta LAB_HOST=<ip-mini-pc>
sudo systemctl enable --now lab-kiosk.service
```

**B) autostart LXDE** (Raspberry Pi OS Desktop, più semplice):
```sh
mkdir -p ~/.config/lxsession/LXDE-pi
echo "@/usr/local/bin/kiosk-browser.sh" >> ~/.config/lxsession/LXDE-pi/autostart
# imposta LAB_HOST in /etc/environment:
echo "LAB_HOST=192.168.1.10" | sudo tee -a /etc/environment
```
Abilita il desktop autologin (`sudo raspi-config` → Desktop Autologin).

## 2. CLI interattiva (fallback)

```sh
# dalla root di laboratory/, sul Pi:
LAB_HOST=192.168.1.10 python3 -m kiosk.kiosk_cli
```
Loop: chiede gli appunti (più righe → riga vuota per inviare), chiama la skill
e stampa lo scaffold. Usa lo stesso client e rendering della pagina web →
**parità di output**. Per attraversare il bridge web come la pagina:
```sh
LAB_URL=http://192.168.1.10:8090 LAB_ENDPOINT=/api/scaffold python3 -m kiosk.kiosk_cli
```

## 3. Rete (offline, cablata)

- mini PC con **IP fisso** (es. `192.168.1.10`) sulla LAN cablata.
- ogni Pi ottiene IP via DHCP dal mini PC/switch e punta a `LAB_HOST`.
- **nessun WiFi / accesso Internet richiesto.**
- Verifica raggiungibilità dal Pi: `curl -s http://192.168.1.10:8090/api/health`
  → `{"ok": true, "backend": "..."}`.

## 4. Leggerezza per 1 GB

- La pagina è **un solo file** (CSS/JS inline), zero richieste esterne.
- chromium kiosk è il caso peggiore per RAM: se un Pi 3 è al limite, usa la CLI.
- Il path **standalone** opzionale (modello 0.5B sul Pi, senza mini PC) è in
  `skill-diario-di-bordo` (task 5/7): qui non serve, il Pi è client.
