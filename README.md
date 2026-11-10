# AI Trading Signal 2.0

## Installation

1. Alle Dateien und Ordner aus dem ZIP **entpacken** und in das **Root-Verzeichnis** des GitHub-Repositories hochladen. Die Ordnerstruktur `.github/workflows/monitor.yml` und `data/signals.json` muss erhalten bleiben. Auf Windows können versteckte Ordner ausgeblendet sein.
2. Unter **Settings → Secrets and variables → Actions → New repository secret** den Secret `TWELVE_DATA_API_KEY` mit deinem persönlichen Schlüssel von https://twelvedata.com/ anlegen.
3. Auf dem iPhone die App **ntfy** installieren und ein langes, zufälliges Topic abonnieren. Zweites GitHub Secret `NTFY_TOPIC` mit exakt diesem Topic anlegen (ohne https://ntfy.sh/).
4. Unter **Actions → AI Trading Signal Monitor → Run workflow** den ersten Lauf manuell starten. Falls GitHub Actions nicht aktiviert ist, aktivieren.
5. Unter **Settings → Pages** die Veröffentlichung aus `main` und `/(root)` aktiv lassen. Die App wird unter der bisherigen GitHub-Pages-Adresse erreichbar.

## Wichtige Einschränkungen

- GitHub Actions startet nominell alle 5 Minuten, kann jedoch verzögert oder ausgelassen werden. Der Cron-Zeitbereich 13–22 UTC, Montag–Freitag, schließt US-Handelszeiten während Sommer-/Winterzeit ein. Außerhalb dieser Stunden werden keine Daten abgefragt. Ein Tageslimit von 800 Credits ist bei 3 Symbolen und 120 geplanten Läufen (360 Credits) normalerweise ausreichend, sofern der Endpunkt pro Symbol 1 Credit kostet. Anbieter-Limits prüfen.
- `Twelve Data Basic` ist nur für interne Tests/Entwicklung bzw. nicht öffentliche Darstellung lizenziert. Das öffentliche Dashboard kann gegen die Lizenz verstoßen, wenn echte Basic-Kurse öffentlich angezeigt werden. Verwende dieses Paket nur, wenn du die erforderlichen Datenrechte hast; sonst Datenquelle/Lizenz wechseln oder auf eine öffentliche Kursanzeige verzichten.
- ntfy.sh-Themen sind ohne Authentifizierung öffentlich lesbar, wenn der Themenname bekannt ist. Keine persönlichen Daten oder Secrets senden.
- Die App zeigt **nur technische Signale** auf abgeschlossenen 5-Minuten-Kerzen. Ein Signal wird nur beim frischen EMA-9/21-Kreuzen und RSI 50–70 (Kauf) bzw. 30–50 (Verkauf) ausgelöst. Kein ML-/KI-Modell und keine Orderausführung.
- Kursangaben sind USD. Der Risikorechner erwartet Euro-Einstiegs- und Stop-Loss-Werte. Wechselkurse, Spreads, Gebühren und Slippage sind nicht automatisch berücksichtigt.
- Bei Anbieterfehlern bleiben vorherige Daten sichtbar, mit Fehlermeldung. Der Zeitpunkt der Kerze zeigt, wie alt die Werte sind.
