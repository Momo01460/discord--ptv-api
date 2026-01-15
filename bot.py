import discord
import json

TOKEN = "TOKEN"

intents = discord.Intents.default()
intents.messages = True
intents.dm_messages = True

client = discord.Client(intents=intents)

DATA_FILE = "data.json"

def load_data():
    with open(DATA_FILE, "r") as f:
        return json.load(f)

@client.event
async def on_ready():
    print(f"Bot connecté en tant que {client.user}")

@client.event
async def on_message(message):
    if message.author == client.user:
        return

    if message.content.lower() == "!last":
        data = load_data()
        if data["keys"]:
            last = data["keys"][-1]
            await message.author.send(
                f"✅ Paiement confirmé\n"
                f"🔐 Ton accès : {last['key']}\n"
                f"📅 Expire le : {last['expire']}"
            )
        else:
            await message.author.send("❌ Aucune clé trouvée.")

client.run(TOKEN)
