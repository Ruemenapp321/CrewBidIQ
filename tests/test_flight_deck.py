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
    assert "return tripModel(item).simplified_route || 'Route unavailable'" in script


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
        "ai brief",
        "ai summary",
        "ai insights",
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


def test_trip_briefing_has_airline_titles_and_all_required_sections():
    script = (ROOT / "app" / "static" / "flight-deck.js").read_text(encoding="utf-8")
    labs = (ROOT / "app" / "labs.py").read_text(encoding="utf-8")

    for title in ("Rotation Briefing", "Sequence Briefing", "Pairing Briefing", "Line Briefing"):
        assert title in script
    for section in (
        "Overview",
        "Operational Highlights",
        "Things to Know",
        "Duty-Day Summary",
        "Layovers and Hotels",
        "Pay or TFP Breakdown",
        "Fatigue",
        "Likelihood of Holding",
        "Commute Planner",
        "Recommendation",
        "Original Airline Trip",
    ):
        assert f"<h2>{section}</h2>" in script
    assert "Exact match explanation" in script
    assert 'src="/static/flight-deck.js?v=0003"' in labs


def test_trip_briefing_reads_trip_facts_from_confirmed_canonical_models_only():
    script = (ROOT / "app" / "static" / "flight-deck.js").read_text(encoding="utf-8")
    briefing = script.split("function tripBriefingPage()", 1)[1].split("function noPackagePage()", 1)[0]

    assert "function legacyTripBriefingPage" not in script
    assert "canonicalTrips(item).filter" in script
    assert "model.package_id === packageId" in script
    assert "model.bidable_inventory_confirmed === true" in script
    assert "const models = briefingModels(item)" in briefing
    assert "const model = briefingPrimaryModel(item)" in briefing
    assert "model?.trip_length_days" in briefing
    assert "model?.duty_period_count" in briefing
    assert "model?.tafb" in briefing
    assert "model?.report" in briefing
    assert "model?.release" in briefing
    for legacy_fallback in (
        "tripLegs(item)",
        "tripLayovers(item)",
        "tripTafb(item)",
        "eventTime(item",
        "airlinePayMetrics(item)",
        "simplifiedRoute(item)",
    ):
        assert legacy_fallback not in briefing
    assert "Array.isArray(model.duty_days)" in script
    assert "Array.isArray(model.layovers)" in script
    assert "model.pay_breakdown" in script
    assert "model.tfp" in script


def test_trip_briefing_preserves_source_provenance_and_safe_missing_states():
    script = (ROOT / "app" / "static" / "flight-deck.js").read_text(encoding="utf-8")

    assert "if (model.bidable_inventory_confirmed !== true) return ''" in script
    for source_field in ("model.source_text", "model.source_page", "model.source_section"):
        assert source_field in script
    assert "Confirmed bidable inventory" in script
    for missing_state in (
        "Canonical trip details are unavailable",
        "Duty-day details are unavailable",
        "No canonical layovers are available",
        "A normalized pay or TFP breakdown is unavailable",
        "Confirmed bidable source provenance is unavailable",
        "No Flight Deck fatigue assessment is available",
        "No holding assessment is available",
        "No commute plan is available",
    ):
        assert missing_state in script


def test_trip_briefing_layout_is_responsive_on_desktop_and_mobile():
    styles = (ROOT / "app" / "static" / "app.css").read_text(encoding="utf-8")

    assert ".fd-briefing-layout{display:grid;grid-template-columns:repeat(2,minmax(0,1fr))" in styles
    assert ".fd-briefing-wide,.fd-briefing-overview{grid-column:1/-1}" in styles
    assert ".fd-briefing-layout{grid-template-columns:1fr}" in styles
    assert ".fd-briefing-wide,.fd-briefing-overview{grid-column:auto}" in styles
    assert ".fd-fact-grid,.fd-source-meta{grid-template-columns:1fr 1fr}" in styles
    assert ".fd-duty-day>header{grid-template-columns:1fr}" in styles


def test_trip_flow_uses_canonical_duty_days_legs_layovers_and_map_path():
    script = (ROOT / "app" / "static" / "flight-deck.js").read_text(encoding="utf-8")
    flow = script.split("function tripFlow(models)", 1)[1].split("function layoversAndHotels", 1)[0]

    assert "function canonicalTripFacts(item)" in script
    assert "model.ordered_legs" in script
    assert "model.duty_days" in script
    assert "model.layovers" in script
    assert "model.route_map_airports" in script
    assert "function tripLegs(item) { return canonicalTripFacts(item).orderedLegs; }" in script
    assert "function tripDutyDays(item) { return canonicalTripFacts(item).dutyDays; }" in script
    assert "function tripLayovers(item) { return canonicalTripFacts(item).layovers; }" in script
    assert "function tripMapAirports(item) { return canonicalTripFacts(item).mapAirports; }" in script
    assert "Array.isArray(model.duty_days)" in flow
    assert "Array.isArray(day.ordered_legs)" in flow
    assert "day.layover_after_duty" in flow
    assert 'data-duty-day=' in flow
    assert "artificial" not in flow.lower()
    assert "layover-only" not in flow.lower()


def test_trip_flow_displays_operating_details_connections_and_24_hour_local_times():
    script = (ROOT / "app" / "static" / "flight-deck.js").read_text(encoding="utf-8")
    flow = script.split("function tripFlow(models)", 1)[1].split("function layoversAndHotels", 1)[0]

    for value in (
        "Duty Day",
        "Local Report",
        "Local Release",
        "Operating",
        "Deadhead",
        "Flight",
        "Aircraft",
        "Depart",
        "Arrive",
        "Connection / Sit",
        "Layover / Overnight after release",
        "Duration",
        "Hotel",
    ):
        assert value in flow
    assert "formatLocalTime24" in flow
    assert "leg.connection_after" in flow
    assert "source_time" not in flow
    assert "source_departure_time" not in flow
    assert "source_arrival_time" not in flow
    assert "Herb" not in script


def test_trip_flow_mobile_layout_stacks_connections_and_layover_details():
    styles = (ROOT / "app" / "static" / "app.css").read_text(encoding="utf-8")

    assert ".fd-duty-layover{display:grid;grid-template-columns:1fr auto 1fr" in styles
    assert ".fd-duty-layover{grid-template-columns:1fr}" in styles
    assert ".fd-trip-connection{align-items:flex-start;flex-direction:column}" in styles
