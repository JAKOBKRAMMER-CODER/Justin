import json
import hmac
import os
import secrets
import sys
import threading
import time
import uuid
from datetime import datetime
from functools import wraps
from pathlib import Path

from flask import (Flask, request, Response, render_template_string,
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


HTML = """
<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Justin</title>
<style>
  :root {
    --bg: #0f1115;
    --panel: #171a21;
    --rand: #262b36;
    --text: #e6e8ee;
    --muted: #8b93a7;
    --akzent: #7aa2f7;
    --akzent-text: #0f1115;
    --du: #2a3142;
    --bot: #1c212c;
    --gruen: #9ece6a;
    --rot: #f7768e;
  }
  @media (prefers-color-scheme: light) {
    :root {
      --bg: #f4f5f8;
      --panel: #ffffff;
      --rand: #e1e4eb;
      --text: #1d2130;
      --muted: #6b7280;
      --akzent: #3b6fd8;
      --akzent-text: #ffffff;
      --du: #dfe7ff;
      --bot: #ffffff;
    }
  }
  * { box-sizing: border-box; }
  html, body { height: 100%; margin: 0; }
  body {
    font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    background: var(--bg); color: var(--text);
    display: flex; flex-direction: column;
  }
  .app { flex: 1; display: flex; min-height: 0; }

  /* Seitenleiste */
  aside {
    width: 270px; flex-shrink: 0;
    background: var(--panel); border-right: 1px solid var(--rand);
    display: flex; flex-direction: column; min-height: 0;
  }
  aside .kopf { padding: 14px; border-bottom: 1px solid var(--rand); }
  .neu-knopf {
    width: 100%; padding: 10px; border: none; border-radius: 10px;
    background: var(--akzent); color: var(--akzent-text);
    font-weight: 600; cursor: pointer;
  }
  #liste { flex: 1; overflow-y: auto; padding: 8px; }
  .leerhinweis { color: var(--muted); font-size: 0.85rem; padding: 12px; text-align: center; }
  .eintrag {
    display: flex; align-items: center; gap: 6px;
    padding: 9px 10px; border-radius: 8px; cursor: pointer; margin-bottom: 2px;
  }
  .eintrag:hover { background: var(--bg); }
  .eintrag.aktiv { background: var(--du); }
  .eintrag-titel {
    flex: 1; min-width: 0; font-size: 0.9rem;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .weg {
    background: transparent; border: none; color: var(--muted);
    cursor: pointer; font-size: 0.85rem; padding: 2px 6px; border-radius: 6px;
    opacity: 0;
  }
  .eintrag:hover .weg, .eintrag.aktiv .weg { opacity: 1; }
  .weg:hover { color: var(--rot); }

  /* Hauptbereich */
  .haupt { flex: 1; display: flex; flex-direction: column; min-width: 0; }
  header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 14px 20px; background: var(--panel);
    border-bottom: 1px solid var(--rand); gap: 10px;
  }
  .titel { display: flex; align-items: center; gap: 10px; }
  .titel h1 { font-size: 1.05rem; margin: 0; font-weight: 600; }
  .status { font-size: 0.8rem; color: var(--muted); display: flex; align-items: center; gap: 6px; }
  .punkt { width: 8px; height: 8px; border-radius: 50%; background: var(--muted); }
  .punkt.ok { background: var(--gruen); }
  .punkt.fehler { background: var(--rot); }
  .rechts { display: flex; gap: 8px; }
  .knopf-klein {
    background: transparent; color: var(--muted);
    border: 1px solid var(--rand); padding: 6px 12px;
    border-radius: 8px; cursor: pointer; font-size: 0.85rem;
    text-decoration: none; display: inline-block;
  }
  .knopf-klein:hover { color: var(--text); border-color: var(--muted); }
  .knopf-klein:disabled { opacity: 0.5; cursor: default; }
  .menue { display: none; }

  #chat { flex: 1; min-height: 0; overflow-y: auto; padding: 24px 16px; }
  .inhalt { max-width: 760px; margin: 0 auto; }

  #leer { text-align: center; margin-top: 12vh; color: var(--muted); }
  #leer h2 { color: var(--text); font-weight: 600; margin-bottom: 6px; }
  .chips { display: flex; flex-wrap: wrap; gap: 8px; justify-content: center; margin-top: 18px; }
  .chip {
    background: var(--panel); color: var(--text);
    border: 1px solid var(--rand); padding: 8px 14px;
    border-radius: 999px; cursor: pointer; font-size: 0.9rem;
  }
  .chip:hover { border-color: var(--akzent); }

  .zeile { display: flex; gap: 10px; margin: 14px 0; align-items: flex-end; }
  .zeile.du { flex-direction: row-reverse; }
  .avatar {
    width: 32px; height: 32px; border-radius: 50%; flex-shrink: 0;
    display: flex; align-items: center; justify-content: center;
    font-size: 0.75rem; font-weight: 700;
    background: var(--akzent); color: var(--akzent-text);
  }
  .zeile.du .avatar { background: var(--rand); color: var(--text); }
  .blase {
    max-width: 78%; padding: 10px 14px; border-radius: 16px;
    background: var(--bot); border: 1px solid var(--rand);
    white-space: pre-wrap; word-wrap: break-word; line-height: 1.5;
  }
  .zeile.du .blase { background: var(--du); border-color: transparent; }

  .tippt { display: flex; gap: 4px; padding: 4px 2px; }
  .tippt span {
    width: 7px; height: 7px; border-radius: 50%; background: var(--muted);
    animation: huepfen 1.2s infinite ease-in-out;
  }
  .tippt span:nth-child(2) { animation-delay: 0.15s; }
  .tippt span:nth-child(3) { animation-delay: 0.3s; }
  @keyframes huepfen {
    0%, 80%, 100% { opacity: 0.25; transform: translateY(0); }
    40% { opacity: 1; transform: translateY(-3px); }
  }

  footer { background: var(--panel); border-top: 1px solid var(--rand); padding: 12px 16px 16px; }
  form { max-width: 760px; margin: 0 auto; display: flex; gap: 10px; align-items: flex-end; }
  textarea {
    flex: 1; resize: none; min-height: 46px; max-height: 160px;
    padding: 12px 14px; border-radius: 12px;
    border: 1px solid var(--rand); background: var(--bg); color: var(--text);
    font: inherit; line-height: 1.4; outline: none;
  }
  textarea:focus { border-color: var(--akzent); }
  #senden {
    height: 46px; padding: 0 20px; border: none; border-radius: 12px;
    background: var(--akzent); color: var(--akzent-text);
    font-weight: 600; cursor: pointer;
  }
  #senden:disabled { opacity: 0.45; cursor: default; }
  .hinweis { text-align: center; font-size: 0.75rem; color: var(--muted); margin-top: 8px; }

  /* Mobil: Seitenleiste als ausklappbares Menü */
  @media (max-width: 700px) {
    .menue { display: inline-block; }
    aside {
      position: fixed; top: 0; bottom: 0; left: -290px; z-index: 10;
      transition: left 0.2s ease;
    }
    aside.offen { left: 0; box-shadow: 4px 0 20px rgba(0,0,0,0.3); }
    .blase { max-width: 88%; }
    .status span.text { display: none; }
  }
</style>
</head>
<body>
<div class="app">
  <aside id="seitenleiste">
    <div class="kopf">
      <button class="neu-knopf" id="neu" type="button">+ Neuer Chat</button>
    </div>
    <div id="liste"></div>
  </aside>

  <div class="haupt">
    <header>
      <div class="titel">
        <button class="knopf-klein menue" id="menue" type="button">☰</button>
        <h1>Justin</h1>
        <span class="status" title="Verbindung zu Ollama">
          <span class="punkt" id="punkt"></span><span class="text" id="statustext">prüfe ...</span>
        </span>
      </div>
      <div class="rechts">
        <button class="knopf-klein" id="vorlesen" type="button">🔊 Vorlesen: an</button>
        <a class="knopf-klein" href="/logout">Abmelden</a>
      </div>
    </header>

    <main id="chat">
      <div class="inhalt" id="inhalt">
        <div id="leer">
          <h2>Womit kann ich helfen?</h2>
          <div>Schreib eine Nachricht oder wähle einen Vorschlag.</div>
          <div class="chips">
            <button class="chip" type="button" data-text="Erkläre mir Python-Listen mit einem Beispiel.">Python-Listen erklären</button>
            <button class="chip" type="button" data-text="Gib mir drei Ideen für ein Wochenend-Projekt.">Projektideen</button>
            <button class="chip" type="button" data-text="Schreib ein kurzes Gedicht über den Herbst.">Gedicht schreiben</button>
          </div>
        </div>
      </div>
    </main>

    <footer>
      <form id="formular">
        <textarea id="eingabe" rows="1" placeholder="Nachricht schreiben ..." autofocus></textarea>
        <button id="senden" type="submit">Senden</button>
      </form>
      <div class="hinweis">Enter zum Senden, Shift + Enter für eine neue Zeile</div>
    </footer>
  </div>
</div>

<script>
const chat = document.getElementById("chat");
const inhalt = document.getElementById("inhalt");
const formular = document.getElementById("formular");
const eingabe = document.getElementById("eingabe");
const senden = document.getElementById("senden");
const leer = document.getElementById("leer");
const punkt = document.getElementById("punkt");
const statustext = document.getElementById("statustext");
const vorlesenKnopf = document.getElementById("vorlesen");
const seitenleiste = document.getElementById("seitenleiste");
const liste = document.getElementById("liste");
const neuKnopf = document.getElementById("neu");
const menueKnopf = document.getElementById("menue");

let aktuelleId = null;

// ---------- Anmeldung prüfen ----------
function abgelaufen(r) {
  if (r.status === 401) {
    window.location.href = "/login";
    return true;
  }
  return false;
}

// ---------- Vorlesen (Browser-Sprachausgabe) ----------
let vorlesenAktiv = true;
const sprachAusgabeVorhanden = "speechSynthesis" in window;

if (!sprachAusgabeVorhanden) {
  vorlesenKnopf.disabled = true;
  vorlesenKnopf.textContent = "Vorlesen nicht verfügbar";
}

function deutscheStimme() {
  const stimmen = speechSynthesis.getVoices();
  return stimmen.find(s => s.lang === "de-DE") ||
         stimmen.find(s => s.lang.startsWith("de")) || null;
}

function vorlesen(text) {
  if (!sprachAusgabeVorhanden || !vorlesenAktiv || !text) return;
  speechSynthesis.cancel();
  const satz = new SpeechSynthesisUtterance(text);
  satz.lang = "de-DE";
  const stimme = deutscheStimme();
  if (stimme) satz.voice = stimme;
  satz.rate = 1;
  speechSynthesis.speak(satz);
}

vorlesenKnopf.addEventListener("click", () => {
  vorlesenAktiv = !vorlesenAktiv;
  vorlesenKnopf.textContent = vorlesenAktiv ? "🔊 Vorlesen: an" : "🔇 Vorlesen: aus";
  if (!vorlesenAktiv) speechSynthesis.cancel();
});

// ---------- Nachrichten anzeigen ----------
function nachBeimScrollen() {
  chat.scrollTop = chat.scrollHeight;
}

function zeileHinzufuegen(rolle, text) {
  leer.style.display = "none";
  const zeile = document.createElement("div");
  zeile.className = "zeile " + (rolle === "du" ? "du" : "bot");

  const avatar = document.createElement("div");
  avatar.className = "avatar";
  avatar.textContent = rolle === "du" ? "Du" : "JU";

  const blase = document.createElement("div");
  blase.className = "blase";
  blase.textContent = text;   // textContent verhindert HTML-Injection

  zeile.append(avatar, blase);
  inhalt.appendChild(zeile);
  nachBeimScrollen();
  return blase;
}

function tippenAnzeigen() {
  const blase = zeileHinzufuegen("bot", "");
  blase.innerHTML = '<div class="tippt"><span></span><span></span><span></span></div>';
  return blase;
}

function verlaufLeeren() {
  inhalt.querySelectorAll(".zeile").forEach(z => z.remove());
}

function eingabeAnpassen() {
  eingabe.style.height = "auto";
  eingabe.style.height = Math.min(eingabe.scrollHeight, 160) + "px";
}

// ---------- Seitenleiste ----------
function seitenleisteSchliessen() {
  seitenleiste.classList.remove("offen");
}

async function chatsLaden() {
  try {
    const r = await fetch("/chats");
    if (abgelaufen(r)) return;
    listeZeichnen(await r.json());
  } catch {
    liste.innerHTML = '<div class="leerhinweis">Chats konnten nicht geladen werden.</div>';
  }
}

function listeZeichnen(chats) {
  liste.innerHTML = "";
  if (!chats.length) {
    liste.innerHTML = '<div class="leerhinweis">Noch keine Chats gespeichert.</div>';
    return;
  }
  for (const c of chats) {
    const eintrag = document.createElement("div");
    eintrag.className = "eintrag" + (c.id === aktuelleId ? " aktiv" : "");

    const titel = document.createElement("span");
    titel.className = "eintrag-titel";
    titel.textContent = c.titel;

    const weg = document.createElement("button");
    weg.className = "weg";
    weg.type = "button";
    weg.title = "Chat löschen";
    weg.textContent = "✕";
    weg.addEventListener("click", (e) => {
      e.stopPropagation();
      chatLoeschen(c.id, c.titel);
    });

    eintrag.addEventListener("click", () => chatOeffnen(c.id));
    eintrag.append(titel, weg);
    liste.appendChild(eintrag);
  }
}

async function chatOeffnen(id) {
  const r = await fetch(`/chats/${id}`);
  if (abgelaufen(r) || !r.ok) return;
  const chat = await r.json();

  aktuelleId = id;
  verlaufLeeren();
  speechSynthesis.cancel();
  for (const m of chat.nachrichten) {
    zeileHinzufuegen(m.role === "user" ? "du" : "bot", m.content);
  }
  if (!chat.nachrichten.length) leer.style.display = "";
  chat.scrollTop = chat.scrollHeight;
  seitenleisteSchliessen();
  chatsLaden();
}

function neuerChat() {
  aktuelleId = null;
  verlaufLeeren();
  leer.style.display = "";
  speechSynthesis.cancel();
  seitenleisteSchliessen();
  chatsLaden();
  eingabe.focus();
}

async function chatLoeschen(id, titel) {
  if (!confirm(`Chat „${titel}“ wirklich löschen?`)) return;
  await fetch(`/chats/${id}`, { method: "DELETE" });
  if (id === aktuelleId) neuerChat();
  else chatsLaden();
}

neuKnopf.addEventListener("click", neuerChat);
menueKnopf.addEventListener("click", () => seitenleiste.classList.toggle("offen"));

// ---------- Senden ----------
async function nachrichtSenden(text) {
  senden.disabled = true;
  zeileHinzufuegen("du", text);
  const blase = tippenAnzeigen();

  try {
    if (!aktuelleId) {
      const r = await fetch("/chats", { method: "POST" });
      if (abgelaufen(r)) throw new Error("Bitte neu anmelden");
      if (!r.ok) throw new Error("Chat konnte nicht angelegt werden");
      aktuelleId = (await r.json()).id;
    }

    const antwort = await fetch(`/chats/${aktuelleId}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ nachricht: text })
    });
    if (abgelaufen(antwort)) throw new Error("Bitte neu anmelden");
    if (!antwort.ok) throw new Error("HTTP " + antwort.status);

    const reader = antwort.body.getReader();
    const decoder = new TextDecoder();
    let erster = true;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      if (erster) { blase.textContent = ""; erster = false; }
      blase.textContent += decoder.decode(value, { stream: true });
      nachBeimScrollen();
    }

    vorlesen(blase.textContent);
  } catch (err) {
    blase.textContent = "Fehler: " + err.message;
    blase.style.color = "var(--rot)";
  } finally {
    senden.disabled = false;
    eingabe.focus();
    chatsLaden();
  }
}

formular.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = eingabe.value.trim();
  if (!text || senden.disabled) return;
  eingabe.value = "";
  eingabeAnpassen();
  nachrichtSenden(text);
});

eingabe.addEventListener("input", eingabeAnpassen);
eingabe.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    formular.requestSubmit();
  }
});

document.querySelectorAll(".chip").forEach((chip) => {
  chip.addEventListener("click", () => nachrichtSenden(chip.dataset.text));
});

// ---------- Status ----------
async function statusPruefen() {
  try {
    const r = await fetch("/status");
    if (abgelaufen(r)) return;
    const d = await r.json();
    punkt.className = "punkt " + (d.ok ? "ok" : "fehler");
    statustext.textContent = d.ok ? "Ollama verbunden" : "Ollama nicht erreichbar";
  } catch {
    punkt.className = "punkt fehler";
    statustext.textContent = "Server nicht erreichbar";
  }
}

statusPruefen();
setInterval(statusPruefen, 30000);
chatsLaden();
</script>
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
    return render_template_string(HTML)


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
