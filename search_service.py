from typing import Any, Dict, List, Optional

import alerts
from intent_manager import IntentManager
from query_engine import QueryEngine

# Event types that live in the alerts table (raised through alerts.py with a
# snapshot + notification) rather than in the zone-session events table.
ALERT_EVENT_TYPES = {
    alerts.FALL_DETECTED,
    alerts.RESTRICTED_ZONE_ENTRY,
    alerts.AFTER_HOURS_ENTRY,
    alerts.VIOLENCE_DETECTED,
}


class SearchService:
    """Single entry point turning a natural-language query into structured event results.

    Wraps IntentManager (query -> filters) and QueryEngine (filters -> DB rows) behind one
    call so any interface (CLI, backend, dashboard) doesn't need to wire both together
    itself. Queries that name an alert type (e.g. "fall", "fell", "intrusion") are routed
    to the alerts table instead of the zone-session events table.
    """

    def __init__(
        self,
        query_engine: Optional[QueryEngine] = None,
        intent_manager: Optional[IntentManager] = None,
    ):
        self.query_engine = query_engine or QueryEngine()
        self.intent_manager = intent_manager or IntentManager()

    def search(self, query: str, session_mode: Optional[str] = None) -> List[Dict[str, Any]]:
        self.intent_manager.set_intent(query)
        filters = self.intent_manager.get_filters()

        event_type = filters.get("event") or filters.get("alert_type")
        if event_type in ALERT_EVENT_TYPES:
            return alerts.search_alerts(
                alert_type=event_type,
                camera_id=filters.get("camera_id"),
                zone_id=filters.get("zone_id"),
            )

        return self.query_engine.run_query(filters=filters, session_mode=session_mode)

    def last_parsed_intent(self) -> Dict[str, Any]:
        return dict(self.intent_manager.intent)
