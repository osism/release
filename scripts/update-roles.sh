#!/usr/bin/env bash

python src/roles.py > roles.lst

if [[ -s roles.lst ]]; then
  release=$(basename $(readlink latest))
  echo "$release: update versions of ansible roles" > commit.msg
  echo >> commit.msg
  cat roles.lst >> commit.msg

  git add $release/base.yml
  git commit -F commit.msg
  git push
fi
rm -f roles.lst commit.msg
