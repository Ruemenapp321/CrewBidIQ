import json
import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse


router = APIRouter()


def labs_enabled() -> bool:
    return os.environ.get("LABS_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def southwest_line_ranker_enabled() -> bool:
    return os.environ.get("SOUTHWEST_LINE_RANKER_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}


def flight_deck_preview_enabled() -> bool:
    return os.environ.get("FLIGHT_DECK_PREVIEW_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


LABS_HTML = r"""
<!doctype html>
<html lang="en" data-theme="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
  <meta name="theme-color" content="#071525">
  <title>CrewBidIQ Labs</title>
  __LABS_MAP_STYLES__
  <link rel="stylesheet" href="/static/app.css?v=0431">
</head>
<body class="labs-body" data-labs-page="__LABS_PAGE__">
<div class="app-shell">
  <aside class="desktop-sidebar labs-sidebar">
    <a class="side-brand" href="__LABS_HOME_HREF__"><span class="wing">&#9992;</span><strong>CrewBid<span>IQ</span></strong><em>Beta</em></a>
    <nav>
      __DASHBOARD_NAV__
      <a href="/labs/build" class="nav-link" data-labs-route="/labs/build"><span>Build My Bid</span></a>
      <a href="/labs/recommendations" class="nav-link" data-labs-route="/labs/recommendations"><span>Recommendations</span></a>
      <a href="/labs/preview" class="nav-link" data-labs-route="/labs/preview"><span>Bid Pool Preview</span></a>
      __FLIGHT_DECK_LINK__
      __SOUTHWEST_LINK__
      <a href="/labs/plan" class="nav-link" data-labs-route="/labs/plan"><span>Bid Plan</span></a>
    </nav>
    <a class="labs-return" href="/">Return to Classic</a>
    <div class="side-footer">CrewBidIQ Labs - experimental tools</div>
  </aside>

  <div class="app-main">
    <header class="mobile-header labs-header">
      <div class="header-identity">
        <a class="brand-word" href="/">CrewBid<span>IQ</span></a>
        <nav class="experience-switch" aria-label="CrewBidIQ experience">
          <a href="/">Classic</a>
          <a href="__LABS_HOME_HREF__" class="active">Labs <small>Beta</small></a>
        </nav>
      </div>
      <div class="header-controls"><span class="beta-badge">Beta</span><button id="labsTheme" class="round-button" type="button" aria-label="Toggle color theme">◐</button></div>
    </header>

    <main id="labsContent" class="labs-main" aria-live="polite">
      <section class="surface labs-loading"><strong>Opening CrewBidIQ Labs...</strong></section>
    </main>

    <nav class="bottom-nav three labs-bottom-nav" aria-label="Primary navigation">
      <a href="/"><span>A</span>Analyze</a>
      <a href="/results"><span>R</span>Results</a>
      <a href="__LABS_HOME_HREF__" class="active"><span>L</span>Labs</a>
    </nav>
  </div>
</div>
<script>window.CREWBIDIQ_LABS_PAGE = "__LABS_PAGE__";window.CREWBIDIQ_BID_PACKAGE_ID=__BID_PACKAGE_ID_JSON__;window.CREWBIDIQ_FLIGHT_DECK_PREVIEW_ENABLED = __FLIGHT_DECK_ENABLED__;window.CREWBIDIQ_ANALYSIS_DEBUG_ENABLED=__ANALYSIS_DEBUG_ENABLED__;</script>
__LABS_MAP_SCRIPTS__
<script src="/static/labs.js?v=0431"></script>
</body>
</html>
"""


def labs_page(page: str, bid_package_id: str = "") -> HTMLResponse:
    if not labs_enabled():
        raise HTTPException(404, "CrewBidIQ Labs is not enabled")
    if page == "southwest" and not southwest_line_ranker_enabled():
        raise HTTPException(404, "Southwest Line Ranker is not enabled")
    southwest_link = (
        '<a href="/labs/southwest" class="nav-link" data-labs-route="/labs/southwest"><span>Southwest Tools</span></a>'
        if southwest_line_ranker_enabled() else ""
    )
    flight_deck_link = (
        '<a href="/labs/flight-deck" class="nav-link"><span>Flight Deck Preview</span></a>'
        if flight_deck_preview_enabled() else ""
    )
    dashboard = page == "analysis_dashboard"
    home_href = f"/bid-packages/{bid_package_id}/labs" if bid_package_id else "/labs"
    dashboard_nav = (
        f'<a href="{home_href}" class="nav-link" data-labs-route="{home_href}"><span>Package Overview</span></a>'
        '<a href="#locations" class="nav-link"><span>Locations</span></a>'
        '<a href="#risk-findings" class="nav-link"><span>Risk Signals</span></a>'
        '<a href="#win-outlook" class="nav-link"><span>Win Outlook</span></a>'
        '<a href="#source-records" class="nav-link"><span>Source Records</span></a>'
        if dashboard else '<a href="/labs" class="nav-link" data-labs-route="/labs"><span>Package Overview</span></a>'
    )
    map_styles = '<link rel="stylesheet" href="/static/vendor/leaflet/leaflet.css?v=1.9.4">' if dashboard else ""
    map_scripts = (
        '<script src="/static/airport-coordinates.js?v=20260716"></script>\n'
        '<script src="/static/vendor/leaflet/leaflet.js?v=1.9.4"></script>'
        if dashboard else ""
    )
    return HTMLResponse(
        LABS_HTML.replace("__LABS_PAGE__", page)
        .replace("__BID_PACKAGE_ID_JSON__", json.dumps(bid_package_id or None).replace("<", "\\u003c"))
        .replace("__LABS_HOME_HREF__", home_href)
        .replace("__DASHBOARD_NAV__", dashboard_nav)
        .replace("__LABS_MAP_STYLES__", map_styles)
        .replace("__LABS_MAP_SCRIPTS__", map_scripts)
        .replace("__SOUTHWEST_LINK__", southwest_link)
        .replace("__FLIGHT_DECK_LINK__", flight_deck_link)
        .replace("__FLIGHT_DECK_ENABLED__", "true" if flight_deck_preview_enabled() else "false")
        .replace("__ANALYSIS_DEBUG_ENABLED__", "true" if os.environ.get("ANALYSIS_DEBUG_ENABLED", "false").lower() == "true" else "false")
    )


FLIGHT_DECK_HTML = r"""
<!doctype html>
<html lang="en" data-theme="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
  <meta name="theme-color" content="#071525">
  <title>Flight Deck Preview | CrewBidIQ</title>
  __FLIGHT_DECK_MAP_STYLES__
  <link rel="stylesheet" href="/static/app.css?v=0431">
</head>
<body class="labs-body flight-deck-body" data-flight-deck-page="__FLIGHT_DECK_PAGE__">
<div class="app-shell">
  <aside class="desktop-sidebar labs-sidebar flight-deck-sidebar">
    <a class="side-brand" href="/labs/flight-deck"><span class="wing">&#9992;</span><strong>Flight Deck</strong><em>Preview</em></a>
    <nav aria-label="Flight Deck navigation">
      <a href="/labs/flight-deck" class="nav-link" data-flight-deck-route="results"><span>Results</span></a>
      <a href="/labs/flight-deck/shortlist" class="nav-link" data-flight-deck-route="shortlist"><span>Shortlist</span></a>
      <a href="/labs/flight-deck/compare" class="nav-link" data-flight-deck-route="compare"><span>Compare</span></a>
    </nav>
    <a class="labs-return" href="/labs">Back to Labs</a>
    <div class="side-footer"><a href="/results">CrewBidIQ Classic</a></div>
  </aside>
  <div class="app-main">
    <header class="mobile-header flight-deck-header">
      <div class="header-identity"><a class="brand-word" href="/labs/flight-deck">Flight Deck</a><span class="beta-badge">Preview</span></div>
      <div class="header-controls"><a class="text-button button" href="/results">Classic</a><button id="flightDeckTheme" class="round-button" type="button" aria-label="Toggle color theme">◐</button></div>
    </header>
    <main id="flightDeckContent" class="flight-deck-main" aria-live="polite">
      <section class="surface labs-loading"><strong>Opening Flight Deck Preview...</strong></section>
    </main>
    <nav class="bottom-nav three flight-deck-bottom-nav" aria-label="Flight Deck navigation">
      <a href="/labs/flight-deck" data-flight-deck-route="results"><span>R</span>Results</a>
      <a href="/labs/flight-deck/shortlist" data-flight-deck-route="shortlist"><span>S</span>Shortlist</a>
      <a href="/labs/flight-deck/compare" data-flight-deck-route="compare"><span>C</span>Compare</a>
    </nav>
  </div>
</div>
<script>window.CREWBIDIQ_FLIGHT_DECK_PAGE="__FLIGHT_DECK_PAGE__";window.CREWBIDIQ_FLIGHT_DECK_TRIP_ID=__TRIP_ID_JSON__;</script>
__FLIGHT_DECK_MAP_SCRIPTS__
<script src="/static/flight-deck.js?v=0005"></script>
</body>
</html>
"""


def flight_deck_page(page: str, trip_id: str = "") -> HTMLResponse:
    if not labs_enabled() or not flight_deck_preview_enabled():
        raise HTTPException(404, "Flight Deck Preview is not enabled")
    map_styles = '<link rel="stylesheet" href="/static/vendor/leaflet/leaflet.css?v=1.9.4">' if page == "trip" else ""
    map_scripts = (
        '<script src="/static/airport-coordinates.js?v=20260716"></script>\n'
        '<script src="/static/vendor/leaflet/leaflet.js?v=1.9.4"></script>'
        if page == "trip" else ""
    )
    return HTMLResponse(
        FLIGHT_DECK_HTML.replace("__FLIGHT_DECK_PAGE__", page)
        .replace("__TRIP_ID_JSON__", json.dumps(trip_id).replace("<", "\\u003c"))
        .replace("__FLIGHT_DECK_MAP_STYLES__", map_styles)
        .replace("__FLIGHT_DECK_MAP_SCRIPTS__", map_scripts)
    )


@router.get("/labs", response_class=HTMLResponse)
def labs_landing() -> HTMLResponse:
    return labs_page("analysis_dashboard")


@router.get("/bid-packages/{bid_package_id}/labs", response_class=HTMLResponse)
def bid_package_labs(bid_package_id: str) -> HTMLResponse:
    return labs_page("analysis_dashboard", bid_package_id)


@router.get("/labs/build", response_class=HTMLResponse)
def labs_build() -> HTMLResponse:
    return labs_page("build")


@router.get("/labs/recommendations", response_class=HTMLResponse)
def labs_recommendations() -> HTMLResponse:
    return labs_page("recommendations")


@router.get("/labs/preview", response_class=HTMLResponse)
def labs_preview() -> HTMLResponse:
    return labs_page("preview")


@router.get("/labs/plan", response_class=HTMLResponse)
def labs_plan() -> HTMLResponse:
    return labs_page("plan")


@router.get("/labs/southwest", response_class=HTMLResponse)
def labs_southwest() -> HTMLResponse:
    return labs_page("southwest")


@router.get("/labs/flight-deck", response_class=HTMLResponse)
def flight_deck_results() -> HTMLResponse:
    return flight_deck_page("results")


@router.get("/labs/flight-deck/trip/{trip_id}", response_class=HTMLResponse)
def flight_deck_trip(trip_id: str) -> HTMLResponse:
    return flight_deck_page("trip", trip_id)


@router.get("/labs/flight-deck/shortlist", response_class=HTMLResponse)
def flight_deck_shortlist() -> HTMLResponse:
    return flight_deck_page("shortlist")


@router.get("/labs/flight-deck/compare", response_class=HTMLResponse)
def flight_deck_compare() -> HTMLResponse:
    return flight_deck_page("compare")
