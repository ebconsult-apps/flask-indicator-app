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

# GP-Optimized Parametrar
gp_optimized_params = [82.93481, 13.147202, 19.855963]  # [buy1, low1, sellall]

def make_levered_price_series(vix_data, leverage=1, start_price=100.0):
    """
    Skapar en syntetisk kolumn 'LevClose' i vix_data,
    som simulerar en daglig rebalanserad produkt med x-gånger hävstång
    baserat på VIX-förändringar.

    :param vix_data: DataFrame med en kolumn 'VIX' och ett datumindex.
    :param leverage: Hävstång (t.ex. 1 för 1x, 2 för 2x).
    :param start_price: Startvärde för den syntetiska produkten.
    :return: Samma DataFrame med en ny kolumn 'LevClose'.
    """
    df = vix_data.copy()
    df['LevClose'] = start_price
    for i in range(1, len(df)):
        prev_vix = df['VIX'].iloc[i - 1]
        curr_vix = df['VIX'].iloc[i]
        prev_price = df['LevClose'].iloc[i - 1]
        if prev_vix == 0:
            # Undvik division-by-zero om data är trasig
            df.loc[df.index[i], 'LevClose'] = prev_price
        else:
            pct_change = (curr_vix - prev_vix) / prev_vix
            df.loc[df.index[i], 'LevClose'] = prev_price * (1 + leverage * pct_change)
    return df

def simulate_gp_model(params, vix_data, leverage=1, initial_cap=100000, sell_fee=0.05, holding_fee=0.0002):
    """
    Simulerar en strategi som:
      - Köper buy1% av kapitalet när VIX < low1.
      - Säljer allt när VIX > sellall.
      - Drar daglig holding fee från positionens värde.
      - Uppdaterar positionsvärde genom att hålla koll på antal andelar.

    :param params: [buy1, low1, sellall]
    :param vix_data: DataFrame med kolumnen 'VIX' (och senare 'LevClose').
    :param leverage: Hävstång för syntetisk produkt.
    :param initial_cap: Startkapital (SEK).
    :param sell_fee: Procentuell avgift vid sälj (ex 0.05 = 5%).
    :param holding_fee: Daglig avgift på position (ex 0.0002 = 0.02%).
    :return: En lista av dicts med (Datum, Kapital, Positioner, Ackumulerat Värde, Aktion, etc.).
    """
    buy1, low1, sellall = params
    capital = initial_cap
    
    # Andelar i den hävstångade produkten
    shares_owned = 0.0

    # Skapa syntetisk hävstångskolumn
    df = make_levered_price_series(vix_data, leverage=leverage, start_price=100.0)

    # Här kommer vi lagra en rad för varje dag i simuleringen
    daily_log = []

    for i in range(len(df)):
        date = df.index[i]
        vix = df['VIX'].iloc[i]
        lev_price = df['LevClose'].iloc[i]

        # Beräkna värdet på vår position
        positions_value = shares_owned * lev_price

        # Dra daglig holding fee från positionen (om > 0)
        if positions_value > 0:
            positions_value *= (1 - holding_fee)
            # Uppdatera antal andelar så att shares_owned * lev_price = positions_value
            # men undvik division med noll om lev_price av någon anledning är noll
            if lev_price != 0:
                shares_owned = positions_value / lev_price

        # --- KÖPLOGIK 1 ---
        # Om VIX < low1 och vi har 0 andelar (dvs ingen befintlig position)
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
        if vix < low1 and capital > 0 and capital > 0:
            if positions_value > 0 and (positions_value / capital) < 0.25:
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

        # Efter ev. köp/sälj - räkna totalvärde
        positions_value = shares_owned * lev_price
        total_value = capital + positions_value

        # Lägg in en "daglig" rad i loggen, oavsett om affär skedde eller ej
        daily_log.append({
            "Datum": date,
            "Aktion": "",  # Tom om ingen affär
            "VIX": vix,
            "Kapital": capital,
            "Positioner": positions_value,
            "Ackumulerat Värde": total_value
        })

    return daily_log

@app.route('/gp_model_last6months')
def gp_model_last6months():
    end_date = datetime.datetime.now()
    start_date = end_date - timedelta(days=6 * 30)  # ~6 månader

    # Hämta VIX-data
    vix_data = yf.Ticker('^VIX').history(start=start_date, end=end_date)[['Close']]
    vix_data = vix_data.rename(columns={'Close': 'VIX'})

    # Simulera
    actions = simulate_gp_model(
        params=gp_optimized_params,
        vix_data=vix_data,
        leverage=1,            # justera hävstång här om du vill
        initial_cap=100000,
        sell_fee=0.05,
        holding_fee=0.0002
    )

    # Konvertera till DataFrame
    df = pd.DataFrame(actions)
    df['Datum'] = pd.to_datetime(df['Datum'])

    # Se till att inte dubbellagra rad: notera att funktionen lägger in
    # minst en rad per dag, ibland två (om en affär sker).
    # Vi kan t.ex. gruppera per dag och ta sista. Men vi behåller här all data.

    # Skapa en dag-för-dag-linje för ackumulerat värde
    # (Kan finnas dubbletter samma datum, vi tar sista per datum)
    daily_df = df.groupby('Datum', as_index=False).last()

    # Rita grafen
    plt.figure(figsize=(10, 6))
    plt.plot(daily_df['Datum'], daily_df['Ackumulerat Värde'], label='Ackumulerat Värde', color='blue')
    plt.xlabel('Datum')
    plt.ylabel('Totalt Värde (SEK)')
    plt.title('Ackumulerat Värde (Senaste 6 månaderna)')
    plt.legend()
    plt.grid()

    # Spara grafen som en base64-sträng
    img = io.BytesIO()
    plt.savefig(img, format='png', bbox_inches='tight')
    img.seek(0)
    graph_url = base64.b64encode(img.getvalue()).decode()
    plt.close()

    # Generera HTML-tabell (alla rader i df)
    table_html = df.to_html(index=False, classes='table table-striped', border=0)

    # Enkel HTML-sida med graf och tabell
    html = f"""
    <!DOCTYPE html>
    <html lang="sv">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>GP-Optimized Actions with Shares-based Logic</title>
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

@app.route('/')
def home():
    """
    Hämta dagens VIX-värde och ge en enkel rekommendation baserat på gp_optimized_params:
      - Köp x% av kapitalet om VIX < low1
      - Sälj allt om VIX > sellall
      - Annars ingen åtgärd
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
        <li><a href="/gp_model_last6months">/gp_model_last6months</a>: Visa simulering och graf för de senaste 6 månaderna</li>
    </ul>
    """
    return html

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
