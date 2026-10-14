# Compare the image references kolla-ansible deploys with the tags a kolla
# release build pushed, for check-kolla-images.py.
#
# Every container uses image: "{{ <x>_image_full }}". Each role's
# *_image_full vars are resolved in that role's scope, as Ansible does when
# it runs the role: the role's own defaults, then the OSISM layers (the
# overlay, versions.yml and osism/defaults all/*.yml), which win. The image
# name is the last path segment of the reference, the tag what follows its
# last colon. A reference whose image the build pushed is compared with the
# pushed tag; one whose image the build did not push is not a finding here.

from dataclasses import dataclass

from ansible_templar import Templar, TemplateFailure

ROLE_RANK = 0
OSISM_RANK = 1
IMAGE_FULL = "_image_full"


@dataclass(frozen=True)
class Mismatch:
    image: str
    role: str
    var: str
    resolved: str
    pushed: str
    hops: tuple


@dataclass(frozen=True)
class Unresolvable:
    role: str
    var: str
    cause: str
    image: str | None  # the pushed image the reference names, None if unknown


@dataclass
class Report:
    pushed: dict  # image name -> pushed tag
    consumers: dict  # image name -> [(role, var)]
    mismatches: list
    unresolvable: list

    @property
    def unconsumed(self):
        return sorted(image for image in self.pushed if image not in self.consumers)

    @property
    def failed_images(self):
        failed = {m.image for m in self.mismatches}
        failed |= {u.image for u in self.unresolvable if u.image}
        return sorted(failed)

    @property
    def failed_references(self):
        return len(self.mismatches) + sum(1 for u in self.unresolvable if u.image)

    @property
    def unplaced(self):
        return [u for u in self.unresolvable if u.image is None]

    @property
    def references(self):
        return sum(len(consumers) for consumers in self.consumers.values())

    @property
    def ok_images(self):
        return len(self.consumers) - len(self.failed_images)


def split_reference(reference):
    """(image name, tag) of an image reference, None if it names no tag."""
    path, colon, tag = reference.rpartition(":")
    if not colon or not tag or "/" in tag:
        return None
    return path.rsplit("/", 1)[-1], tag


def osism_layers(overlay, versions, defaults_files):
    """The Templar layers of the OSISM sources, all of the same rank.

    Within osism/defaults the files load in name order, so a later file
    overrides an earlier one; defaults_files is [(file name, values)].
    """
    defaults = {}
    for name, values in sorted(defaults_files, key=lambda entry: entry[0]):
        for key, raw in (values or {}).items():
            defaults[key] = (raw, f"defaults all/{name}")
    return [
        (OSISM_RANK, {key: (raw, "overlay") for key, raw in (overlay or {}).items()}),
        (
            OSISM_RANK,
            {key: (raw, "versions.yml") for key, raw in (versions or {}).items()},
        ),
        (OSISM_RANK, defaults),
    ]


def image_name(templar, var, value=None):
    """The image name of a reference that has no usable tag, None if unknown.

    From the resolved reference if there is one, else from the reference's
    image var (<x>_image for <x>_image_full), which kolla-ansible declares
    for every container.
    """
    if value is None:
        try:
            value, _ = templar.resolve(var[: -len("_full")])
        except TemplateFailure:
            return None
    path = str(value).rsplit("/", 1)[-1]
    return path.split(":", 1)[0] or None


def check(roles, osism, pushed_images):
    """Resolve every role's *_image_full and compare it with the pushed images.

    roles: {role: role defaults}; osism: osism_layers(); pushed_images: the
    image references of the SBOM. A reference that cannot be resolved counts
    against the pushed image it names; one naming an image the build did not
    push is not a finding, like a resolved one; one whose image cannot be
    determined at all counts on its own.
    """
    pushed = {}
    for reference in pushed_images:
        parts = split_reference(reference)
        if parts:
            pushed[parts[0]] = parts[1]
    consumers, mismatches, unresolvable = {}, [], []
    for role in sorted(roles):
        defaults = roles[role] or {}
        templar = Templar(
            [
                (
                    ROLE_RANK,
                    {
                        key: (raw, f"role defaults: {role}")
                        for key, raw in defaults.items()
                    },
                ),
                *osism,
            ]
        )
        for var in sorted(key for key in defaults if key.endswith(IMAGE_FULL)):
            try:
                value, hops = templar.resolve(var)
            except TemplateFailure as e:
                failure, value = str(e), None
            else:
                parts = split_reference(str(value))
                failure = None if parts else f"{str(value)!r} names no tag"
            if failure:
                image = image_name(templar, var, value)
                if image is not None and image not in pushed:
                    continue
                if image is not None:
                    consumers.setdefault(image, []).append((role, var))
                unresolvable.append(Unresolvable(role, var, failure, image))
                continue
            image, tag = parts
            if image not in pushed:
                continue
            consumers.setdefault(image, []).append((role, var))
            if tag.strip() != pushed[image].strip():
                mismatches.append(
                    Mismatch(image, role, var, tag, pushed[image], tuple(hops))
                )
    return Report(pushed, consumers, mismatches, unresolvable)
