# CrewBidIQ Development Status

## CrewBidIQ 3.0 RC1

Completed:

- Rebranded PairingIQ as CrewBidIQ.
- Preserved Delta and Southwest parser implementations.
- Preserved automatic parser selection and generic fallback.
- Preserved customizable QoL and calendar preference scoring.
- Added GitHub-ready repository cleanup and ignore rules.
- Added Render Blueprint configuration.
- Added deployment and domain-connection instructions.
- Retained regression tests for Delta, Southwest, and parser selection.

Next:

1. Validate additional Delta packages across base, fleet, and month.
2. Add package metadata detection for airline, base, fleet, seat, and bid month.
3. Add a dedicated queue worker and managed database.
4. Add persistent upload storage for multi-instance deployment.
5. Validate American Airlines parsing from an original AA bid package.
6. Add saved profiles and analysis history.
7. Begin the CrewBidIQ Bid Builder calendar optimizer.
