# CrewBidIQ

CrewBidIQ ranks airline pairings or lines using pilot-selected preferences.

## Upload formats
- Most airlines: one PDF bid package.
- Southwest: one ZIP containing both Lines and Pairings, or two individual text files.

## Run locally
```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

The comprehensive user guide is built into the application header.

## Classic and Labs

CrewBidIQ Classic remains the default experience at `/`, with direct Classic results at `/results`. CrewBidIQ Labs runs in the same FastAPI service and shares the completed Classic job stored by the browser:

- `/bid-packages/:bidPackageId/labs` (package-scoped command center)
- `/labs`
- `/labs/build`
- `/labs/recommendations`
- `/labs/preview`
- `/labs/plan`
- `/labs/flight-deck`
- `/labs/flight-deck/trip/:tripId`
- `/labs/flight-deck/shortlist`
- `/labs/flight-deck/compare`

Set `LABS_ENABLED=true` to enable the Labs routes and navigation. Any other value hides Labs from Classic and returns 404 for Labs routes.

Flight Deck Preview is additionally gated by `FLIGHT_DECK_PREVIEW_ENABLED=true`. It reuses the active Classic/Labs package and never requires a second upload.

`/labs` is the unscoped entry point. After an upload or a stored package recovery, Labs uses the authoritative package URL above. The dashboard can recover the package's latest analysis job from the package ID without uploading the source again.

Identical PDF packages are parsed once and stored in the managed SQLite parser cache. Pilot preferences are not part of the cache; reranking always uses the current user's selections.

## Analysis job recovery

The production Render configuration is a persistent Docker web service, not a serverless function. Upload requests persist the source package and return distinct `package_id` and `job_id` values before parsing starts. Parsing then runs independently of the browser connection and is recovered from the managed disk after a service restart.

- `MAX_PARSE_SECONDS=600` is the application parsing deadline.
- `JOB_STALE_SECONDS=720` marks a job expired only after server progress has stopped beyond the parsing deadline.
- `PACKAGE_RETENTION_SECONDS=86400` keeps failed or expired uploads available for one day so Resume analysis can create an idempotent replacement job.
- `ANALYSIS_DEBUG_ENABLED=true` exposes package/job recovery diagnostics in Classic and Labs. It is disabled in normal production UI.

The current deployment uses the service process rather than a distributed job queue. Persistent SQLite state and uploaded files make restarts recoverable on the configured Render disk, but moving to multiple web instances would require a shared queue/lease system before workers could safely scale horizontally.

## v0.2.4 layover-city correction

Ranked cards and city preference scoring now use only true layover/overnight cities. Connection airports, turns, intermediate stations, and the final return to base are not treated as layovers. Expanded details retain a separate "All cities touched" field for reference.


## v0.3.1a filename display patch
- Keeps the selected filename visible on iPhone Safari while uploading and processing.
- Syncs on change, input, pageshow, and Analyze click.
