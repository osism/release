#!/usr/bin/env bash

for repository in $(python src/trigger.py | jq -r ".|to_entries[]|select(.value==true)|.key"); do
    echo "trigger $repository"
    bash scripts/trigger-travis.sh --branch master osism $repository $TRAVIS_ACCESS_TOKEN 'change in release repository'
done
