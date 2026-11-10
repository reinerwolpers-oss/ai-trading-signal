import json, os, urllib.request, urllib.parse, datetime, pathlib
ROOT=pathlib.Path(__file__).parent
OUT=ROOT/'data'/'signals.json'
SYMBOLS=['TSLA','NVDA','AMD']

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
