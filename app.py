from flask import Flask, request, send_file, jsonify
from pypdf import PdfReader, PdfWriter
import io, os, json
from datetime import datetime

app = Flask(__name__)

TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "template_alperia.pdf")
OFFERTE_PATH  = os.path.join(os.path.dirname(__file__), "offerte.json")

# --- Offerte: carica da file o usa default ---
DEFAULT_OFFERTE = {
    "luce": [
        {"nome": "Direct Energy",      "codice": "DIRE-EE"},
        {"nome": "Direct Energy Plus", "codice": "DIRE-EE-PLUS"},
        {"nome": "Alperia Luce Fissa", "codice": "ALF-EE"},
    ],
    "gas": [
        {"nome": "Gas Smile Start", "codice": "GSS-GN"},
        {"nome": "Gas Smile Fisso", "codice": "GSF-GN"},
        {"nome": "Alperia Gas Fissa", "codice": "AGF-GN"},
    ]
}

def load_offerte():
    if os.path.exists(OFFERTE_PATH):
        with open(OFFERTE_PATH) as f:
            return json.load(f)
    return DEFAULT_OFFERTE

def save_offerte(data):
    with open(OFFERTE_PATH, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# --- Routes ---

@app.route("/")
def index():
    return app.send_static_file("index.html")

@app.route("/api/offerte", methods=["GET"])
def get_offerte():
    return jsonify(load_offerte())

@app.route("/api/offerte", methods=["POST"])
def set_offerte():
    data = request.json
    save_offerte(data)
    return jsonify({"ok": True})

@app.route("/api/offerte/aggiungi", methods=["POST"])
def aggiungi_offerta():
    d = request.json  # {tipo: "luce"|"gas", nome: "...", codice: "..."}
    offerte = load_offerte()
    tipo = d.get("tipo", "luce")
    offerte[tipo].append({"nome": d["nome"], "codice": d["codice"]})
    save_offerte(offerte)
    return jsonify({"ok": True, "offerte": offerte})

@app.route("/api/offerte/elimina", methods=["POST"])
def elimina_offerta():
    d = request.json  # {tipo: "luce"|"gas", index: N}
    offerte = load_offerte()
    tipo = d.get("tipo")
    idx  = d.get("index")
    if 0 <= idx < len(offerte[tipo]):
        offerte[tipo].pop(idx)
        save_offerte(offerte)
    return jsonify({"ok": True, "offerte": offerte})

def get_api_key():
    """Legge la chiave API da variabile d'ambiente o dal file config.json"""
    # 1. Variabile d'ambiente (priorità massima)
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if key:
        return key
    # 2. File config.json nella stessa cartella dell'app
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    if os.path.exists(config_path):
        with open(config_path) as f:
            cfg = json.load(f)
        return cfg.get("ANTHROPIC_API_KEY", "")
    return ""

@app.route("/api/check_api_key")
def check_api_key():
    key = get_api_key()
    return jsonify({"configurata": bool(key), "lunghezza": len(key)})

@app.route("/api/salva_api_key", methods=["POST"])
def salva_api_key():
    key = request.json.get("api_key", "").strip()
    if not key.startswith("sk-ant-"):
        return jsonify({"error": "Chiave non valida (deve iniziare con sk-ant-)"}), 400
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    cfg = {}
    if os.path.exists(config_path):
        with open(config_path) as f:
            cfg = json.load(f)
    cfg["ANTHROPIC_API_KEY"] = key
    with open(config_path, "w") as f:
        json.dump(cfg, f, indent=2)
    return jsonify({"ok": True})

@app.route("/api/leggi_bolletta", methods=["POST"])
def leggi_bolletta():
    """Usa Claude API per estrarre dati tecnici dalla bolletta (PDF o immagine)"""
    import base64, urllib.request, urllib.error

    api_key = get_api_key()
    if not api_key:
        return jsonify({"error": "API key non configurata. Vai nelle Impostazioni e inserisci la tua chiave Anthropic."}), 401

    file = request.files.get("file")
    if not file:
        return jsonify({"error": "Nessun file ricevuto"}), 400

    data_bytes = file.read()
    mime = file.content_type or "application/pdf"
    # Normalizza mime type per immagini
    if mime in ("image/jpg",): mime = "image/jpeg"
    b64 = base64.standard_b64encode(data_bytes).decode()

    if mime == "application/pdf":
        media_block = {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": b64}}
    else:
        media_block = {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}}

    prompt = """Analizza questa bolletta di energia elettrica o gas naturale italiana e estrai i seguenti dati in formato JSON puro (nessun testo prima o dopo, solo JSON valido):

{
  "intestatario_cognome": "",
  "intestatario_nome": "",
  "codice_fiscale": "",
  "indirizzo_fornitura": "",
  "civico": "",
  "comune": "",
  "cap": "",
  "provincia": "",
  "pod": "",
  "pdr": "",
  "fornitore": "",
  "consumo_kwh": "",
  "consumo_smc": "",
  "potenza_kw": "",
  "tipo_fornitura": "luce|gas|entrambi",
  "remi": ""
}

Se un dato non è presente lascia il campo con stringa vuota "". Estrai solo dati certi e visibili nel documento. Per tipo_fornitura scrivi esattamente: luce, gas, oppure entrambi."""

    payload = json.dumps({
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1000,
        "messages": [{"role": "user", "content": [media_block, {"type": "text", "text": prompt}]}]
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
            "x-api-key": api_key
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            result = json.loads(resp.read())
        text = result["content"][0]["text"].strip()
        # Pulisci eventuali backtick markdown
        if "```" in text:
            parts = text.split("```")
            for p in parts:
                p = p.strip()
                if p.startswith("json"): p = p[4:].strip()
                if p.startswith("{"): text = p; break
        extracted = json.loads(text.strip())
        return jsonify({"ok": True, "dati": extracted})
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        return jsonify({"error": f"HTTP {e.code}: {body[:200]}"}), 500
    except json.JSONDecodeError as e:
        return jsonify({"error": f"Risposta non valida dall'AI: {str(e)}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/compila", methods=["POST"])
def compila():
    d = request.json
    today = datetime.now().strftime("%d/%m/%Y")

    tipo_luce_map     = {"switch": "/Auswahl4", "subentro": "/Auswahl5", "voltura": "/Auswahl6", "nuova": "/Auswahl7"}
    tipo_gas_map      = {"switch": "/Auswahl4", "subentro": "/Auswahl5", "voltura": "/Auswahl6", "nuova": "/Auswahl7"}
    tipologia_uso_map = {"residente": "/Auswahl1", "non_residente": "/Auswahl2", "altri": "/Auswahl3"}
    titolo_map        = {"proprieta": "/proprietà/usufrutto", "locazione": "/locazione/affitto", "altro": "/detenzione altro titolo"}
    doc_map           = {"ci": "/carta identità", "patente": "/patente", "passaporto": "/passaporto"}

    fields = {}

    # Header
    fields["Agente"] = d.get("agente", "")
    fields["Proposta"] = d.get("proposta", "")

    # Dati cliente
    fields["Cognome"]  = d.get("cognome", "")
    fields["Nome"]     = d.get("nome", "")
    fields["CF"]       = d.get("cf", "").upper()
    fields["Residente si no"] = "/Auswahl1" if d.get("residente", True) else "/Auswahl2"
    fields["Indirizzo di residenza"] = d.get("indirizzo_res", "")
    fields["N"]        = d.get("civico_res", "")
    fields["Comune"]   = d.get("comune_res", "")
    fields["CAP"]      = d.get("cap_res", "")
    fields["Prov"]     = d.get("prov_res", "")
    fields["Documento d'identità"] = doc_map.get(d.get("tipo_doc", "ci"), "/0")
    fields["Telefono"] = d.get("telefono", "")
    fields["Cellulare"]= d.get("cellulare", "")
    fields["Email"]    = d.get("email", "")
    fields["Email per invio bolletta e altre comunicazioni contrattualmente previste"] = \
        d.get("email_bolletta") or d.get("email", "")

    # Indirizzo fornitura se diverso
    if not d.get("fornitura_uguale_residenza", True):
        fields["Indirizzo"]  = d.get("indirizzo_forn", "")
        fields["N_2"]        = d.get("civico_forn", "")
        fields["Comune_2"]   = d.get("comune_forn", "")
        fields["CAP_2"]      = d.get("cap_forn", "")
        fields["Prov_2"]     = d.get("prov_forn", "")

    fields["titolo sull'immobile"] = titolo_map.get(d.get("titolo_immobile", "proprieta"), "/0")
    fields["DATA"] = today

    # --- ENERGIA ELETTRICA ---
    if d.get("vuole_luce", False):
        fields["EE sì"]               = "/On"
        fields["Strom Art der Anfrage"] = tipo_luce_map.get(d.get("tipo_richiesta_luce", "switch"), "/0")
        fields["POD A"]               = d.get("pod", "")
        fields["Fornitore Uscente"]   = d.get("fornitore_uscente_luce", "")
        fields["Consumo annuo kWh"]   = str(d.get("consumo_kwh", ""))
        fields["Potenza impegnata kW_2"] = str(d.get("potenza_kw", ""))
        fields["Tipologia d'uso"]     = tipologia_uso_map.get(d.get("tipologia_uso", "residente"), "/0")
        fields["Nome Offerta sottoscritta"] = d.get("offerta_luce_nome", "")
        fields["Codice offerta"]      = d.get("offerta_luce_codice", "")
        if d.get("data_attivazione_luce"):
            fields["Data Attivazione"] = d.get("data_attivazione_luce", "")

    # --- GAS NATURALE ---
    if d.get("vuole_gas", False):
        fields["Gas sì"]               = "/On"
        fields["Gas  Art der Anfrage"] = tipo_gas_map.get(d.get("tipo_richiesta_gas", "switch"), "/0")
        fields["PDR"]                  = d.get("pdr", "")
        fields["Fornitore Uscente_4"]  = d.get("fornitore_uscente_gas", "")
        fields["Consumo annuo smc"]    = str(d.get("consumo_smc", ""))
        fields["REMI"]                 = d.get("remi", "")
        fields["Nome Offerta sottoscritta_2"] = d.get("offerta_gas_nome", "")
        fields["Codice offerta_2"]     = d.get("offerta_gas_codice", "")
        if d.get("data_attivazione_gas"):
            fields["Data Attivazione_2"] = d.get("data_attivazione_gas", "")
        # Categoria d'uso gas — multi-selezione
        categorie = d.get("categorie_gas", [])
        if "riscaldamento" in categorie:
            fields["Kontrollkästchen7"] = "/Ja"
        if "cottura" in categorie:
            fields["Kontrollkästchen8"] = "/Ja"
        if "acqua_calda" in categorie:
            fields["Kontrollkästchen9"] = "/Ja"

    # --- SEPA ---
    if d.get("iban"):
        debitore_uguale = d.get("debitore_uguale_titolare", True)
        addr = f"{d.get('indirizzo_res','')} {d.get('civico_res','')}, {d.get('cap_res','')} {d.get('comune_res','')}".strip(", ")
        fields["IBAN"]                 = d.get("iban", "").upper().replace(" ", "")
        fields["Presso la Banca"]      = d.get("banca", "")
        fields["Cognome Nome del Debitore"] = f"{d.get('cognome','')} {d.get('nome','')}".strip()
        fields["Indirizzo Via  Piazza  CAP e COMUNE"] = addr
        fields["Zahlungspflichtiger gleich Kontoinhaber"] = "/Auswahl1" if debitore_uguale else "/Auswahl2"
        fields["DATA_2"] = today
        if not debitore_uguale:
            fields["Titolare del Conto Corrente (se diverso dal Debitore) Cognome"] = d.get("titolare_cognome", "")
            fields["Titolare del Conto Corrente (se diverso dal Debitore) Nome"]    = d.get("titolare_nome", "")
            fields["Codice Fiscale  Partita IVA del Titolare del Conto Corrente"]   = d.get("titolare_cf", "")

    # --- PAGINA 2: Privacy ---
    fields["dichiara di aver preso visione dellInformativa di Alperia Smart Services Srl al Cliente in merito al Trattamento dei Dati Personali ai sensi degli articoli 13 e 14 del Regolamento generale sulla protezione"] = "/On"
    fields["Verarbeitung der Daten"]   = "/Auswahl3"   # Marketing: SI
    fields["Verarbeitung der Daten 2"] = "/Auswahl6"   # Profilazione: NO
    fields["Verarbeitung der Daten 3"] = "/Auswahl7"   # Comunicaz. terzi: NO
    fields["DATA_3"] = today   # Privacy consenso

    # DATA_4 = Richiesta esecuzione anticipata (se richiesta)
    if d.get("esecuzione_anticipata", False):
        fields["DATA_4"] = today
    
    fields["DATA_5"] = today   # Formulazione proposta
    fields["DATA_6"] = today   # Approvazione clausole specifiche

    # Rimuovi None
    fields = {k: v for k, v in fields.items() if v is not None}

    # --- Compila PDF ---
    reader = PdfReader(TEMPLATE_PATH)
    writer = PdfWriter()
    writer.append(reader)
    writer.update_page_form_field_values(writer.pages[2], fields)
    writer.update_page_form_field_values(writer.pages[3], fields)

    buf = io.BytesIO()
    writer.write(buf)
    buf.seek(0)

    nome_file = f"Contratto_Alperia_{d.get('cognome','')}_{d.get('nome','')}.pdf".replace(" ", "_")
    return send_file(buf, mimetype="application/pdf",
                     as_attachment=True, download_name=nome_file)

if __name__ == "__main__":
    app.run(debug=True, port=7860)
    if __name__ == "__main__":
       import os
       port = int(os.environ.get("PORT", 10000))
       app.run(host="0.0.0.0", port=port)
  
