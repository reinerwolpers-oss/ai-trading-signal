"""AI Trading Signal 2.0: NYSE 5-minute monitor with ntfy alerts."""
import datetime as dt
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

import pandas_market_calendars as mcal

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'data' / 'signals.json'
SYMBOLS = ('TSLA', 'NVDA', 'AMD')
NY = ZoneInfo('America/New_York')
UTC = dt.timezone.utc
FAILURE_THRESHOLD = 3


def exchange_session(now_utc):
    calendar = mcal.get_calendar('NYSE')
    day = now_utc.astimezone(NY).date()
    schedule = calendar.schedule(start_date=day, end_date=day)
    if schedule.empty:
        return False, 'NYSE geschlossen (Wochenende oder Feiertag)'
    opening = schedule.iloc[0]['market_open'].to_pydatetime().astimezone(UTC)
    closing = schedule.iloc[0]['market_close'].to_pydatetime().astimezone(UTC)
    return opening <= now_utc < closing, f'NYSE Handelsfenster {opening.isoformat()} bis {closing.isoformat()}'


def ema(values, n):
    result = []
    current = values[0]
    weight = 2 / (n + 1)
    for value in values:
        current = value * weight + current * (1 - weight)
        result.append(current)
    return result


def rsi(values, n=14):
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    if len(changes) < n:
        raise ValueError('Zu wenig Werte fuer RSI')
    gain = sum(max(v, 0) for v in changes[:n]) / n
    loss = sum(max(-v, 0) for v in changes[:n]) / n
    for value in changes[n:]:
        gain = (gain * (n - 1) + max(value, 0)) / n
        loss = (loss * (n - 1) + max(-value, 0)) / n
    if loss == 0:
        return 100.0 if gain > 0 else 50.0
    return 100 - 100 / (1 + gain / loss)


def fetch(symbol, key, now_utc):
    params = urllib.parse.urlencode({
        'symbol': symbol, 'interval': '5min', 'outputsize': 100,
        'apikey': key, 'timezone': 'America/New_York'
    })
    request = urllib.request.Request(
        'https://api.twelvedata.com/time_series?' + params,
        headers={'User-Agent': 'AITradingSignal/2.0'})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                payload = json.load(response)
            if payload.get('status') == 'error' or not isinstance(payload.get('values'), list):
                raise RuntimeError(payload.get('message', 'Keine Kursdaten von Twelve Data'))
            break
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == 2:
                raise RuntimeError(f'API-Verbindung fehlgeschlagen: {exc}') from exc
            time.sleep(2 ** attempt)
    rows = sorted(payload['values'], key=lambda item: item['datetime'])
    now_ny = now_utc.astimezone(NY)
    finished = [row for row in rows if
                dt.datetime.fromisoformat(row['datetime']).replace(tzinfo=NY)
                + dt.timedelta(minutes=5) <= now_ny]
    if len(finished) < 35:
        raise RuntimeError(f'Zu wenig abgeschlossene Kerzen: {len(finished)}')
    last_start = dt.datetime.fromisoformat(finished[-1]['datetime']).replace(tzinfo=NY)
    if now_utc - last_start.astimezone(UTC) > dt.timedelta(minutes=25):
        raise RuntimeError(f'Kursdaten veraltet: letzte Kerze {last_start.isoformat()}')
    return finished


def publish(topic, title, message, priority='default', tags='chart_with_upwards_trend'):
    request = urllib.request.Request(
        'https://ntfy.sh/' + urllib.parse.quote(topic, safe=''),
        data=message.encode('utf-8'), method='POST',
        headers={'Title': title, 'Priority': priority, 'Tags': tags})
    with urllib.request.urlopen(request, timeout=20) as response:
        response.read()


def main():
    now = dt.datetime.now(UTC)
    is_open, detail = exchange_session(now)
    print(detail)
    if not is_open:
        print('Keine Kurspruefung ausserhalb der NYSE-Handelszeit.')
        return

    key = os.getenv('TWELVE_DATA_API_KEY', '').strip()
    topic = os.getenv('NTFY_TOPIC', '').strip()
    if not key or not topic:
        raise SystemExit('GitHub Secrets TWELVE_DATA_API_KEY und NTFY_TOPIC sind erforderlich')

    old = {}
    if OUT.exists():
        try:
            old = json.loads(OUT.read_text(encoding='utf-8'))
            if not isinstance(old, dict):
                raise ValueError('Ungueltiges JSON-Format')
        except (ValueError, OSError) as exc:
            print(f'WARNUNG: Vorheriger Zustand nicht lesbar: {exc}')
            old = {}

    results, errors = {}, {}
    sent = dict(old.get('last_alerts') or {})
    health = dict(old.get('api_health') or {})
    push_errors = []
    for symbol in SYMBOLS:
        state = dict(health.get(symbol) or {})
        failures = int(state.get('consecutive_failures', 0))
        warned = bool(state.get('warning_sent', False))
        try:
            rows = fetch(symbol, key, now)
            closes = [float(row['close']) for row in rows]
            e9, e21, r = ema(closes, 9), ema(closes, 21), rsi(closes)
            up = e9[-2] <= e21[-2] and e9[-1] > e21[-1]
            down = e9[-2] >= e21[-2] and e9[-1] < e21[-1]
            signal = ('KAUF' if up and 50 <= r <= 70 else
                      'VERKAUF' if down and 30 <= r <= 50 else 'BEOBACHTEN')
            stamp = rows[-1]['datetime']
            identifier = f'{symbol}:{signal}:{stamp}'
            results[symbol] = {
                'price': round(closes[-1], 4), 'ema9': round(e9[-1], 4),
                'ema21': round(e21[-1], 4), 'rsi14': round(r, 2),
                'signal': signal, 'candle_time_ny': stamp, 'currency': 'USD',
                'stale': False, 'checked_at_utc': now.isoformat()
            }
            # Only mark an alert as delivered after ntfy accepts it.
            if warned:
                try:
                    publish(topic, f'AI Trading Signal | {symbol} API wieder online',
                            f'{symbol}: Kursdaten wieder verfuegbar.', 'default', 'white_check_mark')
                    warned = False
                    print(f'API-Entwarnung gesendet: {symbol}')
                except Exception as exc:
                    push_errors.append(f'{symbol} Entwarnung: {exc}')
            if signal != 'BEOBACHTEN' and sent.get(symbol) != identifier:
                try:
                    publish(topic, f'AI Trading Signal | {symbol} {signal}',
                            f'{symbol}: {signal} bei {closes[-1]:.2f} USD. EMA 9/21, RSI {r:.1f}. Technisches Signal, keine Anlageberatung.')
                    sent[symbol] = identifier
                    print(f'Signal-Push gesendet: {identifier}')
                except Exception as exc:
                    push_errors.append(f'{symbol} Signal: {exc}')
            print(f'{symbol}: {signal}, {closes[-1]:.2f} USD')
            health[symbol] = {'consecutive_failures': 0, 'warning_sent': warned,
                              'last_success_utc': now.isoformat()}
        except Exception as exc:
            errors[symbol] = str(exc)
            failures += 1
            print(f'API-FEHLER {symbol} ({failures}/{FAILURE_THRESHOLD}): {exc}')
            if failures >= FAILURE_THRESHOLD and not warned:
                try:
                    publish(topic, f'AI Trading Signal | {symbol} API-Fehler',
                            f'{symbol}: {failures} aufeinanderfolgende Kurspruefungen fehlgeschlagen. Ursache: {str(exc)[:200]}',
                            'high', 'warning')
                    warned = True
                    print(f'API-Warnung gesendet: {symbol}')
                except Exception as push_exc:
                    push_errors.append(f'{symbol} API-Warnung: {push_exc}')
            health[symbol] = {'consecutive_failures': failures,
                              'warning_sent': warned,
                              'last_success_utc': state.get('last_success_utc')}
            if symbol in (old.get('symbols') or {}):
                results[symbol] = dict(old['symbols'][symbol], stale=True)

    data = {
        'updated_at_utc': now.isoformat(), 'interval': '5min',
        'symbols': results, 'errors': errors, 'last_alerts': sent,
        'api_health': health, 'notification_errors': push_errors,
        'provider': 'Twelve Data',
        'note': 'Technische Signale. Verzoegerte Daten moeglich. Keine Anlageberatung.'
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    if push_errors:
        print('PUSH-FEHLER:', '; '.join(push_errors))
    if errors:
        raise SystemExit('Kurspruefung teilweise fehlgeschlagen: ' + ', '.join(errors))
    if push_errors:
        raise SystemExit('Benachrichtigungen fehlgeschlagen')


if __name__ == '__main__':
    main()
