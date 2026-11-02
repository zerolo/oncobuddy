import re

import pytest

from oncobuddy.curated.topics import known_categories, load_topics, parse_topics

# Categories that may legitimately appear under two topics. Start empty.
SHARED: set[str] = set()
CATEGORY = re.compile(r"^C[0-9]{2}$")


def test_load_topics_is_non_empty_dict():
    topics = load_topics()
    assert isinstance(topics, dict)
    assert topics


def test_every_category_is_icd10_three_character():
    for categories in load_topics().values():
        for code in categories:
            assert CATEGORY.match(code), code


def test_no_topic_is_empty():
    for name, categories in load_topics().items():
        assert categories, name


def test_no_category_under_two_topics_unless_shared():
    seen: dict[str, str] = {}
    for name, categories in load_topics().items():
        for code in categories:
            if code in SHARED:
                continue
            assert code not in seen, f"{code} under {seen.get(code)} and {name}"
            seen[code] = name


def test_known_categories_is_union_of_topics():
    union = set()
    for categories in load_topics().values():
        union.update(categories)
    assert known_categories() == union


def test_neuroendocrine_is_not_a_topic():
    assert "neuroendocrine" not in load_topics()


def test_parse_rejects_non_list_value_without_echo():
    raw = b"breast: SECRET-VALUE\n"
    with pytest.raises(ValueError) as err:
        parse_topics(raw)
    assert "SECRET-VALUE" not in str(err.value)


def test_parse_rejects_bad_category_without_echo():
    raw = b"breast:\n  - SECRET-CODE\n"
    with pytest.raises(ValueError) as err:
        parse_topics(raw)
    assert "SECRET-CODE" not in str(err.value)


def test_parse_rejects_duplicate_keys_without_echo():
    raw = b"breast:\n  - C50\nbreast:\n  - C50\n"
    with pytest.raises(ValueError) as err:
        parse_topics(raw)
    assert "duplicate" in str(err.value)
    assert "C50" not in str(err.value)


def test_parse_rejects_top_level_non_mapping():
    with pytest.raises(ValueError):
        parse_topics(b"- C50\n")


def test_parse_accepts_valid_table():
    assert parse_topics(b"head and neck:\n  - C00\n  - C01\n") == {"head and neck": ["C00", "C01"]}
