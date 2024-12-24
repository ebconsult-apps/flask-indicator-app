from flask import Flask, render_template_string

app = Flask(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Handelsindikatorer</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0-alpha3/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body class="bg-light">
  <div class="container py-4">
    <h1 class="text-center">Handelsindikatorer</h1>
    <div id="indicator" class="alert alert-primary text-center my-3">Laddar data...</div>
    <div id="advice" class="text-center my-3"></div>
  </div>
  <script>
    async function fetchIndicators() {
      const vix = 12;  // Placeholder
      const vvix = 80; // Placeholder
      const derivataVvix = (vvix - 70); // Enkel derivata

      let message = '';
      if (vix < 13) {
        message = '<strong>Köp:</strong> VIX under 13. Köp med 25% av allokerat kapital.';
      } else if (vix > 20) {
        message = '<strong>Sälj:</strong> VIX över 20. Sälj allt.';
      } else {
        message = '<strong>Behåll:</strong> Ingen signal.';
      }

      document.getElementById('indicator').innerHTML = `
        <p>VIX: ${vix}</p>
        <p>VVIX: ${vvix}</p>
        <p>Derivata VVIX: ${derivataVvix}</p>`;
      document.getElementById('advice').innerHTML = `<div class="alert alert-info">${message}</div>`;
    }

    fetchIndicators();
  </script>
</body>
</html>
"""

@app.route('/')
def home():
    return render_template_string(HTML_TEMPLATE)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
