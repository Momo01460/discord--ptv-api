import os
import json
import sqlite3
import secrets
import string
from datetime import datetime, timedelta, timezone

import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# ---------------------------
# CONFIG (ENV VARS)
# ---------------------------
PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID", "")
PAYPAL_SECRET = os.getenv("PAYPAL_SECRET", "")
PAYPAL_WEBHOOK_ID = os.getenv("PAYPAL_WEBHOOK_ID", "")  # ID du webhook PayPal (important)
PAYPAL_BASE = os.getenv("PAYPAL_BASE", "https://api-m.paypal.com")  # sandbox: https://api-m.sandbox.paypal.com

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "")  # ex: https://ton-service.onrender.com
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")

DB_PATH = os.getenv("DB_PATH", "orders.db")

PRIX_FORMULES = {
    "1mois": 10.00,
    "3mois": 25.00,
    "6mois": 45.00,
    "12mois": 70.00,
}
DUREES_FORMULES = {
    "1mois": 30,
    "3mois": 90,
    "6mois": 180,
    "12mois": 365,
}
CURRENCY = "EUR"


# ---------------------------
# DB
# ---------------------------
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            order_id TEXT PRIMARY KEY,
            paypal_order_id TEXT,
            discord_user_id TEXT NOT NULL,
            plan TEXT NOT NULL,
            amount REAL NOT NULL,
            currency TEXT NOT NULL,
            status TEXT NOT NULL,
            code TEXT,
            expires_at TEXT,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

init_db()


# ---------------------------
# HELPERS
# ---------------------------
def now_utc_iso():
    return datetime.now(timezone.utc).isoformat()

def gen_order_id():
    return "ORD-" + secrets.token_hex(8).upper()

def gen_code():
    alphabet = string.ascii_uppercase + string.digits
    return "ABO-" + "".join(secrets.choice(alphabet) for _ in range(12))

def paypal_access_token():
    if not PAYPAL_CLIENT_ID or not PAYPAL_SECRET:
        raise RuntimeError("PayPal credentials missing (PAYPAL_CLIENT_ID/PAYPAL_SECRET).")

    r = requests.post(
        f"{PAYPAL_BASE}/v1/oauth2/token",
        auth=(PAYPAL_CLIENT_ID, PAYPAL_SECRET),
        headers={"Accept": "application/json"},
        data={"grant_type": "client_credentials"},
        timeout=20,
    )
    r.raise_for_status()
    return r.json()["access_token"]

def paypal_create_order(plan: str, order_id: str):
    token = paypal_access_token()
    amount = PRIX_FORMULES[plan]

    payload = {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "reference_id": order_id,
                "invoice_id": order_id,              # <-- on récupère ça plus tard
                "custom_id": order_id,               # <-- pareil
                "amount": {
                    "currency_code": CURRENCY,
                    "value": f"{amount:.2f}"
                },
                "description": f"Abonnement {plan}"
            }
        ],
        "application_context": {
            "brand_name": "Digital Access",
            "user_action": "PAY_NOW",
            "return_url": f"{PUBLIC_BASE_URL}/paypal/return",
            "cancel_url": f"{PUBLIC_BASE_URL}/paypal/cancel"
        }
    }

    r = requests.post(
        f"{PAYPAL_BASE}/v2/checkout/orders",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps(payload),
        timeout=20,
    )
    r.raise_for_status()
    data = r.json()
    approve = None
    for link in data.get("links", []):
        if link.get("rel") in ("approve", "payer", "checkout"):
            approve = link.get("href")
            break
    return data["id"], approve

def paypal_get_order(paypal_order_id: str):
    token = paypal_access_token()
    r = requests.get(
        f"{PAYPAL_BASE}/v2/checkout/orders/{paypal_order_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=20,
    )
    r.raise_for_status()
    return r.json()

def verify_paypal_webhook(payload: dict, headers: dict) -> bool:
    """
    Vérifie la signature du webhook PayPal via verify-webhook-signature.
    """
    if not PAYPAL_WEBHOOK_ID:
        # Si tu veux forcer la sécurité, enlève ce fallback.
        return False

    token = paypal_access_token()
    verify_payload = {
        "auth_algo": headers.get("PAYPAL-AUTH-ALGO"),
        "cert_url": headers.get("PAYPAL-CERT-URL"),
        "transmission_id": headers.get("PAYPAL-TRANSMISSION-ID"),
        "transmission_sig": headers.get("PAYPAL-TRANSMISSION-SIG"),
        "transmission_time": headers.get("PAYPAL-TRANSMISSION-TIME"),
        "webhook_id": PAYPAL_WEBHOOK_ID,
        "webhook_event": payload,
    }

    r = requests.post(
        f"{PAYPAL_BASE}/v1/notifications/verify-webhook-signature",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps(verify_payload),
        timeout=20,
    )
    r.raise_for_status()
    return r.json().get("verification_status") == "SUCCESS"

def discord_send_dm(user_id: str, content: str):
    """
    Envoie un DM via l’API Discord (sans avoir besoin que le bot tourne ici).
    """
    if not DISCORD_BOT_TOKEN:
        raise RuntimeError("Missing DISCORD_BOT_TOKEN")

    headers = {
        "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
        "Content-Type": "application/json"
    }

    # 1) créer/obtenir le canal DM
    r = requests.post(
        "https://discord.com/api/v10/users/@me/channels",
        headers=headers,
        data=json.dumps({"recipient_id": str(user_id)}),
        timeout=20,
    )
    r.raise_for_status()
    channel_id = r.json()["id"]

    # 2) envoyer le message
    r2 = requests.post(
        f"https://discord.com/api/v10/channels/{channel_id}/messages",
        headers=headers,
        data=json.dumps({"content": content}),
        timeout=20,
    )
    r2.raise_for_status()


# ---------------------------
# ROUTES
# ---------------------------
@app.get("/")
def home():
    return "API OK ✅", 200

@app.post("/create-order")
def create_order():
    """
    Appelé par le bot Discord quand un user clique "1/3/6/12 mois".
    Body JSON: { "plan": "1mois", "discord_user_id": "..." }
    Retour: { approval_url, order_id }
    """
    body = request.get_json(force=True, silent=True) or {}
    plan = body.get("plan")
    discord_user_id = str(body.get("discord_user_id", "")).strip()

    if plan not in PRIX_FORMULES:
        return jsonify({"error": "Plan invalide"}), 400
    if not discord_user_id.isdigit():
        return jsonify({"error": "discord_user_id invalide"}), 400
    if not PUBLIC_BASE_URL:
        return jsonify({"error": "PUBLIC_BASE_URL manquant"}), 500

    order_id = gen_order_id()
    amount = PRIX_FORMULES[plan]

    paypal_order_id, approve_url = paypal_create_order(plan, order_id)
    if not approve_url:
        return jsonify({"error": "Impossible de récupérer le lien PayPal"}), 500

    conn = db()
    conn.execute(
        "INSERT INTO orders(order_id, paypal_order_id, discord_user_id, plan, amount, currency, status, created_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (order_id, paypal_order_id, discord_user_id, plan, amount, CURRENCY, "CREATED", now_utc_iso()),
    )
    conn.commit()
    conn.close()

    return jsonify({
        "order_id": order_id,
        "paypal_order_id": paypal_order_id,
        "approval_url": approve_url
    })

@app.post("/paypal/webhook")
def paypal_webhook():
    payload = request.get_json(force=True, silent=True) or {}
    # Sécurité: vérifier signature
    try:
        ok = verify_paypal_webhook(payload, dict(request.headers))
    except Exception:
        ok = False

    if not ok:
        return "invalid webhook signature", 400

    event_type = payload.get("event_type", "")

    # On traite surtout quand le paiement est capturé (confirmé)
    if event_type != "PAYMENT.CAPTURE.COMPLETED":
        return "", 200

    # Récupérer l’order_id PayPal lié
    resource = payload.get("resource", {}) or {}
    paypal_order_id = None

    # souvent ici:
    related = (resource.get("supplementary_data", {}) or {}).get("related_ids", {}) or {}
    paypal_order_id = related.get("order_id")

    if not paypal_order_id:
        # fallback: parfois dans links / ou autre, sinon on ne peut pas.
        return "", 200

    # On récupère le détail de la commande PayPal pour lire invoice_id (= notre order_id)
    try:
        order = paypal_get_order(paypal_order_id)
    except Exception:
        return "", 200

    purchase_units = order.get("purchase_units", [])
    if not purchase_units:
        return "", 200

    our_order_id = purchase_units[0].get("invoice_id") or purchase_units[0].get("reference_id")
    if not our_order_id:
        return "", 200

    conn = db()
    row = conn.execute("SELECT * FROM orders WHERE order_id=?", (our_order_id,)).fetchone()
    if not row:
        conn.close()
        return "", 200

    # idempotence: si déjà livré
    if row["status"] == "DELIVERED":
        conn.close()
        return "", 200

    plan = row["plan"]
    days = DUREES_FORMULES.get(plan, 30)
    expires = (datetime.now(timezone.utc) + timedelta(days=days)).date().isoformat()
    code = gen_code()

    conn.execute(
        "UPDATE orders SET status=?, code=?, expires_at=? WHERE order_id=?",
        ("DELIVERED", code, expires, our_order_id)
    )
    conn.commit()
    conn.close()

    # Envoyer DM Discord
    msg = (
        "✅ Paiement confirmé\n"
        f"📦 Formule : {plan}\n"
        f"🔐 Code : `{code}`\n"
        f"📅 Expire le : {expires}\n"
        "\n"
        "Si tu as besoin d’aide, réponds ici."
    )

    try:
        discord_send_dm(row["discord_user_id"], msg)
    except Exception:
        # si DM impossible (privacy), tu peux logguer et gérer autrement
        pass

    return "", 200


# pages return/cancel (optionnel)
@app.get("/paypal/return")
def paypal_return():
    return "Paiement validé ✅ Tu peux retourner sur Discord.", 200

@app.get("/paypal/cancel")
def paypal_cancel():
    return "Paiement annulé ❌ Tu peux retourner sur Discord.", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3000)
