# CrewBidIQ v0.2.1 Build Notes

## Upload workflow
- Most airlines use one PDF bid package.
- Southwest accepts either one ZIP containing both Lines and Pairings or two individual text files.
- Removed Base, Fleet/Category, and Bid Month fields from the upload screen.

## Preference and results cleanup
- Renamed city preference tiers for clearer, airline-neutral language.
- Uses Preferred Days Off wording.
- Retains Earliest Report Time and Latest Release Time without commuter-specific duplicate fields.
- Adds Work Holidays, Mid-Rotation Redeye, Redeye Start, and Maximum Legs After Post-Redeye Rest.
- Removed Operating Dates from the results table.
- Added a visible Conflicts definition.
- Delta soft credit is limited to EDP, HOL, and SIT; other airlines display N/A.

## Help
- Added a comprehensive in-app user guide accessible from the header.

## Validation
- Python compilation passed.
- JavaScript syntax validation passed.
- Automated parser tests: 4 passed.
