"""Test di base. Esegui con:  python -m unittest discover -s tests -v"""
import os, re, sys, unittest, io
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.pop("DATABASE_URL", None)
os.environ.pop("APP_PASSWORD", None)

import app as A
from pypdf import PdfReader

PAYLOAD = {
    "cognome": "Rossi", "nome": "Mario", "cf": "RSSMRA80A01F205X", "residente": True, "tipo_doc": "patente",
    "telefono": "030111", "cellulare": "333111", "email": "a@b.it",
    "indirizzo_res": "Via Roma", "civico_res": "1", "comune_res": "Desenzano", "cap_res": "25015", "prov_res": "BS",
    "fornitura_uguale_residenza": True, "titolo_immobile": "altro", "titolo_altro": "comodato",
    "vuole_luce": True, "tipo_richiesta_luce": ["voltura"], "pod": "IT001E1", "consumo_kwh": "2700",
    "potenza_kw": "3", "tipologia_uso": "residente", "offerta_luce_nome": "X", "offerta_luce_codice": "C1",
    "vuole_gas": True, "tipo_richiesta_gas": ["switch"], "pdr": "123", "consumo_smc": "900",
    "categorie_gas": ["cottura"], "iban": "IT60X0542811101000000123456", "banca": "Banca",
    "debitore_uguale_titolare": False, "titolare_cognome": "Bianchi", "titolare_nome": "Anna",
    "titolare_cf": "bncnna80a41f205z", "esecuzione_anticipata": True,
}


def genera(client, **over):
    p = dict(PAYLOAD, **over)
    r = client.post("/api/compila", json=p)
    return r, (PdfReader(io.BytesIO(r.data)).get_fields() if r.status_code == 200 else None)


def radio_val(fields, name):
    v = fields[name].get("/V")
    return str(v) if v is not None else None


class TestCampiPdf(unittest.TestCase):
    def test_tutti_i_nomi_campo_esistono_nel_template(self):
        """Ogni fields["..."] / radio_fields["..."] usato in app.py deve esistere nel PDF."""
        src = open(A.__file__, encoding="utf-8").read()
        nomi = set(re.findall(r'fields\["([^"]+)"\]', src))
        nomi |= {n for (n, _, _) in A.CONSENSI_MAP.values()}
        nomi.add(A.INFORMATIVA_KEY)
        nomi |= {"Residente si no", "titolo sull'immobile", "Documento d'identità",
                 "Zahlungspflichtiger gleich Kontoinhaber", "Strom Art der Anfrage",
                 "Gas  Art der Anfrage", "Tipologia d'uso"}
        nel_pdf = set(PdfReader(A.TEMPLATE_PATH).get_fields())
        mancanti = sorted(n for n in nomi if n not in nel_pdf)
        self.assertEqual(mancanti, [], f"campi non presenti nel PDF: {mancanti}")


class TestCompila(unittest.TestCase):
    def setUp(self):
        self.c = A.app.test_client()

    def test_campi_titolare_e_indirizzo_debitore_compilati(self):
        r, f = genera(self.c)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(f["Indirizzo Via  Piazza  CAP e COMUNE"]["/V"], "Via Roma 1, 25015 Desenzano")
        self.assertEqual(f["Cognome_2"]["/V"], "Bianchi")
        self.assertEqual(f["Nome_2"]["/V"], "Anna")
        self.assertEqual(f["Codice Fiscale  Partita IVA del Titolare del Conto Corrente"]["/V"], "BNCNNA80A41F205Z")
        self.assertEqual(f["Detenzione ad altro titolo da specificare"]["/V"], "comodato")

    def test_debitore_uguale_titolare_copia_cf(self):
        _, f = genera(self.c, debitore_uguale_titolare=True)
        self.assertEqual(f["Codice Fiscale  Partita IVA del Titolare del Conto Corrente"]["/V"], "RSSMRA80A01F205X")
        self.assertNotIn("/V", f["Cognome_2"])

    def test_pdf_appiattito_senza_campi_ma_con_testo(self):
        r = self.c.post("/api/compila", json=dict(PAYLOAD, appiattito=True))
        self.assertEqual(r.status_code, 200)
        self.assertIn("_STAMPA.pdf", r.headers["Content-Disposition"])
        rd = PdfReader(io.BytesIO(r.data))
        self.assertEqual(len(rd.get_fields() or {}), 0)
        self.assertNotIn("/AcroForm", rd.trailer["/Root"])
        for pg in rd.pages:
            self.assertFalse(pg.get("/Annots"))
        # il contenuto deve essere ancora sulla pagina (come disegno, non come campo)
        self.assertTrue(any("/FlatW0" in str(pg.get("/Resources", {}).get("/XObject", {}))
                            for pg in rd.pages))

    def test_testi_lunghi_entrano_nei_campi(self):
        """Il codice offerta (31 caratteri) deve stare nella larghezza del campo."""
        lungo = "000368ESFML02XX2695001X260401XVE"
        r = self.c.post("/api/compila", json=dict(PAYLOAD, offerta_luce_codice=lungo))
        rd = PdfReader(io.BytesIO(r.data))
        trovato = False
        for pg in rd.pages:
            for ref in pg.get("/Annots", []) or []:
                a = ref.get_object()
                if str(a.get("/T")) == "Codice offerta":
                    trovato = True
                    w = abs(float(a["/Rect"][2]) - float(a["/Rect"][0]))
                    fs = float(re.search(r"([\d.]+)\s+Tf", str(a["/DA"])).group(1))
                    self.assertLessEqual(fs, 9.0)
                    self.assertLessEqual(A.larghezza_testo(lungo, fs), w - 3)
        self.assertTrue(trovato)

    def test_pdf_normale_resta_modificabile(self):
        _, f = genera(self.c)
        self.assertGreater(len(f), 20)

    def test_pod_senza_prefisso_it_e_non_troncato(self):
        for inserito in ("IT001E12345678", "it001e12345678", "IT 001E 12345678", "001E12345678"):
            _, f = genera(self.c, pod=inserito)
            self.assertEqual(f["POD A"]["/V"], "001E12345678", inserito)

    def test_pod_o_pdr_troppo_lunghi_rifiutati(self):
        self.assertEqual(genera(self.c, pod="IT001E1234567890")[0].status_code, 400)
        self.assertEqual(genera(self.c, pdr="123456789012345")[0].status_code, 400)

    def test_consensi_vuoti_di_default(self):
        _, f = genera(self.c)
        for nome, _, _ in A.CONSENSI_MAP.values():
            self.assertIn(radio_val(f, nome), (None, "/Off"), nome)
        self.assertNotEqual(f[A.INFORMATIVA_KEY].get("/V"), "/On")

    def test_consensi_scelti_dal_form(self):
        _, f = genera(self.c, consensi={"marketing": "no", "profilazione": "si", "terzi": "no"},
                      informativa_letta=True)
        self.assertEqual(radio_val(f, "Verarbeitung der Daten"), "/Auswahl4")
        self.assertEqual(radio_val(f, "Verarbeitung der Daten 2"), "/Auswahl5")
        self.assertEqual(radio_val(f, "Verarbeitung der Daten 3"), "/Auswahl7")
        self.assertEqual(f[A.INFORMATIVA_KEY]["/V"], "/On")

    def test_tipo_richiesta(self):
        _, f = genera(self.c)
        self.assertEqual(radio_val(f, "Strom Art der Anfrage"), "/Auswahl6")  # voltura
        self.assertEqual(radio_val(f, "Gas  Art der Anfrage"), "/Auswahl4")   # switch

    def test_piu_tipi_richiesta_rifiutati(self):
        r, _ = genera(self.c, tipo_richiesta_luce=["switch", "voltura"])
        self.assertEqual(r.status_code, 400)

    def test_payload_non_validi_danno_400_non_500(self):
        self.assertEqual(self.c.post("/api/compila", data="null", content_type="application/json").status_code, 400)
        self.assertEqual(self.c.post("/api/compila", data="x").status_code, 400)
        self.assertEqual(genera(self.c, cf=None)[0].status_code, 400)
        self.assertEqual(genera(self.c, cf="RSSMRA80A01F205Y")[0].status_code, 400)   # checksum errato
        self.assertEqual(genera(self.c, iban="IT60X0542811101000000123457")[0].status_code, 400)
        self.assertEqual(genera(self.c, vuole_luce=False, vuole_gas=False)[0].status_code, 400)
        self.assertEqual(genera(self.c, cognome="")[0].status_code, 400)

    def test_campi_null_non_crashano(self):
        r, _ = genera(self.c, telefono=None, email=None, pod=None, banca=None)
        self.assertEqual(r.status_code, 200)

    def test_data_in_fuso_italiano(self):
        _, f = genera(self.c)
        self.assertRegex(f["DATA"]["/V"], r"^\d{2}/\d{2}/\d{4}$")

    def test_nome_file_sicuro(self):
        self.assertEqual(A.nome_file_sicuro("D'Angelo", "José/../x"), "Contratto_Alperia_D_Angelo_Jose_x.pdf")


class TestOfferte(unittest.TestCase):
    def setUp(self):
        self.c = A.app.test_client()
        A._offerte_runtime = A.load_default_offerte()

    def test_default_da_offerte_json_con_codici_reali(self):
        o = self.c.get("/api/offerte").get_json()
        self.assertTrue(o["luce"][0]["codice"].startswith("000368ESFML"))
        self.assertTrue(o["gas"][0]["codice"].startswith("000368GSFML"))

    def test_endpoint_pericolosi_rimossi(self):
        self.assertEqual(self.c.post("/api/salva_api_key", json={"api_key": "sk-ant-x"}).status_code, 404)
        self.assertEqual(self.c.post("/api/leggi_bolletta", json={}).status_code, 404)
        self.assertEqual(self.c.get("/api/check_api_key").status_code, 404)
        self.assertEqual(self.c.post("/api/offerte", json=[1, 2]).status_code, 405)
        self.assertFalse(os.path.exists(os.path.join(A.BASE_DIR, "config.json")))

    def test_elimina_con_lista_cambiata_dà_409(self):
        r = self.c.post("/api/offerte/elimina",
                        json={"tipo": "luce", "index": 0, "atteso_nome": "Altra", "atteso_codice": "ZZZ"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(len(self.c.get("/api/offerte").get_json()["luce"]), 1)

    def test_aggiungi_modifica_elimina(self):
        r = self.c.post("/api/offerte/aggiungi", json={"tipo": "luce", "nome": "N", "codice": "C"})
        self.assertEqual(r.status_code, 200)
        r = self.c.post("/api/offerte/modifica", json={"tipo": "luce", "index": 1, "nome": "N2", "codice": "C2",
                                                       "atteso_nome": "N", "atteso_codice": "C"})
        self.assertEqual(r.get_json()["offerte"]["luce"][1]["nome"], "N2")
        r = self.c.post("/api/offerte/elimina", json={"tipo": "luce", "index": 1,
                                                      "atteso_nome": "N2", "atteso_codice": "C2"})
        self.assertEqual(len(r.get_json()["offerte"]["luce"]), 1)

    def test_input_non_validi(self):
        self.assertEqual(self.c.post("/api/offerte/aggiungi", data="null",
                                     content_type="application/json").status_code, 400)
        self.assertEqual(self.c.post("/api/offerte/aggiungi", json={"tipo": "luce", "nome": None,
                                                                    "codice": "x"}).status_code, 400)
        self.assertEqual(self.c.post("/api/offerte/elimina", json={"tipo": "luce", "index": True}).status_code, 400)


class TestAuth(unittest.TestCase):
    def test_password(self):
        old = A.APP_PASSWORD
        A.APP_PASSWORD = "segreta"
        try:
            c = A.app.test_client()
            self.assertEqual(c.get("/api/offerte").status_code, 401)
            import base64
            ok = {"Authorization": "Basic " + base64.b64encode(b"u:segreta").decode()}
            ko = {"Authorization": "Basic " + base64.b64encode(b"u:sbagliata").decode()}
            self.assertEqual(c.get("/api/offerte", headers=ok).status_code, 200)
            self.assertEqual(c.get("/api/offerte", headers=ko).status_code, 401)
        finally:
            A.APP_PASSWORD = old


class TestValidatori(unittest.TestCase):
    def test_cf(self):
        self.assertTrue(A.cf_valido("RSSMRA80A01F205X"))
        self.assertFalse(A.cf_valido("RSSMRA80A01F205Y"))
        self.assertFalse(A.cf_valido("troppo corto"))
        self.assertFalse(A.cf_valido(None))

    def test_iban(self):
        self.assertTrue(A.iban_valido("IT60 X054 2811 1010 0000 0123 456"))
        self.assertFalse(A.iban_valido("IT60X0542811101000000123457"))
        self.assertFalse(A.iban_valido(""))


if __name__ == "__main__":
    unittest.main()
