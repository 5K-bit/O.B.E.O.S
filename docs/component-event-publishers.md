# Event Contract v1 Publisher Wiring

External components publish canonical namespaced events to the OBEOS Event API endpoint configured by `OBEOS_EVENT_URL`.

Recommended local value: `http://127.0.0.1:8000/events/v1/publish`.

Publisher failure is intentionally non-fatal. Components remain local-first and continue their primary function when OBEOS is unavailable.

Currently wired publishers:

- MaxPayne: health snapshot, aggregate, and system health state events.
- Nightwatch: shift and task mutation events.
- 3000: snapshot and motion detection events.

The OBEOS ingestion bridge validates the v1 envelope, preserves it under `obeos_event_v1`, and maps it to a legacy-compatible internal event type for existing EventStore and Thoughtglass consumers during migration.
