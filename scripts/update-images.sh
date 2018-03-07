#!/usr/bin/env bash

if [[ $TRAVIS != "true" ]]; then

    command -v pip >/dev/null 2>&1 || { echo >&2 "pip not installed"; exit 1; }
    command -v virtualenv >/dev/null 2>&1 || { echo >&2 "virtualenv not installed"; exit 1; }


    if [[ ! -e .venv ]]; then
        virtualenv .venv
        pip install -r requirements.txt
    fi

    source .venv/bin/activate
fi

python src/images.py
