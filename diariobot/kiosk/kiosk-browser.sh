#!/bin/sh
# Modalità kiosk BROWSER per Raspberry Pi 3 (piena parità con la pagina web).
# Apre un browser a schermo intero sulla pagina laboratorio del mini PC.
#
# Config via env (es. in /etc/lab-kiosk.conf o nell'unit systemd):
#   LAB_HOST       IP/hostname del mini PC sulla LAN cablata  (default: localhost)
#   LAB_PORT       porta del server web                       (default: 8090)
#
# Su Raspberry Pi OS Lite + chromium: installare `chromium-browser`.
# Lo "schermo nero" all'avvio si evita con un window manager minimale
# (openbox/fluxbox) o con X nudo; vedi README.md.

set -eu

LAB_HOST="${LAB_HOST:-localhost}"
LAB_PORT="${LAB_PORT:-8090}"
URL="http://${LAB_HOST}:${LAB_PORT}/"

# Risolve il primo browser disponibile.
for B in chromium chromium-browser google-chrome chrome firefox; do
    if command -v "$B" >/dev/null 2>&1; then
        BROWSER="$B"
        break
    fi
done

if [ -z "${BROWSER:-}" ]; then
    echo "Nessun browser trovato (chromium consigliato sul Pi). Installalo e riprova." >&2
    exit 1
fi

echo "lab-kiosk: avvio $BROWSER su $URL" >&2

case "$BROWSER" in
    firefox)
        exec "$BROWSER" --kiosk "$URL"
        ;;
    *)
        # Chromium/Chrome: kiosk pulito, niente popup, nessun ripristino sessione.
        exec "$BROWSER" \
            --kiosk \
            --noerrdialogs \
            --disable-translate \
            --no-first-run \
            --fast --fast-start \
            --disable-features=TranslateUI \
            --no-default-browser-check \
            --app="$URL"
        ;;
esac
