"""kolla_secrets_orphan: OSISM ships a secret upstream no longer expects.

For each supported release R, an OSISM kolla secret is an orphan when it has no
counterpart in upstream kolla-ansible's authoritative secret list:

  - OSISM set  -- top-level keys of the cfg-cookiecutter kolla secrets template
                  for R (environments/kolla/secrets.yml.R)
  - upstream   -- top-level keys of kolla-ansible etc/kolla/passwords.yml at R's
                  resolved ref (the canonical list of every secret it expects)

A key in the OSISM template but absent upstream is a leftover from a removed
service (e.g. outward_rabbitmq_password). Both sides are release-specific, so the
comparison is per release, not unioned.

Mirrors kolla_enablement_orphan in shape (OSISM-vs-upstream reconciliation) but
on the secret key space: keys come from secrets_map.parse_secret_keys (line
regex, not a YAML load, because the template values are jinja/cookiecutter
placeholders). canon normalises hyphen/underscore. OSISM-invented secrets with
no upstream counterpart are kept via the allowlist rather than removed.
"""

from osism_drift import enablement, secrets_map, source
from osism_drift.model import DriftEntry

NAME = "kolla_secrets_orphan"
DESCRIPTION = (
    "Flag OSISM kolla secret vars absent from upstream kolla-ansible "
    "passwords.yml at a supported release (orphaned secrets)."
)
INPUT_FILES = [
    ("cfg_cookiecutter", "environments/kolla/secrets.yml.<release>"),
    ("kolla_ansible", "etc/kolla/passwords.yml (per resolved release ref)"),
]
SUMMARY = (
    "{n} OSISM kolla secrets defined for this release with no counterpart in "
    "upstream kolla-ansible's passwords.yml, so the secret is orphaned (the "
    "service that consumed it was removed upstream):"
)
REMEDIATION = (
    "remove the orphaned secret from the cfg-cookiecutter kolla secrets template "
    "(and regenerate environments), or allowlist it if it is an OSISM-invented "
    "secret with no upstream counterpart."
)

_SECRETS = "{{cookiecutter.project_name}}/environments/kolla/secrets.yml"
_PASSWORDS = "etc/kolla/passwords.yml"


def run(config, allowlist, verbose: bool = False) -> list[DriftEntry]:
    """Return orphan-secret drifts: OSISM ships a secret upstream dropped."""
    drifts = []
    for release in enablement.release_range(config):
        osism_body = source.read_optional(
            "cfg_cookiecutter", f"{_SECRETS}.{release}", config
        )
        if osism_body is None:
            continue  # no OSISM kolla secrets template for this release
        osism_keys = {
            enablement.canon(k) for k in secrets_map.parse_secret_keys(osism_body)
        }

        ref = source.release_to_ref("kolla_ansible", release, config)
        upstream_keys = {
            enablement.canon(k)
            for k in secrets_map.parse_secret_keys(
                source.read_at_ref("kolla_ansible", _PASSWORDS, ref, config)
            )
        }

        for key in sorted(osism_keys - upstream_keys):
            d = DriftEntry(
                plugin=NAME,
                image=key,
                alias=key,
                expected=f"present in upstream passwords.yml at {ref}",
                found=(
                    "absent upstream; orphaned in cfg-cookiecutter "
                    f"environments/kolla/secrets.yml.{release}"
                ),
                expected_src=f"openstack/kolla-ansible {_PASSWORDS} @ {ref}",
                found_src=f"cfg-cookiecutter environments/kolla/secrets.yml.{release}",
            )
            drifts.append(allowlist.apply(d))
    return drifts
