"""Tests for read-only Cypher validation and value normalization."""

import pytest
from app.services.graphrag.cypher_guard import normalize_neo4j_value, validate_read_cypher


@pytest.mark.parametrize(
    "cypher",
    [
        "MATCH (n:TaekwondoKnowledgeEntity) RETURN n.name LIMIT 10",
        "MATCH (e)-[:TAEKWONDO_RELATION]->(t) WHERE e.stableKey = $key RETURN t LIMIT 20",
        "MATCH (n) WHERE n.description CONTAINS 'kicking' RETURN count(n) AS total",
        # Write keyword inside string literals must be permitted
        "MATCH (n) WHERE n.name = 'I want to DELETE this' RETURN n",
        'MATCH (n) WHERE n.text = "CREATE new rule" RETURN n',
        # Comments with keywords inside must be ignored
        "MATCH (n) // DELETE comment\nRETURN n LIMIT 1",
        "MATCH (n) /* CREATE comment */ RETURN n LIMIT 1",
    ],
)
def test_valid_cypher_passes(cypher: str) -> None:
    validate_read_cypher(cypher)  # Must not raise


@pytest.mark.parametrize(
    ("cypher", "expected_err"),
    [
        ("", "empty"),
        ("   ", "empty"),
        ("CREATE (n:Foo {name: 'bar'}) RETURN n", "CREATE"),
        ("MATCH (n) MERGE (n)-[:REL]->(m) RETURN m", "MERGE"),
        ("MATCH (n) DELETE n", "DELETE"),
        ("MATCH (n) DETACH DELETE n", "DETACH"),
        ("MATCH (n) SET n.name = 'foo' RETURN n", "SET"),
        ("MATCH (n) REMOVE n.name RETURN n", "REMOVE"),
        ("DROP INDEX foo", "DROP"),
        ("MATCH (n) RETURN n; MATCH (m) RETURN m", ";"),
        ("MATCH (n) RETURN n.pskg:productName", "Namespace"),
    ],
)
def test_invalid_cypher_raises_value_error(cypher: str, expected_err: str) -> None:
    with pytest.raises(ValueError, match=rf"(?i){expected_err}"):
        validate_read_cypher(cypher)


def test_normalize_neo4j_value_date() -> None:
    class _FakeDate:
        def iso_format(self) -> str:
            return "2026-10-12"

    assert normalize_neo4j_value(_FakeDate()) == "2026-10-12"


def test_normalize_neo4j_value_duration() -> None:
    class Duration:
        def __str__(self) -> str:
            return "P1Y2M"

    assert normalize_neo4j_value(Duration()) == "P1Y2M"


def test_normalize_neo4j_value_point() -> None:
    class CartesianPoint:
        srid = 7203
        x = 10.5
        y = 20.5
        z = 0.0

    point = CartesianPoint()
    result = normalize_neo4j_value(point)
    assert result == {"srid": 7203, "x": 10.5, "y": 20.5, "z": 0.0}


def test_normalize_neo4j_value_primitives() -> None:
    assert normalize_neo4j_value(42) == 42
    assert normalize_neo4j_value("test") == "test"
    assert normalize_neo4j_value(None) is None
    assert normalize_neo4j_value([1, 2, 3]) == [1, 2, 3]
    assert normalize_neo4j_value({"k": "v"}) == {"k": "v"}
