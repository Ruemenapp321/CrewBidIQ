from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


ROOT = Path(__file__).resolve().parents[1]


def test_flight_deck_routes_require_both_feature_flags(monkeypatch):
    monkeypatch.setenv("LABS_ENABLED", "true")
    monkeypatch.delenv("FLIGHT_DECK_PREVIEW_ENABLED", raising=False)

    with TestClient(app) as client:
        disabled = client.get("/labs/flight-deck")
        labs = client.get("/labs")
        classic_results = client.get("/results")

    assert disabled.status_code == 404
    assert "Flight Deck Preview" not in labs.text
    assert "Try Flight Deck Preview" not in classic_results.text

    monkeypatch.setenv("FLIGHT_DECK_PREVIEW_ENABLED", "true")
    monkeypatch.setenv("LABS_ENABLED", "false")
    with TestClient(app) as client:
        assert client.get("/labs/flight-deck").status_code == 404


def test_enabled_flight_deck_has_all_preview_routes(monkeypatch):
    monkeypatch.setenv("LABS_ENABLED", "true")
    monkeypatch.setenv("FLIGHT_DECK_PREVIEW_ENABLED", "true")
    routes = {
        "/labs/flight-deck": "results",
        "/labs/flight-deck/trip/delta-package%3AR523": "trip",
        "/labs/flight-deck/shortlist": "shortlist",
        "/labs/flight-deck/compare": "compare",
    }

    with TestClient(app) as client:
        responses = {route: client.get(route) for route in routes}

    for route, page in routes.items():
        response = responses[route]
        assert response.status_code == 200
        assert f'data-flight-deck-page="{page}"' in response.text
        assert 'src="/static/flight-deck.js' in response.text
        assert "CrewBidIQ Classic" in response.text
    assert 'window.CREWBIDIQ_FLIGHT_DECK_TRIP_ID="delta-package:R523"' in responses[
        "/labs/flight-deck/trip/delta-package%3AR523"
    ].text


def test_preview_flag_is_explicit_in_deployment_configuration():
    render = (ROOT / "render.yaml").read_text(encoding="utf-8")
    docker = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "FLIGHT_DECK_PREVIEW_ENABLED" in render
    assert "ENV FLIGHT_DECK_PREVIEW_ENABLED=true" in docker


def test_flight_deck_is_prominent_in_labs_and_safe_from_classic_results(monkeypatch):
    monkeypatch.setenv("LABS_ENABLED", "true")
    monkeypatch.setenv("FLIGHT_DECK_PREVIEW_ENABLED", "true")

    with TestClient(app) as client:
        labs = client.get("/labs")
        labs_script = client.get("/static/labs.js")
        home = client.get("/")
        results = client.get("/results")

    assert 'href="/labs/flight-deck"' in labs.text
    assert "window.CREWBIDIQ_FLIGHT_DECK_PREVIEW_ENABLED = true" in labs.text
    assert "Flight Deck Preview" in labs_script.text
    assert "Open Flight Deck Preview" in labs_script.text
    assert "Try Flight Deck Preview" not in home.text
    assert '<a class="flight-deck-link button" href="/labs/flight-deck">Try Flight Deck Preview</a>' in results.text


def test_flight_deck_reuses_active_package_and_strict_canonical_inventory():
    script = (ROOT / "app" / "static" / "flight-deck.js").read_text(encoding="utf-8")

    for shared_key in (
        "crewbidiqLatestJob",
        "crewbidiqActiveJob",
        "crewbidiqActivePackage",
        "crewbidiqShortlist",
        "crewbidiqComparison",
    ):
        assert shared_key in script
    assert "fetch(`/api/jobs/${encodeURIComponent(jobId)}`)" in script
    assert "new FormData" not in script
    assert "item.package_id === packageId" in script
    assert "canonical.every(trip => trip.package_id === packageId)" in script
    assert "item.bidable_inventory_confirmed === true" in script
    assert "canonical.every(trip => trip.bidable_inventory_confirmed === true)" in script
    assert "item.eligibility_violations" in script
    assert "matchClass(item) === 'near' ? reasons : reasons.slice(0, 2)" in script
    assert "Mixed-package results were rejected." in script
    assert "stored.package_id !== activePackageId()" in script
    assert "clearPackageDependentState" in script
    assert "window.addEventListener('storage'" in script


def test_flight_deck_groups_fields_and_airline_terminology_are_explicit():
    script = (ROOT / "app" / "static" / "flight-deck.js").read_text(encoding="utf-8")

    for group in ("Exact Matches", "Strong Matches", "Partial Matches", "Near Matches"):
        assert group in script
    for field in (
        "Trip Length",
        "Total Pay",
        "Trip Credit",
        "TFP",
        "TAFB",
        "Priority layovers",
        "Report",
        "Release",
        "Shortlist",
        "Compare",
        "Open Trip Briefing",
    ):
        assert field in script
    assert "if (tripAirline(item) === 'delta') return 'Rotation'" in script
    assert "if (tripAirline(item) === 'american') return 'Sequence'" in script
    assert "item?.item_type === 'line' ? 'Line' : 'Pairing'" in script
    assert "return 'Pairing'" in script
    assert "[legs[0].origin, ...legs.map(leg => leg.destination)]" in script


def test_flight_deck_filters_and_airline_relevant_sorting_are_available():
    script = (ROOT / "app" / "static" / "flight-deck.js").read_text(encoding="utf-8")

    for label in (
        "Best Match",
        "Trip Length",
        "TAFB",
        "Total Pay",
        "Trip Credit",
        "TFP",
        "Report Time",
        "Release Time",
        "Preferred Layovers",
        "Exact Matches only",
        "1-day",
        "2-day",
        "3-day",
        "4-day",
        "5+ day",
        "No redeyes",
        "One leg per duty day",
        "Two legs maximum",
        "Saved trips",
    ):
        assert label in script
    assert "if (airline === 'delta')" in script
    assert "if (airline === 'southwest')" in script
    assert "if (filterState.exactOnly && matchClass(item) !== 'exact')" in script


def test_flight_deck_omits_disallowed_headline_content():
    sources = (
        (ROOT / "app" / "static" / "flight-deck.js").read_text(encoding="utf-8"),
        (ROOT / "app" / "labs.py").read_text(encoding="utf-8"),
    )
    combined = "\n".join(sources).lower()

    for disallowed in (
        "total distance",
        "nights away",
        "competitive",
        "unsupported commute",
        "holding probability",
        "ai-powered",
    ):
        assert disallowed not in combined


def test_flight_deck_mobile_layout_stacks_without_horizontal_scrolling():
    styles = (ROOT / "app" / "static" / "app.css").read_text(encoding="utf-8")
    script = (ROOT / "app" / "static" / "flight-deck.js").read_text(encoding="utf-8")

    assert "@media(max-width:620px)" in styles
    assert ".flight-deck-body{overflow-x:hidden}" in styles
    assert ".fd-compare-grid,.fd-briefing-grid{grid-template-columns:1fr}" in styles
    assert "min-height:44px" in styles
    assert "calc(145px + env(safe-area-inset-bottom))" in styles
    assert ".fd-card-actions{position:sticky" in styles
    assert "crewbidiqTheme" in script
    assert "document.documentElement.dataset.theme" in script
