#!/bin/sh
# Both plugins must see this file: it is the only consumer of the shellcons
# alias and of docker_registry_shellcons, and it is not .yml/.yaml/.j2.
exec docker run --rm "{{ shellcons_image }}" --registry "{{ docker_registry_shellcons }}"
