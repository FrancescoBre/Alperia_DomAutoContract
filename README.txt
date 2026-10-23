CABONA - Compilatore Contratti Alperia
======================================

INSTALLAZIONE (locale):
1. Python 3.9+ installato
2. In questa cartella:  pip install -r requirements.txt
3. Avvio:               python app.py     (oppure ./avvia.sh)
4. Browser:             http://localhost:7860
   In locale l'app ascolta solo su 127.0.0.1 (non è raggiungibile dalla rete).

USO:
- Seleziona Luce e/o Gas (un solo "tipo richiesta" per fornitura)
- Compila i dati del cliente: codice fiscale e IBAN sono validati anche dal server
- Consensi privacy: scegli SI/NO oppure lascia "vuoto" e fai compilare il cliente.
  Nulla viene preimpostato al posto del cliente.
- "Genera PDF" scarica il contratto; restano vuoti solo i campi firma

OFFERTE:
- Si gestiscono dalla scheda "Offerte" dell'app.
- I valori iniziali (con DB vuoto) vengono letti da offerte.json.
- Senza DATABASE_URL le modifiche restano solo in memoria e si perdono al riavvio.

VARIABILI D'AMBIENTE:
- APP_PASSWORD   password di accesso (HTTP Basic, username libero). OBBLIGATORIA se l'app è online (es. Render).
- DATABASE_URL   Postgres per salvare le offerte in modo permanente
- AGENZIA, AGENTE  se impostate compilano i campi "Agenzia" / "Agente" del PDF
- PORT, HOST     porta e indirizzo (HOST=0.0.0.0 solo se sai cosa fai)
- FLASK_DEBUG=1  debugger Flask, solo in locale

TEST:
  python -m unittest discover -s tests -v
  (controlla che ogni campo usato da app.py esista nel template PDF e i casi limite)

NOTE:
- Se sostituisci template_alperia.pdf con un nuovo modulo, rilancia i test: i nomi dei
  campi cambiano spesso (doppi spazi compresi) e un campo sbagliato viene saltato in silenzio.

SUPPORTO: CABONA Team
