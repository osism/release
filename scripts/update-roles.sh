#!/usr/bin/env bash

if [[ $TRAVIS != "true" ]]; then

    command -v pip >/dev/null 2>&1 || { echo >&2 "pip not installed"; exit 1; }
    command -v virtualenv >/dev/null 2>&1 || { echo >&2 "virtualenv not installed"; exit 1; }

    if [[ ! -e .venv ]]; then
        virtualenv -p python3 .venv
        pip install -r requirements.txt
    fi

    source .venv/bin/activate
fi

python src/roles.py > roles.lst

if [[ -s roles.lst ]]; then
  release="latest"
  echo "$release: update versions of ansible roles" > commit.msg
  echo >> commit.msg
  cat roles.lst >> commit.msg

  git add $release/base.yml
  git commit -F commit.msg
fi

rm -f roles.lst commit.msg
