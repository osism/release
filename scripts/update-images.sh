#!/usr/bin/env bash

command -v pip >/dev/null 2>&1 || { echo >&2 "pip not installed"; exit 1; }
command -v virtualenv >/dev/null 2>&1 || { echo >&2 "virtualenv not installed"; exit 1; }

if [[ ! -e .venv ]]; then
    virtualenv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
else
    source .venv/bin/activate
fi

python src/images.py
