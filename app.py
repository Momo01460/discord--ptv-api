from flask import Flask, request

app = Flask(__name__)

@app.route("/")
def home():
    return "API running ✅"

@app.route("/paypal/webhook", methods=["POST"])
def paypal_webhook():
    print("Webhook PayPal reçu")
    return "", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3000)
