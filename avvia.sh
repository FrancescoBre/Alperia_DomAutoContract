#!/bin/bash
echo "Avvio CABONA Contratti Alperia..."
pip install flask pypdf psycopg2-binary gunicorn -q
python app.py
