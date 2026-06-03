from flask import Flask, request, send_file, jsonify
from pypdf import PdfReader, PdfWriter
import io, os, json, copy
from datetime import datetime

app = Flask(__name__)

TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "template_alperia.pdf")

DEFAULT_OFFERTE = {
    "luce": [
        {"nome": "Alperia Direct Energy", "codice": "DIRE-EE"},
    ],
    "gas": [
        {"nome": "Alperia Gas Smile Start", "codice": "GSS-GN"},
    ],
}

DATABASE_URL = os.environ.get("DATABASE_URL", "")


def get_db():
    import psycopg2
    url = DATABASE_URL
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return psycopg2.connect(url)


def init_db():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS offerte (
                id SERIAL PRIMARY KEY,
                tipo TEXT NOT NULL,
                nome TEXT NOT NULL,
                codice TEXT NOT NULL
            )
        """)
        cur.execute("SELECT COUNT(*) FROM offerte")
        if cur.fetchone()[0] == 0:
            for tipo, lista in DEFAULT_OFFERTE.items():
                for o in lista:
                    cur.execute(
                        "INSERT INTO offerte (tipo, nome, codice) VALUES (%s, %s, %s)",
                        (tipo, o["nome"], o["codice"])
                    )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"DB init warning: {e}")


_offerte_runtime = copy.deepcopy(DEFAULT_OFFERTE)


def load_offerte():
    if DATABASE_URL:
        conn = None
        try:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("SELECT tipo, nome, codice FROM offerte ORDER BY id")
            rows = cur.fetchall()
            cur.close()
            result = {"luce": [], "gas": []}
            for tipo, nome, codice in rows:
                if tipo in result:
                    result[tipo].append({"nome": nome, "codice": codice})
            return result
        except Exception as e:
            print(f"DB load error: {e}")
            return copy.deepcopy(_offerte_runtime)
        finally:
            if conn:
                conn.close()
    return copy.deepcopy(_offerte_runtime)


def save_offerte(data):
    if DATABASE_URL:
        conn = None
        try:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("DELETE FROM offerte")
            for tipo, lista in data.items():
                for o in lista:
                    cur.execute(
                        "INSERT INTO offerte (tipo, nome, codice) VALUES (%s, %s, %s)",
                        (tipo, o["nome"], o["codice"])
                    )
            conn.commit()
            cur.close()
            return
        except Exception as e:
            print(f"DB save error: {e}")
        finally:
            if conn:
                conn.close()
    global _offerte_runtime
    _offerte_runtime = copy.deepcopy(data)


if DATABASE_URL:
    init_db()


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
    d = request.json
    offerte = load_offerte()
    tipo = d.get("tipo", "luce")
    if tipo not in offerte:
        return jsonify({"error": "tipo non valido"}), 400
    nome = d.get("nome", "").strip()
    codice = d.get("codice", "").strip()
    if not nome or not codice:
        return jsonify({"error": "nome e codice obbligatori"}), 400
    offerte[tipo].append({"nome": nome, "codice": codice})
    save_offerte(offerte)
    return jsonify({"ok": True, "offerte": offerte})


@app.route("/api/offerte/elimina", methods=["POST"])
def elimina_offerta():
    d = request.json
    tipo = d.get("tipo")
    idx  = d.get("index")
    # FIX: validazione parametri prima dell'uso
    if not tipo or idx is None or not isinstance(idx, int):
        return jsonify({"error": "parametri mancanti o non validi"}), 400
    offerte = load_offerte()
    if tipo not in offerte:
        return jsonify({"error": "tipo non valido"}), 400
    if 0 <= idx < len(offerte[tipo]):
        offerte[tipo].pop(idx)
        save_offerte(offerte)
    return jsonify({"ok": True, "offerte": offerte})


@app.route("/api/offerte/modifica", methods=["POST"])
def modifica_offerta():
    d      = request.json
    tipo   = d.get("tipo")
    idx    = d.get("index")
    nome   = d.get("nome", "").strip()
    codice = d.get("codice", "").strip()
    # FIX: validazione parametri prima dell'uso
    if not tipo or idx is None or not isinstance(idx, int):
        return jsonify({"error": "parametri mancanti o non validi"}), 400
    offerte = load_offerte()
    if tipo not in offerte:
        return jsonify({"error": "tipo non valido"}), 400
    if nome and codice and 0 <= idx < len(offerte[tipo]):
        offerte[tipo][idx] = {"nome": nome, "codice": codice}
        save_offerte(offerte)
    return jsonify({"ok": True, "offerte": offerte})


def get_api_key():
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if key:
        return key
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
    import base64, urllib.request, urllib.error

    api_key = get_api_key()
    if not api_key:
        return jsonify({"error": "API key non configurata. Vai nelle Impostazioni e inserisci la tua chiave Anthropic."}), 401

    file = request.files.get("file")
    if not file:
        return jsonify({"error": "Nessun file ricevuto"}), 400

    data_bytes = file.read()
    mime = file.content_type or "application/pdf"
    # FIX: normalizza tutti gli alias comuni di MIME type
    mime_map = {"image/jpg": "image/jpeg", "image/JPG": "image/jpeg"}
    mime = mime_map.get(mime, mime)

    b64 = base64.standard_b64encode(data_bytes).decode()

    if mime == "application/pdf":
        media_block = {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": b64}}
    elif mime in ("image/jpeg", "image/png", "image/gif", "image/webp"):
        media_block = {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}}
    else:
        return jsonify({"error": f"Tipo file non supportato: {mime}. Usa PDF, JPEG o PNG."}), 400

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
        if "```" in text:
            parts = text.split("```")
            for p in parts:
                p = p.strip()
                if p.startswith("json"):
                    p = p[4:].strip()
                if p.startswith("{"):
                    text = p
                    break
        extracted = json.loads(text.strip())
        return jsonify({"ok": True, "dati": extracted})
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        return jsonify({"error": f"HTTP {e.code}: {body[:200]}"}), 500
    except json.JSONDecodeError as e:
        return jsonify({"error": f"Risposta non valida dall'AI: {str(e)}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def fill_radio_groups(writer, radio_fields):
    """
    Setta i radio button direttamente sugli oggetti figlio via /AS e /V sul parent.
    pypdf non gestisce correttamente i radio group tramite update_page_form_field_values,
    quindi è necessario operare direttamente sulle annotazioni.
    radio_fields: dict { parent_name: value }
    """
    from pypdf.generic import NameObject
    for page in writer.pages:
        if "/Annots" not in page:
            continue
        for annot_ref in page["/Annots"]:
            annot = annot_ref.get_object()
            parent_ref = annot.get("/Parent")
            if not parent_ref:
                continue
            parent = parent_ref.get_object()
            parent_name = str(parent.get("/T", ""))
            if parent_name not in radio_fields:
                continue
            desired_value = radio_fields[parent_name]
            ap = annot.get("/AP", {})
            if "/N" in ap:
                n = ap["/N"].get_object()
                if hasattr(n, 'keys'):
                    options = [str(k) for k in n.keys()]
                    if desired_value in options:
                        annot_ref.get_object()[NameObject("/AS")] = NameObject(desired_value)
                        parent_ref.get_object()[NameObject("/V")]  = NameObject(desired_value)


@app.route("/api/compila", methods=["POST"])
def compila():
    d = request.json
    today = datetime.now().strftime("%d/%m/%Y")

    tipo_map = {"switch": "/Auswahl4", "subentro": "/Auswahl5", "voltura": "/Auswahl6", "nuova": "/Auswahl7"}

    def get_tipo(val):
        if isinstance(val, list) and val:
            return tipo_map.get(val[0], "/Auswahl4")
        if isinstance(val, str) and val:
            return tipo_map.get(val, "/Auswahl4")
        return "/Auswahl4"

    # FIX: rimossi tipo_luce_map e tipo_gas_map che erano alias inutilizzati di tipo_map
    tipologia_uso_map = {"residente": "/Auswahl1", "non_residente": "/Auswahl2", "altri": "/Auswahl3"}
    titolo_map        = {"proprieta": "/proprietà/usufrutto", "locazione": "/locazione/affitto", "altro": "/detenzione altro titolo"}
    doc_map           = {"ci": "/carta identità", "patente": "/patente", "passaporto": "/passaporto"}

    fields = {}

    fields["Cognome"] = d.get("cognome", "")
    fields["Nome"]    = d.get("nome", "")
    fields["CF"]      = d.get("cf", "").upper()
    fields["Indirizzo di residenza"] = d.get("indirizzo_res", "")
    fields["N"]      = d.get("civico_res", "")
    fields["Comune"] = d.get("comune_res", "")
    fields["CAP"]    = d.get("cap_res", "")
    fields["Prov"]   = d.get("prov_res", "")
    fields["Telefono"]  = d.get("telefono", "")
    fields["Cellulare"] = d.get("cellulare", "")
    fields["Email"]     = d.get("email", "")
    fields["Email per invio bolletta e altre comunicazioni contrattualmente previste"] = \
        d.get("email_bolletta") or d.get("email", "")

    if not d.get("fornitura_uguale_residenza", True):
        fields["Indirizzo"] = d.get("indirizzo_forn", "")
        fields["N_2"]       = d.get("civico_forn", "")
        fields["Comune_2"]  = d.get("comune_forn", "")
        fields["CAP_2"]     = d.get("cap_forn", "")
        fields["Prov_2"]    = d.get("prov_forn", "")

    fields["DATA"] = today

    # Radio group separati — vanno settati con fill_radio_groups, non con update_page_form_field_values
    # FIX: "Gas  Art der Anfrage" ha due spazi nel PDF (nome campo verificato sull'oggetto PDF)
    radio_fields = {
        "Residente si no": "/Auswahl1" if d.get("residente", True) else "/Auswahl2",
        "titolo sull'immobile": titolo_map.get(d.get("titolo_immobile", "proprieta"), "/proprietà/usufrutto"),
        "Documento d'identità": doc_map.get(d.get("tipo_doc", "ci"), "/carta identità"),
        "Zahlungspflichtiger gleich Kontoinhaber": "/Auswahl1" if d.get("debitore_uguale_titolare", True) else "/Auswahl2",
        "Verarbeitung der Daten":   "/Auswahl3",
        "Verarbeitung der Daten 2": "/Auswahl6",
        "Verarbeitung der Daten 3": "/Auswahl7",
    }

    # Rimuovi i campi radio dai fields testo (erano settati erroneamente prima)
    for k in ["Residente si no", "titolo sull'immobile", "Documento d'identità",
              "Zahlungspflichtiger gleich Kontoinhaber",
              "Verarbeitung der Daten", "Verarbeitung der Daten 2", "Verarbeitung der Daten 3"]:
        fields.pop(k, None)

    if d.get("vuole_luce", False):
        fields["EE sì"] = "/On"
        fields["POD A"]                    = d.get("pod", "")
        fields["Fornitore Uscente"]         = d.get("fornitore_uscente_luce", "")
        fields["Consumo annuo kWh"]         = str(d.get("consumo_kwh", ""))
        fields["Potenza impegnata kW_2"]    = str(d.get("potenza_kw", ""))
        fields["Nome Offerta sottoscritta"] = d.get("offerta_luce_nome", "")
        fields["Codice offerta"]            = d.get("offerta_luce_codice", "")
        if d.get("data_attivazione_luce"):
            fields["Data Attivazione"] = d.get("data_attivazione_luce", "")
        # FIX: tipo richiesta luce via radio group
        radio_fields["Strom Art der Anfrage"]  = get_tipo(d.get("tipo_richiesta_luce", "switch"))
        # FIX: tipologia uso via radio group
        radio_fields["Tipologia d'uso"] = tipologia_uso_map.get(d.get("tipologia_uso", "residente"), "/Auswahl1")

    if d.get("vuole_gas", False):
        fields["Gas sì"] = "/On"
        fields["PDR"]                         = d.get("pdr", "")
        fields["Fornitore Uscente_4"]         = d.get("fornitore_uscente_gas", "")
        fields["Consumo annuo smc"]           = str(d.get("consumo_smc", ""))
        fields["REMI"]                        = d.get("remi", "")
        fields["Nome Offerta sottoscritta_2"] = d.get("offerta_gas_nome", "")
        fields["Codice offerta_2"]            = d.get("offerta_gas_codice", "")
        if d.get("data_attivazione_gas"):
            fields["Data Attivazione_2"] = d.get("data_attivazione_gas", "")
        categorie = d.get("categorie_gas", [])
        if "riscaldamento" in categorie:
            fields["Kontrollkästchen7"] = "/Ja"
        if "cottura" in categorie:
            fields["Kontrollkästchen8"] = "/Ja"
        if "acqua_calda" in categorie:
            fields["Kontrollkästchen9"] = "/Ja"
        # FIX: tipo richiesta gas via radio group — nome campo con DOPPIO SPAZIO nel PDF
        radio_fields["Gas  Art der Anfrage"] = get_tipo(d.get("tipo_richiesta_gas", "switch"))

    if d.get("iban"):
        debitore_uguale = d.get("debitore_uguale_titolare", True)
        addr = (
            f"{d.get('indirizzo_res', '')} {d.get('civico_res', '')}, "
            f"{d.get('cap_res', '')} {d.get('comune_res', '')}"
        ).strip(", ")
        fields["IBAN"]                              = d.get("iban", "").upper().replace(" ", "")
        fields["Presso la Banca"]                   = d.get("banca", "")
        fields["Cognome Nome del Debitore"]         = f"{d.get('cognome', '')} {d.get('nome', '')}".strip()
        fields["Indirizzo Via Piazza CAP e COMUNE"] = addr
        fields["DATA_2"] = today
        if not debitore_uguale:
            fields["Titolare del Conto Corrente (se diverso dal Debitore) Cognome"] = d.get("titolare_cognome", "")
            fields["Titolare del Conto Corrente (se diverso dal Debitore) Nome"]    = d.get("titolare_nome", "")
            fields["Codice Fiscale Partita IVA del Titolare del Conto Corrente"]    = d.get("titolare_cf", "")

    fields["dichiara di aver preso visione dellInformativa di Alperia Smart Services Srl al Cliente in merito al Trattamento dei Dati Personali ai sensi degli articoli 13 e 14 del Regolamento generale sulla protezione"] = "/On"
    fields["DATA_3"] = today

    if d.get("esecuzione_anticipata", False):
        fields["DATA_4"] = today

    fields["DATA_5"] = today
    fields["DATA_6"] = today

    # FIX: filtra sia None che stringhe vuote per non inviare campi vuoti al PDF
    fields = {k: v for k, v in fields.items() if v}

    reader = PdfReader(TEMPLATE_PATH)
    writer = PdfWriter()
    writer.append(reader)

    # FIX: cerca dinamicamente le pagine con campi compilabili invece di usare indici fissi
    pages_with_fields = []
    for i, page in enumerate(writer.pages):
        if "/Annots" in page:
            pages_with_fields.append(i)

    target_pages = pages_with_fields if pages_with_fields else [2, 3]
    for page_idx in target_pages:
        if page_idx < len(writer.pages):
            writer.update_page_form_field_values(writer.pages[page_idx], fields)

    # FIX: setta i radio group direttamente sugli oggetti figlio (pypdf non li gestisce via update_page_form_field_values)
    fill_radio_groups(writer, radio_fields)

    buf = io.BytesIO()
    writer.write(buf)
    buf.seek(0)

    nome_file = f"Contratto_Alperia_{d.get('cognome', '')}_{d.get('nome', '')}.pdf".replace(" ", "_")
    return send_file(buf, mimetype="application/pdf",
                     as_attachment=True, download_name=nome_file)


if __name__ == "__main__":
    port  = int(os.environ.get("PORT", 7860))
    debug = os.environ.get("RENDER") is None
    app.run(debug=debug, host="0.0.0.0", port=port)
