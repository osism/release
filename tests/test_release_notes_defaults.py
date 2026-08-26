import importlib.util
import pathlib

import requests
import responses

# release-notes.py is hyphenated -> not importable by name; load it by path.
_SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "release-notes.py"
_spec = importlib.util.spec_from_file_location("release_notes", _SRC)
rn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rn)

TREES = "https://api.github.com/repos/osism/defaults/git/trees/v1.2.3?recursive=1"


def _tree(*paths, truncated=False):
    return {
        "truncated": truncated,
        "tree": [{"path": p, "type": "blob"} for p in paths],
    }


@responses.activate
def test_layer_is_sorted_and_filtered():
    responses.add(
        responses.GET,
        TREES,
        json=_tree(
            "all/001-nova.yml",
            "all/001-common.yml",
            "all/099-kolla.yml",  # not in the layer
            "all/001-notes.md",  # not a .yml
            "all/010-2025.1.yml",  # different layer
        ),
        status=200,
    )
    assert rn.list_mirror_layer_files("osism/defaults", "v1.2.3") == [
        "all/001-common.yml",
        "all/001-nova.yml",
    ]


@responses.activate
def test_layer_omits_directories_and_keeps_only_blobs():
    responses.add(
        responses.GET,
        TREES,
        json={
            "truncated": False,
            "tree": [
                {"path": "all/001-nova.yml", "type": "blob"},
                {"path": "all/001-sub", "type": "tree"},
            ],
        },
        status=200,
    )
    assert rn.list_mirror_layer_files("osism/defaults", "v1.2.3") == [
        "all/001-nova.yml"
    ]


@responses.activate
def test_layer_none_on_http_error():
    responses.add(responses.GET, TREES, status=404)
    assert rn.list_mirror_layer_files("osism/defaults", "v1.2.3") is None


@responses.activate
def test_layer_none_on_truncated_tree():
    # A partial layer would render a plausible but incomplete defaults section.
    responses.add(
        responses.GET,
        TREES,
        json=_tree("all/001-nova.yml", truncated=True),
        status=200,
    )
    assert rn.list_mirror_layer_files("osism/defaults", "v1.2.3") is None


@responses.activate
def test_layer_none_on_request_exception():
    # Must be a requests.RequestException subclass: list_mirror_layer_files
    # catches that, and a plain Exception would escape the handler and error
    # the test instead of exercising the degrade path.
    responses.add(responses.GET, TREES, body=requests.ConnectionError("boom"))
    assert rn.list_mirror_layer_files("osism/defaults", "v1.2.3") is None


@responses.activate
def test_section_fetches_the_layer_before_099_and_names_the_layer_in_prose():
    # The real ordering guarantee: all/ merges lexically and 099-kolla.yml wins
    # over the layer, so the section must emit the layer files first. Asserting the
    # constant alone would not prove the fetch order, and would not cover the
    # prose that used to name the deleted monolith.
    #
    # previous/current are base.yml version maps keyed by (section, name); they
    # must differ on one of the three keys the relevance gate checks, or the
    # function returns None before fetching anything.
    previous = {("", "defaults"): "v1.2.2"}
    current = {("", "defaults"): "v1.2.3"}

    responses.add(
        responses.GET,
        TREES,
        json=_tree("all/001-nova.yml", "all/001-common.yml"),
        status=200,
    )
    for path, body in (
        ("all/001-common.yml", "shared: from_common\n"),
        ("all/001-nova.yml", "nova_api_port: 8774\n"),
        ("all/099-kolla.yml", 'enable_cinder: "yes"\n'),
    ):
        responses.add(
            responses.GET,
            f"https://raw.githubusercontent.com/osism/defaults/v1.2.3/{path}",
            body=body,
            status=200,
        )

    section = rn.osism_kolla_defaults_section(previous, current)
    assert section is not None
    headers = [ln for ln in section.splitlines() if ln.startswith("### ")]
    assert headers == [
        "### all/001-common.yml",
        "### all/001-nova.yml",
        "### all/099-kolla.yml",
    ]
    assert "all/001-*.yml mirror layer" in section
    assert "001-kolla-defaults" not in section


@responses.activate
def test_layer_empty_list_when_listing_succeeds_with_no_matches():
    # [] and None must stay distinguishable: this is a truthful empty answer.
    responses.add(responses.GET, TREES, json=_tree("all/099-kolla.yml"), status=200)
    assert rn.list_mirror_layer_files("osism/defaults", "v1.2.3") == []


@responses.activate
def test_section_is_none_when_the_listing_fails_even_if_099_would_fetch():
    # The whole point of failing closed: 099 alone must not produce a section
    # labelled "the effective kolla defaults".
    responses.add(responses.GET, TREES, status=404)
    responses.add(
        responses.GET,
        "https://raw.githubusercontent.com/osism/defaults/v1.2.3/all/099-kolla.yml",
        body='enable_cinder: "yes"\n',
        status=200,
    )
    assert (
        rn.osism_kolla_defaults_section(
            {("", "defaults"): "v1.2.2"}, {("", "defaults"): "v1.2.3"}
        )
        is None
    )


@responses.activate
def test_section_is_none_when_the_listing_is_truncated():
    responses.add(
        responses.GET, TREES, json=_tree("all/001-nova.yml", truncated=True), status=200
    )
    responses.add(
        responses.GET,
        "https://raw.githubusercontent.com/osism/defaults/v1.2.3/all/099-kolla.yml",
        body='enable_cinder: "yes"\n',
        status=200,
    )
    assert (
        rn.osism_kolla_defaults_section(
            {("", "defaults"): "v1.2.2"}, {("", "defaults"): "v1.2.3"}
        )
        is None
    )


@responses.activate
def test_section_is_none_when_one_mirror_file_fails_to_fetch():
    # A section missing one of the files it lists reads as complete.
    responses.add(
        responses.GET,
        TREES,
        json=_tree("all/001-common.yml", "all/001-nova.yml"),
        status=200,
    )
    responses.add(
        responses.GET,
        "https://raw.githubusercontent.com/osism/defaults/v1.2.3/all/001-common.yml",
        body="shared: from_common\n",
        status=200,
    )
    responses.add(
        responses.GET,
        "https://raw.githubusercontent.com/osism/defaults/v1.2.3/all/001-nova.yml",
        status=500,
    )
    assert (
        rn.osism_kolla_defaults_section(
            {("", "defaults"): "v1.2.2"}, {("", "defaults"): "v1.2.3"}
        )
        is None
    )
