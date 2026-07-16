# CrewBidIQ 3.0 RC1

**Build the month you actually want.**

CrewBidIQ is a mobile-first airline bid-package analysis platform. It parses airline-specific pairing formats into one normalized model, applies pilot-selected quality-of-life preferences, and ranks pairings with plain-language explanations.

## Included in RC1

- Delta master-pairing parser
- Southwest pairing-text parser
- American parser framework (beta; awaiting a real AA fixture)
- Generic fallback parser
- Automatic parser selection
- Background analysis jobs with persistent job status
- Customizable QoL scoring
- Calendar, report/release, layover, deadhead, aircraft, and trip-length preferences
- CSV export
- iPhone-friendly upload and results interface
- Docker and Render deployment configuration

## Repository layout

```text
app/
  main.py
  parsers/
    base.py
    delta.py
    southwest.py
    american.py
    generic.py
  static/
    app.js
    app.css
data/
Dockerfile
render.yaml
requirements.txt
tests/
```

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000`.

## Deploy to Render

1. Upload the extracted repository contents to GitHub.
2. In Render, choose **New → Blueprint**.
3. Select the GitHub repository containing this project.
4. Render will detect `render.yaml`.
5. Create the service and wait for the deployment to become live.
6. Add `crewbidiq.com` as a custom domain in the Render service.
7. Copy Render's DNS records into your domain registrar only after the service is live.

## Current limitations

RC1 uses an in-process background task and SQLite persistence. This is appropriate for private beta testing on one web instance. The next production increment will move jobs to a dedicated worker and managed database before broad public use.

American Airlines support remains beta until an original AA bid package is supplied and validated.

## Test suite

```bash
pytest -q
```
