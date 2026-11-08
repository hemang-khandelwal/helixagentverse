#!/bin/sh
set -eu
cd "$(dirname "$0")/genegap"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
if ! .venv/bin/python -c 'import requests, numpy, scipy, matplotlib; from pydantic import computed_field; from google import genai' >/dev/null 2>&1; then
  .venv/bin/python -m pip install -r requirements.txt
fi
exec .venv/bin/python app.py "$@"
