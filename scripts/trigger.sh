#!/usr/bin/env bash
set -x

for repository in $(python src/trigger.py | jq -r ".|to_entries[]|select(.value==true)|.key"); do
    if [[ $repository == "docker-images" ]]; then
        echo "trigger update of images"
        bash scripts/update-images.sh
    else
        echo "trigger $repository"
        bash scripts/trigger-travis.sh --branch master osism $repository $TRAVIS_ACCESS_TOKEN 'change in release repository'
    fi
done
