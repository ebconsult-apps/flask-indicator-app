from flask import Flask, jsonify
import pandas as pd
import yfinance as yf
import datetime
from datetime import timedelta

app = Flask(__name__)

# GP-Optimized Parametrar
gp_optimized_params = [82.93481, 19.436844, 13.147202, 10.477527, 44.975998, 21.186408, 22.163723, 23.466296, 19.855963]

# Funktion för simulering med GP-Optimized modellen
def simulate_gp_model(params, vix_data, leverage=1, initial_cap=100000, sell_fee=0.05, holding_fee=0.0002):
    buy1, buy2, low1, low2, sell1, sell2, high1, high2, sellall = params
    capital = initial_cap
    positions = 0
    actions = []

    for i in range(len(vix_data)):
        date = vix_data.index[i]
        vix = vix_data['VIX'].iloc[i]

        # Lägg till holdingkostnad
        if positions > 0:
            holding_cost = positions * vix * holding_fee
            capital -= holding_cost

        # Köp 1 om VIX < low1
        if vix < low1 and capital > 0:
            amount_to_invest = capital * (buy1 / 100) * leverage
            positions += amount_to_invest / vix
            capital -= amount_to_invest
            actions.append({"Datum": date, "Aktion": "Köp", "VIX": vix, "Kapital": capital, "Positioner": positions})

        # Köp 2 om VIX < low2
        if vix < low2 and capital > 0:
            amount_to_invest = capital * (buy2 / 100) * leverage
            positions += amount_to_invest / vix
            capital -= amount_to_invest
            actions.append({"Datum": date, "Aktion": "Köp", "VIX": vix, "Kapital": capital, "Positioner": positions})

        # Sälj 1 om VIX > high1
        if vix > high1 and positions > 0:
            amount_to_sell = positions * (sell1 / 100)
            sell_value = amount_to_sell * vix * (1 - sell_fee)
            capital += sell_value
            positions -= amount_to_sell
            actions.append({"Datum": date, "Aktion": "Sälj", "VIX": vix, "Kapital": capital, "Positioner": positions})

        # Sälj 2 om VIX > high2
        if vix > high2 and positions > 0:
            amount_to_sell = positions * (sell2 / 100)
            sell_value = amount_to_sell * vix * (1 - sell_fee)
            capital += sell_value
            positions -= amount_to_sell
            actions.append({"Datum": date, "Aktion": "Sälj", "VIX": vix, "Kapital": capital, "Positioner": positions})

        # Sälj allt om VIX > sellall
        if vix > sellall and positions > 0:
            sell_value = positions * vix * (1 - sell_fee)
            capital += sell_value
            positions = 0
            actions.append({"Datum": date, "Aktion": "Sälj Allt", "VIX": vix, "Kapital": capital, "Positioner": positions})

    return actions

# Endpoint för full historik (maximalt data)
@app.route('/gp_model')
def gp_model():
    vix_data = yf.Ticker('^VIX').history(period="max")[['Close']].rename(columns={'Close': 'VIX'})
    vix_data_2012 = vix_data[vix_data.index >= "2012-01-01"]
    actions = simulate_gp_model(gp_optimized_params, vix_data_2012)
    return jsonify(actions)

# Endpoint för de senaste 6 månaderna
@app.route('/gp_model_last6months')
def gp_model_last6months():
    end_date = datetime.datetime.now()
    start_date = end_date - timedelta(days=6 * 30)  # Ungefär 6 månader
    vix_data = yf.Ticker('^VIX').history(start=start_date, end=end_date)[['Close']].rename(columns={'Close': 'VIX'})
    actions = simulate_gp_model(gp_optimized_params, vix_data)
    return jsonify(actions)

# Root-route
@app.route('/')
def home():
    return """
    <h1>Välkommen till GP-Optimized Modellen</h1>
    <p>Använd följande endpoints:</p>
    <ul>
        <li><a href="/gp_model">/gp_model</a>: Visa full historik (2012-2024)</li>
        <li><a href="/gp_model_last6months">/gp_model_last6months</a>: Visa de senaste 6 månaderna</li>
    </ul>
    """

if __name__ == "__main__":
    app.run(debug=True)
