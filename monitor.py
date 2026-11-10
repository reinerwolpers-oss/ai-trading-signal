import datetime as dt
import json
import os
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

import pandas_market_calendars as mcal

ROOT = pathlib.Path(__file__).resolve().parent
OUT = ROOT / 'data' / 'signals.json'
SYMBOLS = ('TSLA', 'NVDA', 'AMD')
NY = ZoneInfo('America/New_York')
UTC = dt.timezone.utc


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
    out = []
    value = values[0]
    k = 2 / (n + 1)
    for x in values:
        value = x * k + value * (1 - k)
        out.append(value)
    return out


def rsi(values, n=14):
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    if len(changes) < n:
        raise ValueError('Zu wenig Werte fuer RSI')
    gain = sum(max(x, 0) for x in changes[:n]) / n
    loss = sum(max(-x, 0) for x in changes[:n]) / n
    for x in changes[n:]:
        gain = (gain * (n - 1) + max(x, 0)) / n
        loss = (loss * (n - 1) + max(-x, 0)) / n
    if loss == 0:
        return 100.0 if gain > 0 else 50.0
    return 100 - 100 / (1 + gain / loss)


def fetch(symbol, key, now_utc):
    params = urllib.parse.urlencode({
        'symbol': symbol, 'interval': '5min', 'outputsize': 100,
        'apikey': key, 'timezone': 'America/New_York'
    })
    url = 'https://api.twelvedata.com/time_series?' + params
    request = urllib.request.Request(url, headers={'User-Agent': 'AITradingSignal/2.1'})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                obj = json.load(response)
            if obj.get('status') == 'error' or 'values' not in obj:
                raise RuntimeError(obj.get('message', 'Keine Kursdaten von Twelve Data'))
            break
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == 2:
                raise RuntimeError(f'API-Verbindung fehlgeschlagen: {exc}') from exc
            time.sleep(2 ** attempt)
    rows = sorted(obj['values'], key=lambda v: v['datetime'])
    finished = []
    for row in rows:
        candle_start = dt.datetime.fromisoformat(row['datetime']).replace(tzinfo=NY)
        if candle_start + dt.timedelta(minutes=5) <= now_utc.astimezone(NY):
            finished.append(row)
    if len(finished) < 35:
        raise RuntimeError(f'Zu wenig abgeschlossene Kerzen: {len(finished)}')
    last_start = dt.datetime.fromisoformat(finished[-1]['datetime']).replace(tzinfo=NY)
    if now_utc - last_start.astimezone(UTC) > dt.timedelta(minutes=25):
        raise RuntimeError(f'Kursdaten veraltet: letzte Kerze {last_start.isoformat()}')
    return finished


def publish(topic, title, message, priority='default', tags='chart_with_upwards_trend'):
    url = 'https://ntfy.sh/' + urllib.parse.quote(topic, safe='')
    request = urllib.request.Request(url, data=message.encode('utf-8'), method='POST', headers={
        'Title': title, 'Priority': priority, 'Tags': tags
    })
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
        except (ValueError, OSError):
            print('Warnung: Bisherige signals.json nicht lesbar')

    results, errors = {}, {}
    sent = dict(old.get('last_alerts', {}))
    for symbol in SYMBOLS:
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
            if signal != 'BEOBACHTEN' and sent.get(symbol) != identifier:
                publish(topic, f'AI Trading Signal | {symbol} {signal}',
                        f'{symbol}: {signal} bei {closes[-1]:.2f} USD. EMA 9/21, RSI {r:.1f}. Technisches Signal, keine Anlageberatung.')
                sent[symbol] = identifier
                print(f'Push gesendet: {identifier}')
            else:
                print(f'{symbol}: {signal}, {closes[-1]:.2f} USD')
        except Exception as exc:
            errors[symbol] = str(exc)
            print(f'FEHLER {symbol}: {exc}')
            if symbol in old.get('symbols', {}):
                results[symbol] = dict(old['symbols'][symbol], stale=True)

    data = {
        'updated_at_utc': now.isoformat(), 'interval': '5min',
        'symbols': results, 'errors': errors, 'last_alerts': sent,
        'provider': 'Twelve Data',
        'note': 'Technische Signale. Verzoegerte Daten moeglich. Keine Anlageberatung.'
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    if errors:
        raise SystemExit('Kurspruefung teilweise fehlgeschlagen: ' + ', '.join(errors))


if __name__ == '__main__':
    main()
import json, os, urllib.request, urllib.parse, datetime, pathlib
ROOT=pathlib.Path(__file__).parent
OUT=ROOT/'data'/'signals.json'
SYMBOLS=['TSLA','NVDA','AMD']


from zoneinfo import ZoneInfo

def market_is_open():
    now = datetime.datetime.now(
        ZoneInfo("America/New_York")
    )

    if now.weekday() >= 5:
        return False

    opening = datetime.time(9, 30)
    closing = datetime.time(16, 0)

    return opening <= now.time() < closing


def ema(values,n):
    v=values[0]; out=[]; k=2/(n+1)
    for x in values:
        v=x*k+v*(1-k);out.append(v)
    return out

def rsi(values,n=14):
    changes=[values[i]-values[i-1] for i in range(1,len(values))]
    if len(changes)<n:return None
    gain=sum(max(x,0) for x in changes[:n])/n
    loss=sum(max(-x,0) for x in changes[:n])/n
    for x in changes[n:]:
        gain=(gain*(n-1)+max(x,0))/n
        loss=(loss*(n-1)+max(-x,0))/n
    return 100 if loss==0 else 100-100/(1+gain/loss)

def fetch(symbol,key):
    params=urllib.parse.urlencode({'symbol':symbol,'interval':'5min','outputsize':100,'apikey':key,'timezone':'America/New_York'})
    req=urllib.request.Request('https://api.twelvedata.com/time_series?'+params,headers={'User-Agent':'AITradingSignal/2.0'})
    with urllib.request.urlopen(req,timeout=25) as response: obj=json.load(response)
    if obj.get('status')=='error' or 'values' not in obj:raise RuntimeError(obj.get('message','Keine Kursdaten'))
    rows=sorted(obj['values'],key=lambda v:v['datetime'])
    # Exclude the current, potentially incomplete candle: only include bars whose end is <= now NY.
    from zoneinfo import ZoneInfo
    now=datetime.datetime.now(ZoneInfo('America/New_York'))
    rows=[v for v in rows if datetime.datetime.fromisoformat(v['datetime']).replace(tzinfo=ZoneInfo('America/New_York'))+datetime.timedelta(minutes=5)<=now]
    if len(rows)<35:raise RuntimeError('Weniger als 35 abgeschlossene Kerzen')
    return rows

def publish(topic,symbol,signal,price,stamp):
    msg=f'{symbol}: {signal} bei {price:.2f} USD (5-Minuten-EMA 9/21, RSI-Filter). Keine Anlageberatung.'
    req=urllib.request.Request('https://ntfy.sh/'+urllib.parse.quote(topic,safe=''),data=msg.encode(),method='POST',headers={'Title':'AI Trading Signal','Priority':'default','Tags':'chart_with_upwards_trend'})
    with urllib.request.urlopen(req,timeout=20) as r:r.read()

def main():
    
    if not market_is_open():
        print("US-Boerse geschlossen. Keine Kurspruefung.")
        return

    old={}
    if OUT.exists():
        try:old=json.loads(OUT.read_text())
        except Exception:pass
    key=os.environ.get('TWELVE_DATA_API_KEY','').strip();topic=os.environ.get('NTFY_TOPIC','').strip()
    if not key:raise SystemExit('GitHub Secret TWELVE_DATA_API_KEY fehlt')
    now=datetime.datetime.now(datetime.timezone.utc).isoformat()
    results={}; errors={}; sent=dict(old.get('last_alerts',{}))
    for symbol in SYMBOLS:
        try:
            rows=fetch(symbol,key);closes=[float(v['close']) for v in rows]
            e9=ema(closes,9);e21=ema(closes,21);r=rsi(closes)
            up=e9[-2]<=e21[-2] and e9[-1]>e21[-1]
            down=e9[-2]>=e21[-2] and e9[-1]<e21[-1]
            # Conservative RSI confirmation: buy 50-70; sell 30-50.
            signal='KAUF' if up and 50<=r<=70 else 'VERKAUF' if down and 30<=r<=50 else 'BEOBACHTEN'
            stamp=rows[-1]['datetime'];identifier=f'{symbol}:{signal}:{stamp}'
            results[symbol]={'price':round(closes[-1],4),'ema9':round(e9[-1],4),'ema21':round(e21[-1],4),'rsi14':round(r,2),'signal':signal,'candle_time_ny':stamp,'currency':'USD'}
            if signal!='BEOBACHTEN' and sent.get(symbol)!=identifier and topic:
                publish(topic,symbol,signal,closes[-1],stamp)
                sent[symbol]=identifier
        except Exception as ex:
            errors[symbol]=str(ex)
            if symbol in old.get('symbols',{}):results[symbol]=old['symbols'][symbol]
    data={'updated_at_utc':now,'interval':'5min','symbols':results,'errors':errors,'last_alerts':sent,'provider':'Twelve Data','note':'Testversion. Daten können verzögert sein. Keine Anlageberatung.'}
    OUT.parent.mkdir(exist_ok=True);OUT.write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n')
    if errors:print('Fehler:',errors)
    if not results:raise SystemExit('Keine Daten vorhanden')
if __name__=='__main__':main()
