"""var_refs: which variable names a file reads."""

from osism_drift import var_refs as vr

# --- reads by name ---------------------------------------------------------


def test_yaml_mapping_key_is_not_a_read_but_its_value_is():
    assert vr.reads("x.yml", 'foo: "{{ bar }}"\n') == {"bar"}


def test_yaml_list_item_key_is_not_a_read():
    assert "foo" not in vr.reads("x.yml", "- foo: 1\n")


def test_quoted_yaml_key_is_not_a_read():
    assert "foo" not in vr.reads("x.yaml", '"foo": 1\n')


def test_self_reference_in_value_is_a_read():
    # Roles pass values through like this; the value reads foo.
    assert "foo" in vr.reads("defaults/main.yml", 'foo: "{{ foo | default(1) }}"\n')


def test_yaml_full_line_comment_is_not_a_read():
    text = "#bifrost_network_address_family: x\n   # mentions foo\n"
    assert vr.reads("globals.yml", text) == set()


def test_yaml_value_mention_is_a_read():
    assert "foo" in vr.reads("tasks/main.yml", "when: foo | bool\n")


def test_non_yaml_counts_every_token_including_keys_and_comments():
    text = "foo: {{ bar }}\n# baz\n"
    assert {"foo", "bar", "baz"} <= vr.reads("templates/x.conf.j2", text)


def test_top_level_keys_ignore_nested_keys_and_comments_and_dedupe():
    text = "a: 1\n  b: 2\n# c: 3\nd:\n  - e\na: 3\n"
    assert vr.top_level_keys(text) == ["a", "d"]


# --- merge_variables calls --------------------------------------------------


def test_merge_call_literal_regex():
    text = "x: \"{{ lookup('community.general.merge_variables', '^foo__.+$', initial_value=foo, groups=g) }}\"\n"
    assert vr.merge_calls(text) == [
        vr.MergeCall(patterns=(("regex", "^foo__.+$"),), resolved=True)
    ]


def test_merge_call_short_name_double_quotes_query_and_several_patterns():
    calls = vr.merge_calls('{{ query("merge_variables", "^a__", "^b__") }}')
    assert calls == [
        vr.MergeCall(patterns=(("regex", "^a__"), ("regex", "^b__")), resolved=True)
    ]


def test_merge_call_literal_pattern_type():
    calls = vr.merge_calls(
        "{{ lookup('community.general.merge_variables', 'foo__', pattern_type='prefix') }}"
    )
    assert calls == [vr.MergeCall(patterns=(("prefix", "foo__"),), resolved=True)]


def test_merge_call_with_a_variable_pattern_is_unresolved():
    [call] = vr.merge_calls(
        "{{ lookup('community.general.merge_variables', my_pattern) }}"
    )
    assert call.resolved is False


def test_merge_call_with_a_concatenated_pattern_is_unresolved():
    [call] = vr.merge_calls("{{ lookup('merge_variables', '^' ~ prefix ~ '__') }}")
    assert call.resolved is False


def test_merge_call_with_a_variable_pattern_type_is_unresolved():
    [call] = vr.merge_calls(
        "{{ lookup('merge_variables', '^a__', pattern_type=ptype) }}"
    )
    assert call.resolved is False


def test_merge_call_without_a_pattern_is_unresolved():
    [call] = vr.merge_calls("{{ lookup('merge_variables') }}")
    assert call.resolved is False


def test_merge_call_with_an_invalid_regex_is_unresolved_and_drops_it():
    [call] = vr.merge_calls("{{ lookup('merge_variables', '^a__(', '^b__') }}")
    assert call.resolved is False
    assert call.patterns == (("regex", "^b__"),)  # never handed to re.search


def test_unterminated_merge_call_is_unresolved():
    [call] = vr.merge_calls("lookup('merge_variables', '^a")
    assert call.resolved is False


def test_prose_mention_is_not_a_call():
    assert vr.merge_calls("use the merge_variables lookup for this") == []


# --- matching, the way the lookup matches ----------------------------------


def test_pattern_matches_like_community_general():
    # plugins/lookup/merge_variables.py _var_matches: regex uses re.search.
    assert vr.pattern_matches(
        "regex", "^k3s_add_labels__.+$", "k3s_add_labels__network"
    )
    assert not vr.pattern_matches(
        "regex", "^k3s_add_labels__.+$", "k3s_add_labels_monitoring"
    )
    assert vr.pattern_matches("regex", "labels__", "x_labels__y")
    assert vr.pattern_matches("prefix", "foo__", "foo__a")
    assert not vr.pattern_matches("prefix", "foo__", "xfoo__a")
    assert vr.pattern_matches("suffix", "__foo", "a__foo")


# --- near miss ---------------------------------------------------------------


def test_near_miss_one_underscore_short():
    assert (
        vr.near_miss("k3s_add_labels_monitoring", "^k3s_add_labels__.+$")
        == "k3s_add_labels__monitoring"
    )


def test_near_miss_only_for_the_standard_shape():
    assert vr.near_miss("foo_bar", "foo") is None
    assert vr.near_miss("foo_bar", "^foo__[a-z]+$") is None


def test_near_miss_not_for_a_match_or_an_unrelated_name():
    assert vr.near_miss("k3s_add_labels__network", "^k3s_add_labels__.+$") is None
    assert vr.near_miss("other_name", "^k3s_add_labels__.+$") is None
    assert vr.near_miss("k3s_add_labels_", "^k3s_add_labels__.+$") is None


def test_escaped_quote_call_is_found_and_unresolved():
    text = r'x: "{{ lookup(\"community.general.merge_variables\", \"^a__.+$\") }}"'
    calls = vr.merge_calls(text)
    assert len(calls) == 1
    assert calls[0].resolved is False
    assert calls[0].patterns == ()


def test_pattern_with_backslash_is_unresolved():
    (call,) = vr.merge_calls(r"lookup('merge_variables', '^a\d__.+$')")
    assert call.resolved is False


def test_top_level_keys_accept_quoted_keys():
    text = "\"dead_var\": true\n'other_var': 1\nplain: 2\n\"mismatched': 3\n"
    assert vr.top_level_keys(text) == ["dead_var", "other_var", "plain"]


def test_code_text_drops_yaml_comment_lines_only():
    text = "a: 1\n  # lookup('merge_variables', x)\nb: '#not a comment'\n"
    assert vr.code_text("x.yml", text) == "a: 1\nb: '#not a comment'"
    assert vr.code_text("x.j2", text) == text
