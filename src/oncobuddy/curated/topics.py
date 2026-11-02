"""ESMO topic table: routing from tumour-type topics to ICD-10 3-character categories.

Routing only. Holds no clinical claim. Errors name the problem, never file content.
"""

import re
from importlib.resources import files

from oncobuddy.curated.loader import parse_yaml_text

TOPICS_FILE = "esmo_topics.yaml"
_TOPIC = re.compile(r"^[a-z][a-z -]{0,59}$")
_CATEGORY = re.compile(r"^C[0-9]{2}$")


def parse_topics(raw: bytes, name: str = TOPICS_FILE) -> dict[str, list[str]]:
    """Strictly parse and validate a topic table. Messages never echo table content."""
    data = parse_yaml_text(raw, name)
    if not isinstance(data, dict) or not data:
        raise ValueError(f"{name}: expected a non-empty mapping of topics")
    topics: dict[str, list[str]] = {}
    for topic, categories in data.items():
        if not isinstance(topic, str) or not _TOPIC.match(topic):
            raise ValueError(f"{name}: topic name is not lowercase words")
        if not isinstance(categories, list) or not categories:
            raise ValueError(f"{name}: topic has no categories or is not a list")
        for code in categories:
            if not isinstance(code, str) or not _CATEGORY.match(code):
                raise ValueError(f"{name}: category is not an ICD-10 3-character code")
        topics[topic] = list(categories)
    return topics


def load_topics() -> dict[str, list[str]]:
    """Return the packaged topic table: topic name to ICD-10 3-character categories."""
    raw = (files("oncobuddy.curated") / TOPICS_FILE).read_bytes()
    return parse_topics(raw, TOPICS_FILE)


def known_categories() -> set[str]:
    """Union of every ICD-10 category listed in the topic table."""
    return {code for categories in load_topics().values() for code in categories}
