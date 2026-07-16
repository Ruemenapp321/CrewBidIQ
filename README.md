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

## v0.2.4 layover-city correction

Ranked cards and city preference scoring now use only true layover/overnight cities. Connection airports, turns, intermediate stations, and the final return to base are not treated as layovers. Expanded details retain a separate "All cities touched" field for reference.


## v0.3.1a filename display patch
- Keeps the selected filename visible on iPhone Safari while uploading and processing.
- Syncs on change, input, pageshow, and Analyze click.
