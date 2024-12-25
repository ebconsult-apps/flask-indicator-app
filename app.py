from flask import Flask, render_template, jsonify
import pandas as pd
import yfinance as yf

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
            actions.append([date, "Köp", vix, capital, positions])

        # Köp 2 om VIX < low2
        if vix < low2 and capital > 0:
            amount_to_invest = capital * (buy2 / 100) * leverage
            positions += amount_to_invest / vix
            capital -= amount_to_invest
            actions.append([date, "Köp", vix, capital, positions])

        # Sälj 1 om VIX > high1
        if vix > high1 and positions > 0:
            amount_to_sell = positions * (sell1 / 100)
            sell_value = amount_to_sell * vix * (1 - sell_fee)
            capital += sell_value
            positions -= amount_to_sell
            actions.append([date, "Sälj", vix, capital, positions])

        # Sälj 2 om VIX > high2
        if vix > high2 and positions > 0:
            amount_to_sell = positions * (sell2 / 100)
            sell_value = amount_to_sell * vix * (1 - sell_fee)
            capital += sell_value
            positions -= amount_to_sell
            actions.append([date, "Sälj", vix, capital, positions])

        # Sälj allt om VIX > sellall
        if vix > sellall and positions > 0:
            sell_value = positions * vix * (1 - sell_fee)
            capital += sell_value
            positions = 0
            actions.append([date, "Sälj Allt", vix, capital, positions])

    return pd.DataFrame(actions, columns=["Datum", "Aktion", "VIX", "Kapital", "Positioner"])

# Endpoint för att visa GP-modellen
@app.route('/gp_model')
def gp_model():
    vix_data = yf.Ticker('^VIX').history(period="max")[['Close']].rename(columns={'Close': 'VIX'})
    vix_data_2012 = vix_data[vix_data.index >= "2012-01-01"]
    actions_df = simulate_gp_model(gp_optimized_params, vix_data_2012)
    return actions_df.to_json(orient="records")

# Huvudsida
@app.route('/')
def home():
    return render_template("index.html")

if __name__ == "__main__":
    app.run(debug=True)
