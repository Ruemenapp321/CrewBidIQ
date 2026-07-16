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

- `/labs`
- `/labs/build`
- `/labs/recommendations`
- `/labs/preview`
- `/labs/plan`

Set `LABS_ENABLED=true` to enable the Labs routes and navigation. Any other value hides Labs from Classic and returns 404 for Labs routes.

Identical PDF packages are parsed once and stored in the managed SQLite parser cache. Pilot preferences are not part of the cache; reranking always uses the current user's selections.

## v0.2.4 layover-city correction

Ranked cards and city preference scoring now use only true layover/overnight cities. Connection airports, turns, intermediate stations, and the final return to base are not treated as layovers. Expanded details retain a separate "All cities touched" field for reference.


## v0.3.1a filename display patch
- Keeps the selected filename visible on iPhone Safari while uploading and processing.
- Syncs on change, input, pageshow, and Analyze click.
