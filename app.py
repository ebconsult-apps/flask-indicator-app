from flask import Flask, jsonify, render_template_string
import yfinance as yf

app = Flask(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Handelsindikatorer</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0-alpha3/dist/css/bootstrap.min.css" rel="stylesheet">
  <script src="https://cdn.jsdelivr.net/npm/axios/dist/axios.min.js"></script>
</head>
<body class="bg-light">
  <div class="container py-4">
    <h1 class="text-center">Handelsindikatorer</h1>
    <div id="indicator" class="alert alert-primary text-center my-3">Laddar data...</div>
    <div id="advice" class="text-center my-3"></div>
  </div>
  <script>
    async function fetchIndicators() {
      try {
        const response = await axios.get('/api/indicators');
        const { vix, vvix, derivataVvix, advice } = response.data;

        document.getElementById('indicator').innerHTML = `
          <p>VIX: ${vix}</p>
          <p>VVIX: ${vvix}</p>
          <p>Derivata VVIX: ${derivataVvix}</p>
        `;
        document.getElementById('advice').innerHTML = `<div class="alert alert-info">${advice}</div>`;
      } catch (error) {
        document.getElementById('indicator').innerHTML = `<div class="alert alert-danger">Kunde inte hämta data.</div>`;
      }
    }

    fetchIndicators();
  </script>
</body>
</html>
"""

@app.route('/')
def home():
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/indicators')
def get_indicators():
    try:
        vix_data = yf.Ticker('^VIX').history(period='1d')
        vvix_data = yf.Ticker('^VVIX').history(period='1d')

        vix = vix_data['Close'].iloc[-1] if not vix_data.empty else None
        vvix = vvix_data['Close'].iloc[-1] if not vvix_data.empty else None

        if vix is None or vvix is None:
            return jsonify({'error': 'Kunde inte hämta data'}), 500

        derivata_vvix = vvix - 70

        if vix < 13:
            advice = "<strong>Köp:</strong> VIX under 13. Köp med 25% av allokerat kapital."
        elif vix > 20:
            advice = "<strong>Sälj:</strong> VIX över 20. Sälj allt."
        else:
            advice = "<strong>Behåll:</strong> Ingen tydlig signal."

        return jsonify({
            'vix': round(vix, 2),
            'vvix': round(vvix, 2),
            'derivataVvix': round(derivata_vvix, 2),
            'advice': advice
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
