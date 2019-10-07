#!/usr/bin/env bash

sudo add-apt-repository "deb [arch=amd64] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable"
sudo apt update
sudo apt -y install docker-ce
echo '{ "experimental": true }' | sudo tee /etc/docker/daemon.json
sudo service docker restart

echo $TRAVIS_DOCKER_PASSWORD | docker login --username="$TRAVIS_DOCKER_USERNAME" --password-stdin
