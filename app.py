from flask import Flask, request, send_file, jsonify, Response
from pypdf import PdfReader, PdfWriter
import io, os, json, copy, re, hmac, unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo

app = Flask(__name__)
# Limite alle richieste: il form invia pochi KB di JSON
app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_PATH = os.path.join(BASE_DIR, "template_alperia.pdf")
OFFERTE_JSON_PATH = os.path.join(BASE_DIR, "offerte.json")

DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Password di accesso (HTTP Basic, username libero). Se non impostata l'app è
# aperta: va bene SOLO in locale. Su Render imposta APP_PASSWORD.
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")

# Opzionali: se impostati compilano "Agenzia" / "Agente" nel PDF
AGENZIA = os.environ.get("AGENZIA", "")
AGENTE = os.environ.get("AGENTE", "")

FALLBACK_OFFERTE = {
    "luce": [{"nome": "Alperia Direct Energy", "codice": "000368ESFML02XX2695001X260401XVE"}],
    "gas":  [{"nome": "Alperia Gas Smile Start", "codice": "000368GSFML01XX1895452X260511XVE"}],
}


# ─── Offerte: validazione e default ───────────────────────────────

def clean_offerte(data):
    """Valida la struttura {"luce": [{nome, codice}], "gas": [...]}.
    Solleva ValueError se non è valida."""
    if not isinstance(data, dict):
        raise ValueError("struttura offerte non valida")
    result = {"luce": [], "gas": []}
    for tipo in result:
        lista = data.get(tipo, [])
        if not isinstance(lista, list):
            raise ValueError(f"'{tipo}' deve essere una lista")
        for o in lista:
            if not isinstance(o, dict):
                raise ValueError("offerta non valida")
            nome = str(o.get("nome") or "").strip()
            codice = str(o.get("codice") or "").strip()
            if not nome or not codice:
                raise ValueError("nome e codice obbligatori")
            result[tipo].append({"nome": nome, "codice": codice})
    return result


def load_default_offerte():
    """I default arrivano da offerte.json (codici reali); se manca/è rotto, fallback."""
    try:
        with open(OFFERTE_JSON_PATH, encoding="utf-8") as f:
            return clean_offerte(json.load(f))
    except Exception as e:
        print(f"offerte.json non utilizzabile ({e}), uso i default interni")
        return copy.deepcopy(FALLBACK_OFFERTE)


DEFAULT_OFFERTE = load_default_offerte()


# ─── Database ─────────────────────────────────────────────────────

def get_db():
    import psycopg2
    url = DATABASE_URL
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return psycopg2.connect(url)


def init_db():
    conn = None
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
    except Exception as e:
        print(f"DB init warning: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()


# Usato solo senza DATABASE_URL. Attenzione: è per-processo (con più worker
# gunicorn ogni worker ha il suo) e si perde al riavvio: in produzione usa il DB.
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
    """Salva le offerte. Solleva RuntimeError se il DB è configurato ma il
    salvataggio fallisce (prima l'errore veniva ignorato e l'app rispondeva ok)."""
    global _offerte_runtime
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
            conn.commit()  # DELETE + INSERT sono nella stessa transazione: o tutto o niente
            cur.close()
            return
        except Exception as e:
            print(f"DB save error: {e}")
            if conn:
                conn.rollback()
            raise RuntimeError("salvataggio offerte non riuscito") from e
        finally:
            if conn:
                conn.close()
    _offerte_runtime = copy.deepcopy(data)


if DATABASE_URL:
    init_db()


# ─── Sicurezza: autenticazione e errori ───────────────────────────

@app.before_request
def check_auth():
    if not APP_PASSWORD:
        return None
    auth = request.authorization
    given = (auth.password or "") if auth else ""
    if auth and hmac.compare_digest(given.encode(), APP_PASSWORD.encode()):
        return None
    return Response("Autenticazione richiesta", 401,
                    {"WWW-Authenticate": 'Basic realm="CABONA Contratti"'})


@app.errorhandler(413)
def too_large(_e):
    return jsonify({"error": "richiesta troppo grande"}), 413


def get_json_dict():
    """Body JSON come dict, oppure None se assente/non valido."""
    d = request.get_json(silent=True)
    return d if isinstance(d, dict) else None


def txt(d, key, default=""):
    """Legge una stringa dal payload: None/assente -> default, sempre strip()."""
    v = d.get(key)
    return default if v is None else str(v).strip()


# ─── Validazione dati ─────────────────────────────────────────────

_CF_ODD = {
    **dict(zip("0123456789", [1, 0, 5, 7, 9, 13, 15, 17, 19, 21])),
    **dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ",
               [1, 0, 5, 7, 9, 13, 15, 17, 19, 21, 2, 4, 18, 20, 11, 3, 6, 8,
                12, 14, 16, 10, 22, 25, 24, 23])),
}


def cf_valido(cf):
    cf = (cf or "").upper()
    if not re.fullmatch(r"[A-Z0-9]{16}", cf):
        return False
    tot = 0
    for i, c in enumerate(cf[:15]):
        if i % 2 == 0:
            tot += _CF_ODD[c]
        else:
            tot += int(c) if c.isdigit() else ord(c) - 65
    return cf[15] == chr(65 + tot % 26)


def iban_valido(iban):
    iban = (iban or "").upper().replace(" ", "")
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", iban):
        return False
    spostato = iban[4:] + iban[:4]
    numero = "".join(str(int(c, 36)) for c in spostato)
    return int(numero) % 97 == 1


# ─── Route ────────────────────────────────────────────────────────

@app.route("/")
def index():
    return app.send_static_file("index.html")


@app.route("/api/offerte", methods=["GET"])
def get_offerte():
    return jsonify(load_offerte())


def _salva_o_errore(offerte):
    try:
        save_offerte(offerte)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500
    return None


def _atteso_ok(d, corrente):
    """Se il client indica quale offerta credeva di modificare/eliminare, deve
    coincidere con quella all'indice richiesto (evita di toccare la riga
    sbagliata quando due schede/utenti hanno liste diverse)."""
    if "atteso_nome" in d or "atteso_codice" in d:
        return (corrente["nome"] == txt(d, "atteso_nome")
                and corrente["codice"] == txt(d, "atteso_codice"))
    return True


@app.route("/api/offerte/aggiungi", methods=["POST"])
def aggiungi_offerta():
    d = get_json_dict()
    if d is None:
        return jsonify({"error": "JSON non valido"}), 400
    offerte = load_offerte()
    tipo = txt(d, "tipo", "luce")
    if tipo not in offerte:
        return jsonify({"error": "tipo non valido"}), 400
    nome = txt(d, "nome")
    codice = txt(d, "codice")
    if not nome or not codice:
        return jsonify({"error": "nome e codice obbligatori"}), 400
    offerte[tipo].append({"nome": nome, "codice": codice})
    err = _salva_o_errore(offerte)
    if err:
        return err
    return jsonify({"ok": True, "offerte": offerte})


@app.route("/api/offerte/elimina", methods=["POST"])
def elimina_offerta():
    d = get_json_dict()
    if d is None:
        return jsonify({"error": "JSON non valido"}), 400
    tipo = d.get("tipo")
    idx = d.get("index")
    if not tipo or not isinstance(idx, int) or isinstance(idx, bool):
        return jsonify({"error": "parametri mancanti o non validi"}), 400
    offerte = load_offerte()
    if tipo not in offerte:
        return jsonify({"error": "tipo non valido"}), 400
    if not (0 <= idx < len(offerte[tipo])):
        return jsonify({"error": "offerta non trovata", "offerte": offerte}), 409
    if not _atteso_ok(d, offerte[tipo][idx]):
        return jsonify({"error": "la lista è cambiata, ricarica le offerte"}), 409
    offerte[tipo].pop(idx)
    err = _salva_o_errore(offerte)
    if err:
        return err
    return jsonify({"ok": True, "offerte": offerte})


@app.route("/api/offerte/modifica", methods=["POST"])
def modifica_offerta():
    d = get_json_dict()
    if d is None:
        return jsonify({"error": "JSON non valido"}), 400
    tipo = d.get("tipo")
    idx = d.get("index")
    nome = txt(d, "nome")
    codice = txt(d, "codice")
    if not tipo or not isinstance(idx, int) or isinstance(idx, bool):
        return jsonify({"error": "parametri mancanti o non validi"}), 400
    offerte = load_offerte()
    if tipo not in offerte:
        return jsonify({"error": "tipo non valido"}), 400
    if not nome or not codice:
        return jsonify({"error": "nome e codice obbligatori"}), 400
    if not (0 <= idx < len(offerte[tipo])):
        return jsonify({"error": "offerta non trovata", "offerte": offerte}), 409
    if not _atteso_ok(d, offerte[tipo][idx]):
        return jsonify({"error": "la lista è cambiata, ricarica le offerte"}), 409
    offerte[tipo][idx] = {"nome": nome, "codice": codice}
    err = _salva_o_errore(offerte)
    if err:
        return err
    return jsonify({"ok": True, "offerte": offerte})


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




# Larghezze Helvetica (per 1000 unità) dei caratteri ASCII 32-126, per stimare l'ingombro del testo
_HELV_W = [278,278,355,556,556,889,667,191,333,333,389,584,278,333,278,278,
           556,556,556,556,556,556,556,556,556,556,278,278,584,584,584,556,
           1015,667,667,722,722,667,611,778,722,278,500,667,556,833,722,778,
           667,778,722,667,611,722,667,944,667,667,611,278,278,278,469,556,
           333,556,556,500,556,556,278,556,556,222,222,500,222,833,556,556,
           556,556,333,500,278,556,500,722,500,500,500,334,260,334,584]


def larghezza_testo(testo, fs):
    return sum((_HELV_W[ord(c) - 32] if 32 <= ord(c) <= 126 else 600) for c in testo) * fs / 1000.0


def adatta_font_campi(writer, fields):
    """Imposta nel PDF una dimensione carattere esplicita per ogni campo di testo
    compilato: abbastanza piccola da stare nella larghezza del campo (es. codici
    offerta lunghi) e mai più grande di 9pt. Prima il PDF usava 'auto' e il testo
    veniva troppo grande e tagliato."""
    import re as _re
    from pypdf.generic import NameObject, TextStringObject

    def ered(a, k):
        while a is not None:
            if k in a:
                return a[k]
            par = a.get("/Parent")
            a = par.get_object() if par is not None else None
        return None

    acro = writer._root_object.get("/AcroForm")
    da_globale = str(acro.get_object().get("/DA", "")) if acro is not None else ""
    for page in writer.pages:
        for ref in page.get("/Annots", []) or []:
            a = ref.get_object()
            if a.get("/Subtype") != "/Widget" or ered(a, "/FT") != "/Tx":
                continue
            ff = int(ered(a, "/Ff") or 0)
            if ff & (1 << 24) or ff & (1 << 12):      # caselle (comb) e multilinea: gestiti a parte
                continue
            nome = a.get("/T")
            if nome is None and a.get("/Parent") is not None:
                nome = a["/Parent"].get_object().get("/T")
            valore = fields.get(str(nome)) if nome is not None else None
            if not valore:
                continue
            rect = [float(x) for x in a["/Rect"]]
            w, h = abs(rect[2] - rect[0]), abs(rect[3] - rect[1])
            da = str(ered(a, "/DA") or da_globale or "/Helv 0 Tf 0 g")
            m = _re.search(r"(/\S+)\s+([\d.]+)\s+Tf", da)
            font = m.group(1) if m else "/Helv"
            resto = _re.sub(r"/\S+\s+[\d.]+\s+Tf", "", da).strip() or "0 g"
            fs = min(9.0, h * 0.62)
            ingombro = larghezza_testo(str(valore), 1.0)
            if ingombro > 0:
                fs = min(fs, (w - 4) / ingombro)
            fs = max(fs, 5.0)
            a[NameObject("/DA")] = TextStringObject(f"{font} {fs:.1f} Tf {resto}")


def flatten_pdf(writer):
    """Appiattisce il modulo: disegna l'aspetto di ogni campo direttamente nella
    pagina e poi elimina i campi, così il PDF non è più modificabile e si stampa
    identico a come si vede."""
    from pypdf.generic import (ArrayObject, DictionaryObject, NameObject,
                               StreamObject, FloatObject)

    def stream_aspetto(annot):
        ap = annot.get("/AP")
        if not ap or "/N" not in ap:
            return None
        n = ap["/N"].get_object()
        if isinstance(n, StreamObject):
            return ap.raw_get("/N")
        stato = annot.get("/AS")
        if stato is not None and stato in n:
            return n.raw_get(stato)
        return None

    def ereditato(a, chiave):
        while a is not None:
            if chiave in a:
                return a[chiave]
            par = a.get("/Parent")
            a = par.get_object() if par is not None else None
        return None

    def aspetto_a_caselle(a, w, h):
        """Campi 'comb' (CF, POD, PDR, IBAN...): un carattere per casella, in Courier.
        Le caselle sono già disegnate nel modulo: qui si scrivono solo i caratteri."""
        if ereditato(a, "/FT") != "/Tx":
            return None
        if not (int(ereditato(a, "/Ff") or 0) & (1 << 24)):
            return None
        maxlen = int(ereditato(a, "/MaxLen") or 0)
        valore = str(ereditato(a, "/V") or "")
        if maxlen <= 0 or not valore:
            return "vuoto"
        cw = w / maxlen
        fs = min(h * 0.72, cw * 1.4)
        y = (h - fs * 0.62) / 2
        ops = ["BT", f"/FC {fs:.2f} Tf", "0 g"]
        for i, ch in enumerate(valore[:maxlen]):
            if ch in "()\\":
                ch = "\\" + ch
            x = i * cw + (cw - 0.6 * fs) / 2
            ops.append(f"1 0 0 1 {x:.2f} {y:.2f} Tm ({ch}) Tj")
        ops.append("ET")
        font = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Courier"),
        })
        st = StreamObject()
        st.update({
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Form"),
            NameObject("/BBox"): ArrayObject([FloatObject(0), FloatObject(0), FloatObject(w), FloatObject(h)]),
            NameObject("/Resources"): DictionaryObject({
                NameObject("/Font"): DictionaryObject({NameObject("/FC"): writer._add_object(font)})
            }),
        })
        st.set_data("\n".join(ops).encode("latin-1", "replace"))
        return writer._add_object(st)

    for page in writer.pages:
        annots = page.get("/Annots")
        if not annots:
            continue
        annots = annots.get_object()
        res = page.get("/Resources")
        res = res.get_object() if res is not None else None
        if res is None:
            res = DictionaryObject()
            page[NameObject("/Resources")] = res
        xobj = res.get("/XObject")
        xobj = xobj.get_object() if xobj is not None else None
        if xobj is None:
            xobj = DictionaryObject()
            res[NameObject("/XObject")] = xobj

        ops = []
        restanti = ArrayObject()
        for i, ref in enumerate(annots):
            a = ref.get_object()
            if a.get("/Subtype") != "/Widget":
                restanti.append(ref)
                continue
            flags = int(a.get("/F", 0))
            if flags & 2 or flags & 32:      # nascosto / non visibile
                continue
            rect = [float(x) for x in a["/Rect"]]
            x0, x1 = sorted((rect[0], rect[2]))
            y0, y1 = sorted((rect[1], rect[3]))
            ap_ref = aspetto_a_caselle(a, x1 - x0, y1 - y0)
            if ap_ref == "vuoto":
                continue
            if ap_ref is None:
                ap_ref = stream_aspetto(a)
            if ap_ref is None:
                continue
            ap_obj = ap_ref.get_object()
            bbox = [float(x) for x in ap_obj.get("/BBox", [0, 0, x1 - x0, y1 - y0])]
            bw, bh = (bbox[2] - bbox[0]) or 1.0, (bbox[3] - bbox[1]) or 1.0
            sx, sy = (x1 - x0) / bw, (y1 - y0) / bh
            tx, ty = x0 - bbox[0] * sx, y0 - bbox[1] * sy
            nome = f"/FlatW{i}"
            xobj[NameObject(nome)] = ap_ref
            ops.append(f"q {sx:.5f} 0 0 {sy:.5f} {tx:.3f} {ty:.3f} cm {nome} Do Q")

        if ops:
            def nuovo(data):
                st = StreamObject()
                st.set_data(data)
                return writer._add_object(st)
            cont = page.raw_get("/Contents") if "/Contents" in page else None
            esist = []
            if cont is not None:
                obj = cont.get_object()
                esist = list(obj) if isinstance(obj, ArrayObject) else [cont]
            page[NameObject("/Contents")] = ArrayObject(
                [nuovo(b"q")] + esist + [nuovo(b"Q"), nuovo("\n".join(ops).encode())]
            )
        if restanti:
            page[NameObject("/Annots")] = restanti
        else:
            del page["/Annots"]

    root = writer._root_object
    if "/AcroForm" in root:
        del root["/AcroForm"]


TIPO_MAP = {"switch": "/Auswahl4", "subentro": "/Auswahl5", "voltura": "/Auswahl6", "nuova": "/Auswahl7"}
TIPOLOGIA_USO_MAP = {"residente": "/Auswahl1", "non_residente": "/Auswahl2", "altri": "/Auswahl3"}
TITOLO_MAP = {"proprieta": "/proprietà/usufrutto", "locazione": "/locazione/affitto", "altro": "/detenzione altro titolo"}
DOC_MAP = {"ci": "/carta identità", "patente": "/patente", "passaporto": "/passaporto"}

# Consensi facoltativi (pagina 4): gruppo radio -> (valore SÌ, valore NO).
# 1) marketing, 2) profilazione, 3) comunicazione a terzi.
CONSENSI_MAP = {
    "marketing":    ("Verarbeitung der Daten",   "/Auswahl3", "/Auswahl4"),
    "profilazione": ("Verarbeitung der Daten 2", "/Auswahl5", "/Auswahl6"),
    "terzi":        ("Verarbeitung der Daten 3", "/Auswahl8", "/Auswahl7"),
}

INFORMATIVA_KEY = (
    "dichiara di aver preso visione dellInformativa di Alperia Smart Services Srl al Cliente "
    "in merito al Trattamento dei Dati Personali ai sensi degli articoli 13 e 14 del Regolamento "
    "generale sulla protezione"
)


def get_tipo(val):
    """Tipo richiesta per il radio del PDF (un solo valore ammesso)."""
    if isinstance(val, str):
        val = [val]
    if not isinstance(val, list):
        val = []
    val = [v for v in val if v]
    if len(val) > 1:
        raise ValueError("seleziona un solo tipo di richiesta per ogni fornitura")
    if not val:
        return "/Auswahl4"
    return TIPO_MAP.get(val[0], "/Auswahl4")


def nome_file_sicuro(cognome, nome, suffisso=""):
    base = f"Contratto_Alperia_{cognome}_{nome}{suffisso}"
    base = unicodedata.normalize("NFKD", base).encode("ascii", "ignore").decode()
    base = re.sub(r"[^A-Za-z0-9_-]+", "_", base).strip("_")
    return (base or "Contratto_Alperia") + ".pdf"


@app.route("/api/compila", methods=["POST"])
def compila():
    d = get_json_dict()
    if d is None:
        return jsonify({"error": "JSON non valido"}), 400

    # Data in fuso italiano (il server, es. Render, gira in UTC)
    today = datetime.now(ZoneInfo("Europe/Rome")).strftime("%d/%m/%Y")

    cognome = txt(d, "cognome")
    nome = txt(d, "nome")
    cf = txt(d, "cf").upper()
    iban = txt(d, "iban").upper().replace(" ", "")
    vuole_luce = bool(d.get("vuole_luce", False))
    vuole_gas = bool(d.get("vuole_gas", False))

    # Validazione lato server (prima c'era solo nel browser)
    if not cognome or not nome:
        return jsonify({"error": "cognome e nome sono obbligatori"}), 400
    if not cf_valido(cf):
        return jsonify({"error": "codice fiscale non valido"}), 400
    if not (vuole_luce or vuole_gas):
        return jsonify({"error": "seleziona almeno una fornitura (luce o gas)"}), 400
    if iban and not iban_valido(iban):
        return jsonify({"error": "IBAN non valido"}), 400

    # Nel modulo "IT" è già stampato nelle prime due caselle del POD: il campo
    # contiene solo i 12 caratteri successivi. Si toglie quindi il prefisso "IT".
    pod = txt(d, "pod").upper().replace(" ", "")
    if pod.startswith("IT"):
        pod = pod[2:]
    pdr = txt(d, "pdr").replace(" ", "")
    if vuole_luce and len(pod) > 12:
        return jsonify({"error": "POD non valido: dopo 'IT' devono esserci al massimo 12 caratteri"}), 400
    if vuole_gas and len(pdr) > 14:
        return jsonify({"error": "PDR non valido: al massimo 14 cifre"}), 400

    fields = {}

    fields["Cognome"] = cognome
    fields["Nome"] = nome
    fields["CF"] = cf
    fields["Indirizzo di residenza"] = txt(d, "indirizzo_res")
    fields["N"] = txt(d, "civico_res")
    fields["Comune"] = txt(d, "comune_res")
    fields["CAP"] = txt(d, "cap_res")
    fields["Prov"] = txt(d, "prov_res")
    fields["Telefono"] = txt(d, "telefono")
    fields["Cellulare"] = txt(d, "cellulare")
    fields["Email"] = txt(d, "email")
    fields["Email per invio bolletta e altre comunicazioni contrattualmente previste"] = \
        txt(d, "email_bolletta") or txt(d, "email")

    if not d.get("fornitura_uguale_residenza", True):
        fields["Indirizzo"] = txt(d, "indirizzo_forn")
        fields["N_2"] = txt(d, "civico_forn")
        fields["Comune_2"] = txt(d, "comune_forn")
        fields["CAP_2"] = txt(d, "cap_forn")
        fields["Prov_2"] = txt(d, "prov_forn")

    fields["DATA"] = today

    if AGENZIA:
        fields["Agenzia"] = AGENZIA
    if AGENTE:
        fields["Agente"] = AGENTE

    # Radio group: vanno settati con fill_radio_groups, non con update_page_form_field_values
    titolo = txt(d, "titolo_immobile", "proprieta")
    radio_fields = {
        "Residente si no": "/Auswahl1" if d.get("residente", True) else "/Auswahl2",
        "titolo sull'immobile": TITOLO_MAP.get(titolo, "/proprietà/usufrutto"),
        "Documento d'identità": DOC_MAP.get(txt(d, "tipo_doc", "ci"), "/carta identità"),
        "Zahlungspflichtiger gleich Kontoinhaber":
            "/Auswahl1" if d.get("debitore_uguale_titolare", True) else "/Auswahl2",
    }
    if titolo == "altro" and txt(d, "titolo_altro"):
        fields["Detenzione ad altro titolo da specificare"] = txt(d, "titolo_altro")

    # Consensi privacy: scelti dal cliente nel form. Se non indicati restano
    # VUOTI nel PDF (prima erano preimpostati: marketing SÌ, profilazione NO, terzi NO).
    consensi = d.get("consensi") if isinstance(d.get("consensi"), dict) else {}
    for chiave, (gruppo, val_si, val_no) in CONSENSI_MAP.items():
        scelta = str(consensi.get(chiave) or "").strip().lower()
        if scelta == "si":
            radio_fields[gruppo] = val_si
        elif scelta == "no":
            radio_fields[gruppo] = val_no

    try:
        if vuole_luce:
            fields["EE sì"] = "/On"
            fields["POD A"] = pod
            fields["Fornitore Uscente"] = txt(d, "fornitore_uscente_luce")
            fields["Consumo annuo kWh"] = txt(d, "consumo_kwh")
            fields["Potenza impegnata kW_2"] = txt(d, "potenza_kw")
            fields["Nome Offerta sottoscritta"] = txt(d, "offerta_luce_nome")
            fields["Codice offerta"] = txt(d, "offerta_luce_codice")
            if txt(d, "data_attivazione_luce"):
                fields["Data Attivazione"] = txt(d, "data_attivazione_luce")
            radio_fields["Strom Art der Anfrage"] = get_tipo(d.get("tipo_richiesta_luce", "switch"))
            radio_fields["Tipologia d'uso"] = TIPOLOGIA_USO_MAP.get(
                txt(d, "tipologia_uso", "residente"), "/Auswahl1")

        if vuole_gas:
            fields["Gas sì"] = "/On"
            fields["PDR"] = pdr
            fields["Fornitore Uscente_4"] = txt(d, "fornitore_uscente_gas")
            fields["Consumo annuo smc"] = txt(d, "consumo_smc")
            fields["REMI"] = txt(d, "remi")
            fields["Nome Offerta sottoscritta_2"] = txt(d, "offerta_gas_nome")
            fields["Codice offerta_2"] = txt(d, "offerta_gas_codice")
            if txt(d, "data_attivazione_gas"):
                fields["Data Attivazione_2"] = txt(d, "data_attivazione_gas")
            categorie = d.get("categorie_gas")
            categorie = categorie if isinstance(categorie, list) else []
            if "riscaldamento" in categorie:
                fields["Kontrollkästchen7"] = "/Ja"
            if "cottura" in categorie:
                fields["Kontrollkästchen8"] = "/Ja"
            if "acqua_calda" in categorie:
                fields["Kontrollkästchen9"] = "/Ja"
            # Il nome del campo gas nel PDF ha DUE spazi
            radio_fields["Gas  Art der Anfrage"] = get_tipo(d.get("tipo_richiesta_gas", "switch"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    if iban:
        addr = (
            f"{txt(d, 'indirizzo_res')} {txt(d, 'civico_res')}, "
            f"{txt(d, 'cap_res')} {txt(d, 'comune_res')}"
        ).strip(", ")
        fields["IBAN"] = iban
        fields["Presso la Banca"] = txt(d, "banca")
        fields["Cognome Nome del Debitore"] = f"{cognome} {nome}".strip()
        # FIX: i nomi reali nel PDF hanno doppi spazi
        fields["Indirizzo Via  Piazza  CAP e COMUNE"] = addr
        fields["DATA_2"] = today
        if not d.get("debitore_uguale_titolare", True):
            # FIX: i campi reali del titolare sono Cognome_2 / Nome_2
            fields["Cognome_2"] = txt(d, "titolare_cognome")
            fields["Nome_2"] = txt(d, "titolare_nome")
            fields["Codice Fiscale  Partita IVA del Titolare del Conto Corrente"] = \
                txt(d, "titolare_cf").upper()
        else:
            # Debitore = titolare: il CF del titolare è quello del cliente
            fields["Codice Fiscale  Partita IVA del Titolare del Conto Corrente"] = cf

    # Presa visione informativa: solo se confermata nel form
    if d.get("informativa_letta"):
        fields[INFORMATIVA_KEY] = "/On"
    fields["DATA_3"] = today

    if d.get("esecuzione_anticipata", False):
        fields["DATA_4"] = today

    fields["DATA_5"] = today
    fields["DATA_6"] = today

    # Non inviare campi vuoti al PDF
    fields = {k: v for k, v in fields.items() if v}

    reader = PdfReader(TEMPLATE_PATH)
    writer = PdfWriter()
    writer.append(reader)

    # Cerca dinamicamente le pagine con campi compilabili invece di usare indici fissi
    pages_with_fields = [i for i, page in enumerate(writer.pages) if "/Annots" in page]
    target_pages = pages_with_fields if pages_with_fields else [2, 3]
    adatta_font_campi(writer, fields)
    for page_idx in target_pages:
        if page_idx < len(writer.pages):
            writer.update_page_form_field_values(writer.pages[page_idx], fields)

    # pypdf non gestisce i radio group via update_page_form_field_values
    fill_radio_groups(writer, radio_fields)

    appiattito = bool(d.get("appiattito", False))
    if appiattito:
        flatten_pdf(writer)

    buf = io.BytesIO()
    writer.write(buf)
    buf.seek(0)

    return send_file(buf, mimetype="application/pdf",
                     as_attachment=True,
                     download_name=nome_file_sicuro(cognome, nome, "_STAMPA" if appiattito else ""))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    su_render = os.environ.get("RENDER") is not None
    # In locale ascolta solo su 127.0.0.1; su Render serve 0.0.0.0.
    # Il debugger si attiva solo in locale e solo con FLASK_DEBUG=1.
    host = "0.0.0.0" if su_render else os.environ.get("HOST", "127.0.0.1")
    debug = (not su_render) and os.environ.get("FLASK_DEBUG") == "1"
    app.run(debug=debug, host=host, port=port)
