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
