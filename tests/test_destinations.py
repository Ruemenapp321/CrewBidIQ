from app.destinations import destination_matches, is_transcontinental, resolve_destination, taxonomy_payload


def test_hierarchical_destination_groups_resolve_outside_the_ui():
    hawaii = resolve_destination("Any Hawaii")
    assert hawaii["level"] == "region"
    assert "HNL" in hawaii["airports"]
    assert destination_matches(["ATL", "OGG"], "HAWAII")
    assert destination_matches(["HND"], "JAPAN")
    assert destination_matches(["AMS"], "EUROPE")
    assert any(group["code"] == "CARIBBEAN" for group in taxonomy_payload())


def test_transcontinental_requires_an_actual_coast_to_coast_leg():
    assert is_transcontinental([{"departure": "JFK", "arrival": "LAX"}])
    assert not is_transcontinental([{"departure": "ATL", "arrival": "LAX"}])
    assert not is_transcontinental([{"departure": "JFK", "arrival": "ATL"}])
