import os
from flask import Flask, render_template_string, request, jsonify
import pandas as pd
import yfinance as yf
import datetime
from datetime import timedelta
import matplotlib.pyplot as plt
import io
import base64

# --- För web push ---
import json
from pywebpush import webpush, WebPushException

app = Flask(__name__)

# === VAPID-nycklar (EXEMPEL!) ===
# Generera dina egna via "pywebpush --gen-vapid-key"
VAPID_PUBLIC_KEY = "BMy_FAKED_PUBLIC_KEY_HERE____"
VAPID_PRIVATE_KEY = "FAKED_PRIVATE_KEY_HERE"
VAPID_CLAIMS = {
    "sub": "mailto:din_email@example.com"
}

# I en riktig applikation sparar du dessa i en databas
# men här har vi en enkel lista i RAM.
subscribers = [bohjorte@gmail.com]

# GP-Optimized parametrar (buy1%, low1, sellall)
gp_optimized_params = [82.93481, 13.147202, 19.855963]

def make_levered_price_series(vix_data, leverage=1, start_price=100.0):
    df = vix_data.copy()
    df['LevClose'] = start_price
    for i in range(1, len(df)):
        prev_vix = df['VIX'].iloc[i - 1]
        curr_vix = df['VIX'].iloc[i]
        prev_price = df['LevClose'].iloc[i - 1]
        if prev_vix == 0:
            df.loc[df.index[i], 'LevClose'] = prev_price
        else:
            pct_change = (curr_vix - prev_vix) / prev_vix
            df.loc[df.index[i], 'LevClose'] = prev_price * (1 + leverage * pct_change)
    return df

def send_push_notification(title, body):
    """
    Skickar push-notis till alla prenumeranter i 'subscribers'.
    title, body: strängar för notisens innehåll.
    """
    for subscription in subscribers:
        try:
            webpush(
                subscription_info=subscription,
                data=json.dumps({"title": title, "body": body}),
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims=VAPID_CLAIMS
            )
        except WebPushException as ex:
            print("Web push misslyckades:", ex)
            # Om en prenumeration är ogiltig/expired kan du ta bort den här

def simulate_gp_model(params, vix_data, leverage=1, initial_cap=100000,
                      sell_fee=0.05, holding_fee=0.0002):
    buy1, low1, sellall = params
    capital = initial_cap
    shares_owned = 0.0
    df = make_levered_price_series(vix_data, leverage=leverage, start_price=100.0)

    daily_log = []
    for i in range(len(df)):
        date = df.index[i]
        vix = df['VIX'].iloc[i]
        lev_price = df['LevClose'].iloc[i]

        # positions_value innan nya eventuella transaktioner
        positions_value = shares_owned * lev_price
        # dra daglig holding fee
        if positions_value > 0:
            positions_value *= (1 - holding_fee)
            if lev_price != 0:
                shares_owned = positions_value / lev_price

        # KÖP - logik 1
        if vix < low1 and capital > 0 and shares_owned == 0:
            amount_to_invest = capital * (buy1 / 100.0)
            new_shares = amount_to_invest / lev_price if lev_price > 0 else 0
            shares_owned += new_shares
            capital -= amount_to_invest

            # Skicka push-notis
            send_push_notification(
                title="Köp-trigger!",
                body=f"VIX={vix:.2f}, Köp {buy1:.2f}% av kapitalet."
            )

            daily_log.append({
                "Datum": date,
                "Aktion": "Köp",
                "VIX": vix,
                "Kapital": capital,
                "Positioner": shares_owned * lev_price,
            })

        # KÖP - logik 2
        positions_value = shares_owned * lev_price
        if vix < low1 and capital > 0:
            if positions_value > 0 and capital > 0:
                ratio = positions_value / capital if capital != 0 else 0
                if ratio < 0.25:
                    amount_to_invest = capital * (buy1 / 100.0)
                    new_shares = amount_to_invest / lev_price if lev_price > 0 else 0
                    shares_owned += new_shares
                    capital -= amount_to_invest

                    send_push_notification(
                        title="Köp igen!",
                        body=f"VIX={vix:.2f}, position/kapital < 0.25 -> Köp mer."
                    )

                    daily_log.append({
                        "Datum": date,
                        "Aktion": "Köp igen",
                        "VIX": vix,
                        "Kapital": capital,
                        "Positioner": shares_owned * lev_price,
                    })

        # SÄLJ
        positions_value = shares_owned * lev_price
        if vix > sellall and shares_owned > 0:
            sell_value = positions_value * (1 - sell_fee)
            capital += sell_value
            shares_owned = 0.0

            send_push_notification(
                title="Sälj-trigger!",
                body=f"VIX={vix:.2f} > {sellall:.2f} -> Sälj allt."
            )

            daily_log.append({
                "Datum": date,
                "Aktion": "Sälj Allt",
                "VIX": vix,
                "Kapital": capital,
                "Positioner": 0.0,
            })

        positions_value = shares_owned * lev_price
        total_value = capital + positions_value

        daily_log.append({
            "Datum": date,
            "Aktion": "",
            "VIX": vix,
            "Kapital": capital,
            "Positioner": positions_value,
            "Ackumulerat Värde": total_value
        })

    return daily_log

# ============== ROUTES ==============

@app.route('/')
def home():
    vix_data = yf.Ticker('^VIX').history(period='1d')[['Close']].rename(columns={'Close': 'VIX'})
    if len(vix_data) == 0:
        today_vix = float('nan')
    else:
        today_vix = vix_data['VIX'].iloc[-1]

    buy1, low1, sellall = gp_optimized_params

    if today_vix < low1:
        recommendation = f"Köp {buy1:.2f}% av kapitalet - VIX är under {low1:.2f}."
    elif today_vix > sellall:
        recommendation = f"Sälj alla positioner - VIX är över {sellall:.2f}."
    else:
        recommendation = "Inga åtgärder rekommenderas idag."

    # En enkel startsida som laddar in manifest & service worker (för PWA)
    # Och inkluderar lite JS för att prenumerera på push
    # Notera att vi även refererar till "subscribe" endpointen.
    html = f"""
    <!DOCTYPE html>
    <html lang="sv">
    <head>
        <meta charset="UTF-8">
        <title>GP-Optimized PWA</title>
        <link rel="manifest" href="/manifest.json">
        <meta name="theme-color" content="#ffffff">
    </head>
    <body>
      <h1>Välkommen till GP-Optimized Modellen (PWA)</h1>
      <p>Dagens VIX-värde: <strong>{today_vix:.2f}</strong></p>
      <p>Rekommenderad åtgärd: <strong>{recommendation}</strong></p>
      <p>Använd följande endpoints:</p>
      <ul>
        <li><a href="/gp_model_last6months">/gp_model_last6months</a> – Senaste 6 månadernas simulering</li>
        <li><a href="/gp_model_alltime">/gp_model_alltime</a> – Maximal historik</li>
      </ul>

      <button onclick="subscribeToPush()">Aktivera notiser</button>

      <script>
      // Registrera service worker + be om lov att visa notiser + prenumerera
      if ('serviceWorker' in navigator) {{
          navigator.serviceWorker.register('/service-worker.js').then(reg => {{
              console.log('Service Worker är registrerad!', reg);
          }});
      }}

      async function subscribeToPush() {{
          if (!('serviceWorker' in navigator)) {{
              alert("Din webbläsare stöder ej service workers.");
              return;
          }}
          const reg = await navigator.serviceWorker.ready;
          const permission = await Notification.requestPermission();
          if (permission !== 'granted') {{
              alert("Push-notiser nekades :(");
              return;
          }}
          // Prenumerera på push
          const vapidPublicKey = "{VAPID_PUBLIC_KEY}";
          const convertedKey = urlBase64ToUint8Array(vapidPublicKey);

          try {{
              const subscription = await reg.pushManager.subscribe({{
                userVisibleOnly: true,
                applicationServerKey: convertedKey
              }});
              console.log("Subscription:", subscription.toJSON());
              // Skicka till server
              const res = await fetch('/subscribe', {{
                method: 'POST',
                headers: {{
                  'Content-Type': 'application/json'
                }},
                body: JSON.stringify(subscription.toJSON())
              }});
              const data = await res.json();
              console.log("Server svar:", data);
              alert("Du prenumererar nu på push-notiser!");
          }} catch (err) {{
              console.error("Fel vid subscription:", err);
          }}
      }}

      function urlBase64ToUint8Array(base64String) {{
          const padding = '='.repeat((4 - base64String.length % 4) % 4);
          const base64 = (base64String + padding)
            .replace(/\\-/g, '+')
            .replace(/_/g, '/');
          const rawData = window.atob(base64);
          const outputArray = new Uint8Array(rawData.length);
          for (let i = 0; i < rawData.length; ++i) {{
            outputArray[i] = rawData.charCodeAt(i);
          }}
          return outputArray;
      }}
      </script>
    </body>
    </html>
    """
    return html

@app.route('/subscribe', methods=['POST'])
def subscribe():
    """
    Tar emot subscription-objekt (JSON) från klienten och lagrar i subscribers-listan.
    """
    subscription_json = request.get_json()
    # Lägg till i subscribers (om den inte redan finns)
    if subscription_json not in subscribers:
        subscribers.append(subscription_json)
    return jsonify({"status": "ok", "message": "Subscription mottagen."})

@app.route('/manifest.json')
def manifest():
    # Exempel på ett enkelt manifest.json
    # Tips: lägg i en riktig /static/manifest.json i produktion
    manifest_data = {
        "name": "GP-Optimized PWA",
        "short_name": "GP-Opt",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#ffffff",
        "theme_color": "#ffffff",
        "icons": [
            {
                "src": "https://upload.wikimedia.org/wikipedia/commons/3/33/White_square_meterial_design.svg",
                "sizes": "192x192",
                "type": "image/svg"
            }
        ]
    }
    return jsonify(manifest_data)

@app.route('/service-worker.js')
def service_worker():
    """
    Enkel service worker som lyssnar på 'push'-event och visar notis.
    Returneras med mimetype=application/javascript
    """
    sw_code = f"""
self.addEventListener('install', event => {{
  console.log('Service Worker installing.');
  self.skipWaiting();
}});

self.addEventListener('activate', event => {{
  console.log('Service Worker activating.');
}});

self.addEventListener('push', event => {{
  if (event.data) {{
    let payload = event.data.json();
    console.log("Push received:", payload);
    const title = payload.title || "GP-Optimized Notis";
    const body = payload.body || "";
    event.waitUntil(
      self.registration.showNotification(title, {{
        body: body,
        icon: 'https://upload.wikimedia.org/wikipedia/commons/3/33/White_square_meterial_design.svg'
      }})
    );
  }}
}});
"""
    return app.response_class(sw_code, mimetype='application/javascript')

@app.route('/gp_model_last6months')
def gp_model_last6months():
    end_date = datetime.datetime.now()
    start_date = end_date - timedelta(days=6 * 30)
    vix_data = yf.Ticker('^VIX').history(start=start_date, end=end_date)[['Close']].rename(columns={'Close': 'VIX'})
    if len(vix_data) == 0:
        return "Ingen data tillgänglig för de senaste 6 månaderna."

    actions = simulate_gp_model(
        params=gp_optimized_params,
        vix_data=vix_data,
        leverage=1,
        initial_cap=100000,
        sell_fee=0.05,
        holding_fee=0.0002
    )
    df = pd.DataFrame(actions)
    df['Datum'] = pd.to_datetime(df['Datum'])
    daily_df = df.groupby('Datum', as_index=False).last()

    plt.figure(figsize=(10, 6))
    plt.plot(daily_df['Datum'], daily_df['Ackumulerat Värde'], label='Ackumulerat Värde', color='blue')
    plt.xlabel('Datum')
    plt.ylabel('Totalt Värde (SEK)')
    plt.title('Ackumulerat Värde (Senaste 6 månaderna)')
    plt.legend()
    plt.grid()

    img = io.BytesIO()
    plt.savefig(img, format='png', bbox_inches='tight')
    img.seek(0)
    graph_url = base64.b64encode(img.getvalue()).decode()
    plt.close()

    table_html = df.to_html(index=False, classes='table table-striped', border=0)
    html = f"""
    <!DOCTYPE html>
    <html lang="sv">
    <head>
        <meta charset="UTF-8">
        <title>GP-Optimized - Senaste 6 månaderna</title>
        <link rel="manifest" href="/manifest.json">
        <meta name="theme-color" content="#ffffff">
    </head>
    <body>
        <div class="container" style="max-width: 900px; margin: auto;">
            <h1>GP-Optimized (Senaste 6 månaderna)</h1>
            <div>
                <img src="data:image/png;base64,{graph_url}" style="max-width:100%;" alt="Graf för ackumulerade värden">
            </div>
            {table_html}
        </div>
    </body>
    </html>
    """
    return render_template_string(html)

@app.route('/gp_model_alltime')
def gp_model_alltime():
    vix_data = yf.Ticker('^VIX').history(period='max')[['Close']].rename(columns={'Close': 'VIX'})
    if len(vix_data) == 0:
        return "Ingen historisk data tillgänglig för ^VIX."

    actions = simulate_gp_model(
        params=gp_optimized_params,
        vix_data=vix_data,
        leverage=1,
        initial_cap=100000,
        sell_fee=0.05,
        holding_fee=0.0002
    )
    df = pd
