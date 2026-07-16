import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse


router = APIRouter()


def labs_enabled() -> bool:
    return os.environ.get("LABS_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


LABS_HTML = r"""
<!doctype html>
<html lang="en" data-theme="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
  <meta name="theme-color" content="#071525">
  <title>CrewBidIQ Labs</title>
  <link rel="stylesheet" href="/static/app.css?v=0424">
</head>
<body class="labs-body" data-labs-page="__LABS_PAGE__">
<div class="app-shell">
  <aside class="desktop-sidebar labs-sidebar">
    <a class="side-brand" href="/labs"><span class="wing">&#9992;</span><strong>CrewBid<span>IQ</span></strong><em>Beta</em></a>
    <nav>
      <a href="/labs" class="nav-link" data-labs-route="/labs"><span>Home</span></a>
      <a href="/labs/build" class="nav-link" data-labs-route="/labs/build"><span>Build My Bid</span></a>
      <a href="/labs/recommendations" class="nav-link" data-labs-route="/labs/recommendations"><span>Recommendations</span></a>
      <a href="/labs/preview" class="nav-link" data-labs-route="/labs/preview"><span>Bid Pool Preview</span></a>
      <a href="/labs/southwest" class="nav-link" data-labs-route="/labs/southwest"><span>Southwest Tools</span></a>
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
          <a href="/labs" class="active">Labs <small>Beta</small></a>
        </nav>
      </div>
      <span class="beta-badge">Beta</span>
    </header>

    <main id="labsContent" class="labs-main" aria-live="polite">
      <section class="surface labs-loading"><strong>Opening CrewBidIQ Labs...</strong></section>
    </main>

    <nav class="bottom-nav three labs-bottom-nav" aria-label="Primary navigation">
      <a href="/"><span>A</span>Analyze</a>
      <a href="/results"><span>R</span>Results</a>
      <a href="/labs" class="active"><span>L</span>Labs</a>
    </nav>
  </div>
</div>
<script>window.CREWBIDIQ_LABS_PAGE = "__LABS_PAGE__";</script>
<script src="/static/labs.js?v=0425"></script>
</body>
</html>
"""


def labs_page(page: str) -> HTMLResponse:
    if not labs_enabled():
        raise HTTPException(404, "CrewBidIQ Labs is not enabled")
    return HTMLResponse(LABS_HTML.replace("__LABS_PAGE__", page))


@router.get("/labs", response_class=HTMLResponse)
def labs_landing() -> HTMLResponse:
    return labs_page("landing")


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
