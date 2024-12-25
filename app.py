import os
from flask import Flask, render_template_string
import pandas as pd
import yfinance as yf
import datetime
from datetime import timedelta
import matplotlib.pyplot as plt
import io
import base64

app = Flask(__name__)

# GP-Optimized parametrar (buy1%, low1, sellall)
gp_optimized_params = [82.93481, 13.147202, 19.855963]

def make_levered_price_series(vix_data, leverage=1, start_price=100.0):
    """
    Skapar en syntetisk kolumn 'LevClose' i vix_data,
    som simulerar en daglig rebalanserad produkt med x-gånger hävstång
    baserat på VIX-förändringar.

    :param vix_data: DataFrame med 'VIX'-kolumn och ett datumindex.
    :param leverage: Hävstång (t.ex. 2 för 2x).
    :param start_price: Startvärde för den syntetiska produkten.
    :return: Samma DataFrame med en ny kolumn 'LevClose'.
    """
    df = vix_data.copy()
    df['LevClose'] = start_price

    for i in range(1, len(df)):
        prev_vix = df['VIX'].iloc[i - 1]
        curr_vix = df['VIX'].iloc[i]
        prev_price = df['LevClose'].iloc[i - 1]

        # Undvik division by zero om data är trasig
        if prev_vix == 0:
            df.loc[df.index[i], 'LevClose'] = prev_price
        else:
            pct_change = (curr_vix - prev_vix) / prev_vix
            df.loc[df.index[i], 'LevClose'] = prev_price * (1 + leverage * pct_change)

    return df

def simulate_gp_model(
    params,
    vix_data,
    leverage=1,
    initial_cap=100000,
    sell_fee=0.05,
    holding_fee=0.0002
):
    """
    Simulerar en strategi som:
      - Köper buy1% av kapitalet när VIX < low1 (och du inte har någon position),
      - Om (position / kapital) < 0.25 och VIX < low1 => "Köp igen",
      - Säljer allt när VIX > sellall,
      - Drar daglig holding fee från positionens värde,
      - Uppdaterar positionsvärdet genom att spåra hur många andelar (shares) man äger.

    :param params: [buy1, low1, sellall]
    :param vix_data: DataFrame med kolumnerna 'VIX' samt 'LevClose'.
    :param leverage: Hävstång för den syntetiska VIX-produkten.
    :param initial_cap: Startkapital.
    :param sell_fee: Procentuell avgift vid sälj (0.05 = 5%).
    :param holding_fee: Daglig avgift på position (0.0002 = 0.02%).
    :return: En lista av dictar med bl.a. Datum, Kapital, Positioner, Ackumulerat Värde, Aktion.
    """
    buy1, low1, sellall = params
    capital = initial_cap

    # Variabel för antal andelar i den hävstångade produkten
    shares_owned = 0.0

    # Skapa syntetisk hävstångskolumn om den inte redan finns
    df = make_levered_price_series(vix_data, leverage=leverage, start_price=100.0)

    daily_log = []
    for i in range(len(df)):
        date = df.index[i]
        vix = df['VIX'].iloc[i]
        lev_price = df['LevClose'].iloc[i]

        # Beräkna nuvarande positionsvärde
        positions_value = shares_owned * lev_price

        # Dra daglig holding fee
        if positions_value > 0:
            positions_value *= (1 - holding_fee)
            # Uppdatera antal andelar
            if lev_price != 0:
                shares_owned = positions_value / lev_price

        # --- KÖPLOGIK 1 ---
        # Om VIX < low1 och vi har 0 andelar
        if vix < low1 and capital > 0 and shares_owned == 0:
            amount_to_invest = capital * (buy1 / 100.0)
            new_shares = amount_to_invest / lev_price if lev_price > 0 else 0
            shares_owned += new_shares
            capital -= amount_to_invest

            daily_log.append({
                "Datum": date,
                "Aktion": "Köp",
                "VIX": vix,
                "Kapital": capital,
                "Positioner": shares_owned * lev_price,
            })

        # --- KÖPLOGIK 2 ---
        # Om VIX < low1 och (position / kapital) < 0.25 => "Köp igen"
        positions_value = shares_owned * lev_price
        if vix < low1 and capital > 0:
            if positions_value > 0 and capital > 0:
                ratio = positions_value / capital if capital != 0 else 0
                if ratio < 0.25:
                    amount_to_invest = capital * (buy1 / 100.0)
                    new_shares = amount_to_invest / lev_price if lev_price > 0 else 0
                    shares_owned += new_shares
                    capital -= amount_to_invest

                    daily_log.append({
                        "Datum": date,
                        "Aktion": "Köp igen",
                        "VIX": vix,
                        "Kapital": capital,
                        "Positioner": shares_owned * lev_price,
                    })

        # --- SÄLJLOGIK ---
        # Sälj allt om VIX > sellall
        positions_value = shares_owned * lev_price
        if vix > sellall and shares_owned > 0:
            sell_value = positions_value * (1 - sell_fee)
            capital += sell_value
            shares_owned = 0.0

            daily_log.append({
                "Datum": date,
                "Aktion": "Sälj Allt",
                "VIX": vix,
                "Kapital": capital,
                "Positioner": 0.0,
            })

        # Räkna ut totalvärde
        positions_value = shares_owned * lev_price
        total_value = capital + positions_value

        # Logga minst en rad varje dag
        daily_log.append({
            "Datum": date,
            "Aktion": "",  # Tom om ingen ny affär
            "VIX": vix,
            "Kapital": capital,
            "Positioner": positions_value,
            "Ackumulerat Värde": total_value
        })

    return daily_log

@app.route('/')
def home():
    """
    Startsida: visar dagens VIX-värde och enkel rekommendation.
    """
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

    html = f"""
    <h1>Välkommen till GP-Optimized Modellen</h1>
    <p>Dagens VIX-värde: <strong>{today_vix:.2f}</strong></p>
    <p>Rekommenderad åtgärd: <strong>{recommendation}</strong></p>
    <p>Använd följande endpoints:</p>
    <ul>
        <li><a href="/gp_model_last6months">/gp_model_last6months</a> – Senaste 6 månadernas simulering</li>
        <li><a href="/gp_model_alltime">/gp_model_alltime</a> – Maximal historik</li>
    </ul>
    """
    return html

@app.route('/gp_model_last6months')
def gp_model_last6months():
    """
    Visar strategins resultat för de senaste 6 månaderna.
    """
    end_date = datetime.datetime.now()
    start_date = end_date - timedelta(days=6 * 30)  # ~6 månader

    # Hämta VIX-data
    vix_data = yf.Ticker('^VIX').history(start=start_date, end=end_date)[['Close']].rename(columns={'Close': 'VIX'})
    if len(vix_data) == 0:
        return "Ingen data tillgänglig för de senaste 6 månaderna."

    # Simulera strategin
    actions = simulate_gp_model(
        params=gp_optimized_params,
        vix_data=vix_data,
        leverage=1,         # justera hävstång här
        initial_cap=100000,
        sell_fee=0.05,
        holding_fee=0.0002
    )

    df = pd.DataFrame(actions)
    df['Datum'] = pd.to_datetime(df['Datum'])

    # Skapa dag-för-dag-linje (sista rad per datum)
    daily_df = df.groupby('Datum', as_index=False).last()

    # Rita graf
    plt.figure(figsize=(10, 6))
    plt.plot(daily_df['Datum'], daily_df['Ackumulerat Värde'], label='Ackumulerat Värde', color='blue')
    plt.xlabel('Datum')
    plt.ylabel('Totalt Värde (SEK)')
    plt.title('Ackumulerat Värde (Senaste 6 månaderna)')
    plt.legend()
    plt.grid()

    # Spara grafen som base64
    img = io.BytesIO()
    plt.savefig(img, format='png', bbox_inches='tight')
    img.seek(0)
    graph_url = base64.b64encode(img.getvalue()).decode()
    plt.close()

    # Generera HTML-tabell
    table_html = df.to_html(index=False, classes='table table-striped', border=0)

    html = f"""
    <!DOCTYPE html>
    <html lang="sv">
    <head>
        <meta charset="UTF-8">
        <title>GP-Optimized - Senaste 6 månaderna</title>
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.1.3/css/bootstrap.min.css">
    </head>
    <body>
        <div class="container mt-5">
            <h1>GP-Optimized (Senaste 6 månaderna)</h1>
            <div class="mb-4">
                <img src="data:image/png;base64,{graph_url}" class="img-fluid" alt="Graf för ackumulerade värden">
            </div>
            {table_html}
        </div>
    </body>
    </html>
    """
    return render_template_string(html)

@app.route('/gp_model_alltime')
def gp_model_alltime():
    """
    Visar strategins resultat över hela historiken som finns på Yahoo Finance för ^VIX.
    """
    # Hämta maximal historik för VIX
    vix_data = yf.Ticker('^VIX').history(period='max')[['Close']].rename(columns={'Close': 'VIX'})
    if len(vix_data) == 0:
        return "Ingen historisk data tillgänglig för ^VIX."

    # Simulera strategin
    actions = simulate_gp_model(
        params=gp_optimized_params,
        vix_data=vix_data,
        leverage=1,         # justera hävstång om du vill
        initial_cap=100000,
        sell_fee=0.05,
        holding_fee=0.0002
    )

    df = pd.DataFrame(actions)
    df['Datum'] = pd.to_datetime(df['Datum'])

    # Skapa dag-för-dag-linje (ta sista rad per datum)
    daily_df = df.groupby('Datum', as_index=False).last()

    # Rita graf
    plt.figure(figsize=(10, 6))
    plt.plot(daily_df['Datum'], daily_df['Ackumulerat Värde'], label='Ackumulerat Värde', color='green')
    plt.xlabel('Datum')
    plt.ylabel('Totalt Värde (SEK)')
    plt.title('Ackumulerat Värde - Hela Historiken')
    plt.legend()
    plt.grid()

    # Spara grafen som base64
    img = io.BytesIO()
    plt.savefig(img, format='png', bbox_inches='tight')
    img.seek(0)
    graph_url = base64.b64encode(img.getvalue()).decode()
    plt.close()

    # Generera HTML-tabell
    table_html = df.to_html(index=False, classes='table table-striped', border=0)

    html = f"""
    <!DOCTYPE html>
    <html lang="sv">
    <head>
        <meta charset="UTF-8">
        <title>GP-Optimized - Maximal Historik</title>
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.1.3/css/bootstrap.min.css">
    </head>
    <body>
        <div class="container mt-5">
            <h1>GP-Optimized (Hela Historiken)</h1>
            <div class="mb-4">
                <img src="data:image/png;base64,{graph_url}" class="img-fluid" alt="Graf för ackumulerade värden">
            </div>
            {table_html}
        </div>
    </body>
    </html>
    """
    return render_template_string(html)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    # Kör appen lokalt eller på en server
    app.run(host="0.0.0.0", port=port)
