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

# === 2% årlig holding cost ===
# Anta daglig = 0.02 / 365
ANNUAL_FEE = 0.02
daily_holding_fee = ANNUAL_FEE / 365  # ~0.00005479

def make_levered_price_series(vix_data, leverage=1, start_price=100.0):
    """
    Skapar en syntetisk kolumn 'LevClose' i vix_data,
    som simulerar en daglig rebalanserad produkt med x-gånger hävstång
    baserat på VIX-förändringar.
    """
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
      - Spårar positionsvärdet via antal andelar (shares).
    """
    buy1, low1, sellall = params
    capital = initial_cap
    shares_owned = 0.0

    df = make_levered_price_series(vix_data, leverage=leverage, start_price=100.0)
    daily_log = []
    for i in range(len(df)):
        date = df.index[i]
        vix = df['VIX'].iloc[i]
        lev_price = df['LevClose'].iloc[i]

        positions_value = shares_owned * lev_price

        # Dra daglig holding fee
        if positions_value > 0:
            positions_value *= (1 - holding_fee)
            # Uppdatera shares
            if lev_price != 0:
                shares_owned = positions_value / lev_price

        # --- KÖPLOGIK 1 ---
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

        positions_value = shares_owned * lev_price
        total_value = capital + positions_value

        # Logga minst en rad per dag
        daily_log.append({
            "Datum": date,
            "Aktion": "",
            "VIX": vix,
            "Kapital": capital,
            "Positioner": positions_value,
            "Ackumulerat Värde": total_value
        })

    return daily_log

def compute_max_drawdown(equity_series):
    """
    Beräknar maximal drawdown av en tidsserie av totala värden.
    MDD = max( (peak - trough) / peak ).
    Returnerar ett positivt tal, t.ex. 0.5 = 50%.
    """
    # equity_series är en pandas Series med t.ex. daily ack värden
    roll_max = equity_series.cummax()
    drawdown = (equity_series - roll_max) / roll_max
    max_drawdown = drawdown.min()  # blir ett negativt tal
    return abs(max_drawdown)  # gör det positivt

def probability_of_ruin(equity_series):
    """
    Enkel "sannolikhet" att nå 0:
    Om equity någon gång <= 0 => 1 (100%), annars 0 (0%).
    """
    if (equity_series <= 0).any():
        return 1.0
    return 0.0

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
        <li><a href="/compare_leverage">/compare_leverage</a> – Jämför 2x och 4x hävstång</li>
    </ul>
    """
    return html

@app.route('/gp_model_last6months')
def gp_model_last6months():
    end_date = datetime.datetime.now()
    start_date = end_date - timedelta(days=6 * 30)  # ~6 månader

    vix_data = yf.Ticker('^VIX').history(start=start_date, end=end_date)[['Close']].rename(columns={'Close': 'VIX'})
    if len(vix_data) == 0:
        return "Ingen data tillgänglig för de senaste 6 månaderna."

    # Använd 1x som tidigare
    actions = simulate_gp_model(
        params=gp_optimized_params,
        vix_data=vix_data,
        leverage=1,
        initial_cap=100000,
        sell_fee=0.05,
        holding_fee=daily_holding_fee  # 2% per år
    )

    df = pd.DataFrame(actions)
    df['Datum'] = pd.to_datetime(df['Datum'])

    # Sista rad per datum
    daily_df = df.groupby('Datum', as_index=False).last()

    # Rita graf
    plt.figure(figsize=(10, 6))
    plt.plot(daily_df['Datum'], daily_df['Ackumulerat Värde'], label='Ack Värde (1x)', color='blue')
    plt.xlabel('Datum')
    plt.ylabel('Totalt Värde (SEK)')
    plt.title('Ackumulerat Värde (Senaste 6 månaderna, 1x)')
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
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.1.3/css/bootstrap.min.css">
    </head>
    <body>
        <div class="container mt-5">
            <h1>GP-Optimized (Senaste 6 månaderna, 1x)</h1>
            <p>Holding Fee: 2% per år (~{daily_holding_fee:.6f} per dag)</p>
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
    vix_data = yf.Ticker('^VIX').history(period='max')[['Close']].rename(columns={'Close': 'VIX'})
    if len(vix_data) == 0:
        return "Ingen historisk data tillgänglig för ^VIX."

    actions = simulate_gp_model(
        params=gp_optimized_params,
        vix_data=vix_data,
        leverage=1,
        initial_cap=100000,
        sell_fee=0.05,
        holding_fee=daily_holding_fee
    )

    df = pd.DataFrame(actions)
    df['Datum'] = pd.to_datetime(df['Datum'])
    daily_df = df.groupby('Datum', as_index=False).last()

    # Rita graf
    plt.figure(figsize=(10, 6))
    plt.plot(daily_df['Datum'], daily_df['Ackumulerat Värde'], label='Ack Värde (1x)', color='green')
    plt.xlabel('Datum')
    plt.ylabel('Totalt Värde (SEK)')
    plt.title('Ackumulerat Värde - Hela Historiken (1x)')
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
        <title>GP-Optimized - Maximal Historik</title>
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.1.3/css/bootstrap.min.css">
    </head>
    <body>
        <div class="container mt-5">
            <h1>GP-Optimized (Hela Historiken, 1x)</h1>
            <p>Holding Fee: 2% per år (~{daily_holding_fee:.6f} per dag)</p>
            <div class="mb-4">
                <img src="data:image/png;base64,{graph_url}" class="img-fluid" alt="Graf för ackumulerade värden">
            </div>
            {table_html}
        </div>
    </body>
    </html>
    """
    return render_template_string(html)

@app.route('/compare_leverage')
def compare_leverage():
    """
    Jämför 2x och 4x hävstång. Plotta båda i samma graf.
    Beräkna max drawdown, sannolikhet att nå 0.
    """
    # Hämta maximal data
    vix_data = yf.Ticker('^VIX').history(period='max')[['Close']].rename(columns={'Close': 'VIX'})
    if len(vix_data) == 0:
        return "Ingen historisk data tillgänglig för ^VIX."

    # Simulera 2x
    actions_2x = simulate_gp_model(
        params=gp_optimized_params,
        vix_data=vix_data,
        leverage=2,
        initial_cap=100000,
        sell_fee=0.05,
        holding_fee=daily_holding_fee
    )
    df2 = pd.DataFrame(actions_2x)
    df2['Datum'] = pd.to_datetime(df2['Datum'])
    daily_df2 = df2.groupby('Datum', as_index=False).last()

    # Simulera 4x
    actions_4x = simulate_gp_model(
        params=gp_optimized_params,
        vix_data=vix_data,
        leverage=4,
        initial_cap=100000,
        sell_fee=0.05,
        holding_fee=daily_holding_fee
    )
    df4 = pd.DataFrame(actions_4x)
    df4['Datum'] = pd.to_datetime(df4['Datum'])
    daily_df4 = df4.groupby('Datum', as_index=False).last()

    # Beräkna max drawdown och "prob of ruin"
    mdd_2x = compute_max_drawdown(daily_df2['Ackumulerat Värde'])
    ruin_2x = probability_of_ruin(daily_df2['Ackumulerat Värde'])

    mdd_4x = compute_max_drawdown(daily_df4['Ackumulerat Värde'])
    ruin_4x = probability_of_ruin(daily_df4['Ackumulerat Värde'])

    # Rita i samma figur
    plt.figure(figsize=(10, 6))
    plt.plot(daily_df2['Datum'], daily_df2['Ackumulerat Värde'], label=f'2x Lev', color='blue')
    plt.plot(daily_df4['Datum'], daily_df4['Ackumulerat Värde'], label=f'4x Lev', color='red')
    plt.xlabel('Datum')
    plt.ylabel('Totalt Värde (SEK)')
    plt.title('Jämförelse av 2x vs 4x (2% årlig holding fee)')
    plt.legend()
    plt.grid()

    img = io.BytesIO()
    plt.savefig(img, format='png', bbox_inches='tight')
    img.seek(0)
    graph_url = base64.b64encode(img.getvalue()).decode()
    plt.close()

    # Skapa en liten rapport-ruta
    # MDD (i procent) & ruin (i procent)
    mdd_2x_pct = mdd_2x * 100.0
    ruin_2x_pct = ruin_2x * 100.0

    mdd_4x_pct = mdd_4x * 100.0
    ruin_4x_pct = ruin_4x * 100.0

    stats_html = f"""
    <h2>2x Hävstång</h2>
    <p>Max Drawdown: {mdd_2x_pct:.2f}%<br>
       Probability of 0: {ruin_2x_pct:.2f}%</p>
    <h2>4x Hävstång</h2>
    <p>Max Drawdown: {mdd_4x_pct:.2f}%<br>
       Probability of 0: {ruin_4x_pct:.2f}%</p>
    """

    html = f"""
    <!DOCTYPE html>
    <html lang="sv">
    <head>
        <meta charset="UTF-8">
        <title>Jämförelse 2x och 4x</title>
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.1.3/css/bootstrap.min.css">
    </head>
    <body>
        <div class="container" style="max-width: 960px; margin: auto;">
            <h1>Jämförelse: 2x vs 4x Hävstång</h1>
            <p>Holding Fee: 2% per år (~{daily_holding_fee:.6f} per dag)</p>
            <div style="margin-bottom: 20px;">
                <img src="data:image/png;base64,{graph_url}" alt="Leverage Comparison" style="max-width:100%;">
            </div>
            {stats_html}
            <h3>Data 2x (senaste rad per dag)</h3>
            {daily_df2.to_html(index=False, classes='table table-striped', border=0)}
            <br>
            <h3>Data 4x (senaste rad per dag)</h3>
            {daily_df4.to_html(index=False, classes='table table-striped', border=0)}
        </div>
    </body>
    </html>
    """
    return html


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
