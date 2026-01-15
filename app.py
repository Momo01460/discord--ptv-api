from flask import Flask, request, jsonify
import json, uuid, datetime

app = Flask(__name__)

DATA_FILE = "data.json"

def load_data():
    with open(DATA_FILE, "r") as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

def generate_key():
    return "ABO-" + uuid.uuid4().hex[:12].upper()

@app.route("/")
def home():
    return "API running ✅"

@app.route("/paypal/webhook", methods=["POST"])
def paypal_webhook():
    event = request.json
    event_type = event.get("event_type")

    if event_type == "PAYMENT.CAPTURE.COMPLETED":
        data = load_data()

        key = generate_key()
        expire = (datetime.datetime.now() + datetime.timedelta(days=30)).strftime("%Y-%m-%d")

        data["keys"].append({
            "key": key,
            "expire": expire
        })

        save_data(data)

        print("Paiement OK → clé créée :", key)

    return "", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3000)
