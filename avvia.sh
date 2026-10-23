#!/bin/bash
set -e
cd "$(dirname "$0")"
echo "Avvio CABONA Contratti Alperia..."
pip install -r requirements.txt -q
# In locale l'app ascolta solo su 127.0.0.1 (http://localhost:7860).
# Per il debugger di Flask: FLASK_DEBUG=1 ./avvia.sh
python app.py
