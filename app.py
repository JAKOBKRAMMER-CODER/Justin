import hmac
import json
import os
import secrets
import sys
import threading
import time
import uuid
from datetime import datetime
from functools import wraps
from pathlib import Path

from flask import (Flask, request, Response, render_template, render_template_string,
                   jsonify, session, redirect, url_for)
import ollama

MODELL = "llama3.2"
SYSTEM_PROMPT = "Du bist Justin, ein freundlicher Chatbot, der auf Deutsch antwortet."
DATEI = Path(__file__).with_name("chats.json")

# PIN kommt aus der Umgebungsvariable, nicht aus dem Code
PIN = os.environ.get("JUSTIN_PIN", "")
if not PIN:
    sys.exit("Fehler: Umgebungsvariable JUSTIN_PIN ist nicht gesetzt.\n"
             "macOS/Linux:  export JUSTIN_PIN=...\n"
             "Windows:      set JUSTIN_PIN=...")

app = Flask(__name__)
app.secret_key = os.environ.get("JUSTIN_SECRET") or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

sperre = threading.Lock()

# Schutz gegen Raten: nach 5 Fehlversuchen 5 Minuten Sperre pro IP
FEHLER_MAX = 5
SPERRE_SEKUNDEN = 300
fehlversuche = {}          # ip -> (anzahl, zeitpunkt)
fehler_sperre = threading.Lock()


def ip_gesperrt(ip):
    with fehler_sperre:
        anzahl, zeit = fehlversuche.get(ip, (0, 0))
        vergangen = time.time() - zeit
        if anzahl >= FEHLER_MAX and vergangen < SPERRE_SEKUNDEN:
            return int(SPERRE_SEKUNDEN - vergangen)
        return 0


def fehler_merken(ip):
    with fehler_sperre:
        anzahl, zeit = fehlversuche.get(ip, (0, 0))
        if time.time() - zeit >= SPERRE_SEKUNDEN:
            anzahl = 0
        fehlversuche[ip] = (anzahl + 1, time.time())


def fehler_zuruecksetzen(ip):
    with fehler_sperre:
        fehlversuche.pop(ip, None)


def login_pflicht(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("eingeloggt"):
            if request.path.startswith("/chats") or request.path == "/status":
                return jsonify(fehler="nicht angemeldet"), 401
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


def laden():
    if not DATEI.exists():
        return {}
    try:
        return json.loads(DATEI.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def speichern(chats):
    tmp = DATEI.with_suffix(".tmp")
    tmp.write_text(json.dumps(chats, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(DATEI)


LOGIN_HTML = """
<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Justin – Anmeldung</title>
<style>
  :root {
    --bg: #0f1115; --panel: #171a21; --rand: #262b36; --text: #e6e8ee;
    --muted: #8b93a7; --akzent: #7aa2f7; --akzent-text: #0f1115; --rot: #f7768e;
  }
  @media (prefers-color-scheme: light) {
    :root {
      --bg: #f4f5f8; --panel: #ffffff; --rand: #e1e4eb; --text: #1d2130;
      --muted: #6b7280; --akzent: #3b6fd8; --akzent-text: #ffffff;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
    background: var(--bg); color: var(--text);
    font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  }
  form {
    background: var(--panel); border: 1px solid var(--rand); border-radius: 16px;
    padding: 32px; width: min(340px, 90vw); text-align: center;
  }
  h1 { margin: 0 0 6px; font-size: 1.3rem; }
  p { color: var(--muted); margin: 0 0 20px; font-size: 0.9rem; }
  input {
    width: 100%; padding: 12px; font-size: 1.4rem; letter-spacing: 0.4em; text-align: center;
    border-radius: 10px; border: 1px solid var(--rand); background: var(--bg);
    color: var(--text); outline: none;
  }
  input:focus { border-color: var(--akzent); }
  button {
    margin-top: 16px; width: 100%; padding: 12px; border: none; border-radius: 10px;
    background: var(--akzent); color: var(--akzent-text); font-weight: 600;
    font-size: 1rem; cursor: pointer;
  }
  .fehler { color: var(--rot); margin: 14px 0 0; font-size: 0.9rem; }
</style>
</head>
<body>
<form method="post" action="/login">
  <h1>Justin</h1>
  <p>Bitte PIN eingeben</p>
  <input type="password" name="pin" inputmode="numeric" autocomplete="one-time-code" autofocus required>
  <button type="submit">Anmelden</button>
  {% if fehler %}<div class="fehler">{{ fehler }}</div>{% endif %}
</form>
</body>
</html>
"""


@app.route("/login", methods=["GET", "POST"])
def login():
    ip = request.remote_addr or "unbekannt"
    fehler = None

    if request.method == "POST":
        wartezeit = ip_gesperrt(ip)
        if wartezeit:
            fehler = f"Zu viele Fehlversuche. Bitte {wartezeit // 60 + 1} Minute(n) warten."
        else:
            eingegeben = request.form.get("pin", "")
            if hmac.compare_digest(eingegeben.encode(), PIN.encode()):
                fehler_zuruecksetzen(ip)
                session.clear()
                session["eingeloggt"] = True
                return redirect(url_for("startseite"))
            fehler_merken(ip)
            fehler = "Falsche PIN."

    return render_template_string(LOGIN_HTML, fehler=fehler)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_pflicht
def startseite():
    return render_template("index.html")


@app.route("/status")
@login_pflicht
def status():
    try:
        ollama.list()
        return jsonify(ok=True)
    except Exception:
        return jsonify(ok=False)


@app.route("/chats", methods=["GET"])
@login_pflicht
def chats_liste():
    with sperre:
        chats = laden()
    liste = [
        {"id": k, "titel": v["titel"], "erstellt": v["erstellt"]}
        for k, v in chats.items()
    ]
    liste.sort(key=lambda c: c["erstellt"], reverse=True)
    return jsonify(liste)


@app.route("/chats", methods=["POST"])
@login_pflicht
def chat_anlegen():
    chat_id = uuid.uuid4().hex
    with sperre:
        chats = laden()
        chats[chat_id] = {
            "titel": "Neuer Chat",
            "erstellt": datetime.now().isoformat(timespec="seconds"),
            "nachrichten": [],
        }
        speichern(chats)
    return jsonify(id=chat_id), 201


@app.route("/chats/<chat_id>", methods=["GET"])
@login_pflicht
def chat_laden(chat_id):
    with sperre:
        chats = laden()
    if chat_id not in chats:
        return Response("Nicht gefunden", status=404, mimetype="text/plain")
    return jsonify(chats[chat_id])


@app.route("/chats/<chat_id>", methods=["DELETE"])
@login_pflicht
def chat_loeschen(chat_id):
    with sperre:
        chats = laden()
        chats.pop(chat_id, None)
        speichern(chats)
    return "", 204


@app.route("/chats/<chat_id>/chat", methods=["POST"])
@login_pflicht
def chat_senden(chat_id):
    nachricht = (request.get_json(silent=True) or {}).get("nachricht", "").strip()
    if not nachricht:
        return Response("Leere Nachricht", status=400, mimetype="text/plain")

    with sperre:
        chats = laden()
        if chat_id not in chats:
            return Response("Chat nicht gefunden", status=404, mimetype="text/plain")
        chat = chats[chat_id]
        chat["nachrichten"].append({"role": "user", "content": nachricht})
        if chat["titel"] == "Neuer Chat":
            chat["titel"] = nachricht[:40] + ("…" if len(nachricht) > 40 else "")
        speichern(chats)
        verlauf = [{"role": "system", "content": SYSTEM_PROMPT}] + list(chat["nachrichten"])

    def stream():
        antwort = ""
        try:
            for teil in ollama.chat(model=MODELL, messages=verlauf, stream=True):
                text = teil["message"]["content"]
                antwort += text
                yield text
        except Exception as e:
            fehler = f"\n[Fehler bei Ollama: {e}]"
            antwort += fehler
            yield fehler
        finally:
            with sperre:
                chats = laden()
                if chat_id in chats:
                    chats[chat_id]["nachrichten"].append(
                        {"role": "assistant", "content": antwort}
                    )
                    speichern(chats)

    return Response(stream(), mimetype="text/plain")


if __name__ == "__main__":
    app.run(debug=False, port=5000, threaded=True)
