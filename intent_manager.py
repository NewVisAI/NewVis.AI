import re
import os
from collections import OrderedDict
from typing import Any, Dict, Optional, Tuple

from zone_manager import get_all_zones
from llm_parser import LLMParser

# Cache ONLY the expensive LLM parses (the rule parse is ~1.7 ms, so it always
# runs fresh — no staleness if zones are redrawn). Keyed on the normalized query.
_LLM_INTENT_CACHE: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
_LLM_INTENT_CACHE_MAX = 512


def _llm_cache_put(key: str, value: Dict[str, Any]) -> None:
    _LLM_INTENT_CACHE[key] = dict(value)
    _LLM_INTENT_CACHE.move_to_end(key)
    while len(_LLM_INTENT_CACHE) > _LLM_INTENT_CACHE_MAX:
        _LLM_INTENT_CACHE.popitem(last=False)


class IntentManager:
    TIME_KEYWORDS = {
        "morning": (6, 11),
        "afternoon": (12, 17),
        "evening": (18, 21),
        "night": (22, 5),
        "day": (6, 18),
        "midnight": (0, 2),
    }

    OBJECT_ALIAS = {
        "person": "person",
        "persons": "person",
        "people": "person",
        "man": "person",
        "men": "person",
        "woman": "person",
        "women": "person",
        "car": "car",
        "cars": "car",
        "truck": "truck",
        "trucks": "truck",
        "bus": "bus",
        "buses": "bus",
        "busses": "bus",
        "bike": "motorcycle",
        "bikes": "motorcycle",
        "bicycle": "motorcycle",
        "bicycles": "motorcycle",
        "motorcycle": "motorcycle",
        "motorcycles": "motorcycle",
        "motorbike": "motorcycle",
        "motorbikes": "motorcycle",
    }

    EVENT_ALIAS = {
        "enter": "entering",
        "enters": "entering",
        "entered": "entering",
        "entering": "entering",
        "stay": "staying",
        "stays": "staying",
        "stayed": "staying",
        "staying": "staying",
        "exit": "leaving",
        "exits": "leaving",
        "leave": "leaving",
        "leaves": "leaving",
        "leaving": "leaving",
        "uniform": "dress_code_violation",
        "uniforms": "dress_code_violation",
        "dress": "dress_code_violation",
        "violation": "dress_code_violation",
        "violations": "dress_code_violation",
        "run": "running_detected",
        "runs": "running_detected",
        "ran": "running_detected",
        "running": "running_detected",
        "fall": "fall_detected",
        "falls": "fall_detected",
        "fell": "fall_detected",
        "fallen": "fall_detected",
        "falling": "fall_detected",
        "collapse": "fall_detected",
        "collapses": "fall_detected",
        "collapsed": "fall_detected",
        "slip": "fall_detected",
        "slips": "fall_detected",
        "hazard": "fall_detected",
        "hazards": "fall_detected",
        "violence": "violence_detected",
        "violent": "violence_detected",
        "fight": "violence_detected",
        "fights": "violence_detected",
        "fighting": "violence_detected",
        "altercation": "violence_detected",
        "altercations": "violence_detected",
        "anomaly": "violence_detected",
        "anomalies": "violence_detected",
        "scuffle": "violence_detected",
        "brawl": "violence_detected",
        "intrusion": "restricted_zone_entry",
        "intrusions": "restricted_zone_entry",
        "trespass": "restricted_zone_entry",
        "trespasses": "restricted_zone_entry",
        "trespassing": "restricted_zone_entry",
    }

    def __init__(self):
        self.intent: Dict[str, Any] = {}
        self.last_query = ""
        self.llm_parser = None
        if os.environ.get("GROQ_API_KEY"):
            self.llm_parser = LLMParser()

    def set_intent(self, query: str) -> None:
        self.last_query = query

        # Rules FIRST: the rule parser is ~400x faster than the Groq LLM round-trip
        # (~1.7 ms vs ~700 ms) and scores HIGHER on our own NLQ benchmark. Only when
        # rules find no structured signal (they returned just the raw-query fallback)
        # do we pay for the LLM — and that result is cached so repeats are instant.
        parsed = self._rule_based_parse(query)
        rule_confident = any(key != "query" for key in parsed)

        if not rule_confident and self.llm_parser:
            cache_key = " ".join(query.lower().split())
            cached = _LLM_INTENT_CACHE.get(cache_key)
            if cached is not None:
                _LLM_INTENT_CACHE.move_to_end(cache_key)
                parsed = dict(cached)
                print("🧠 LLM Intent (cached):", parsed)
            else:
                try:
                    llm_intent = self.llm_parser.parse(query)
                    if llm_intent:
                        parsed = llm_intent
                        _llm_cache_put(cache_key, llm_intent)
                        print("🧠 LLM Parsed Intent (rule fallback):", parsed)
                except Exception as e:
                    print("⚠️ LLM parsing failed, using rule result:", e)

        self.intent = parsed
        print("🧠 Intent:", self.intent)

    def get_filters(self) -> Dict[str, Any]:
        filters: Dict[str, Any] = {}

        obj = self.intent.get("object")
        if obj:
            normalized = self._normalize_object(obj)
            if normalized:
                filters["object"] = normalized
        else:
            fallback_obj = self._fallback_object(self.last_query)
            if fallback_obj:
                filters["object"] = fallback_obj

        event = self.intent.get("event")
        if event:
            normalized = self._normalize_event(event)
            if normalized:
                filters["event"] = normalized

        track_id = self.intent.get("track_id")
        if isinstance(track_id, int):
            filters["track_id"] = track_id

        global_id = self.intent.get("global_id")
        if isinstance(global_id, int):
            filters["global_id"] = global_id

        camera_id = self.intent.get("camera_id")
        if isinstance(camera_id, int):
            filters["camera_id"] = camera_id
        else:
            fallback_cam = self._extract_camera_id(self.last_query)
            if fallback_cam is not None:
                filters["camera_id"] = fallback_cam

        zone = self.intent.get("zone")
        # The LLM returns zones as text ("2"); QueryEngine needs an int id.
        if isinstance(zone, str) and zone.strip().isdigit():
            zone = int(zone.strip())
        elif not isinstance(zone, int):
            zone = None
        zone_candidate = zone or self._zone_from_query(self.last_query.lower())
        if zone_candidate is not None:
            filters["zone_id"] = zone_candidate

        time_descriptor = self.intent.get("time")
        # The rule parser already stores an (start, end) hour tuple; only
        # free-text descriptors (e.g. from the LLM parser) need parsing.
        if (isinstance(time_descriptor, (tuple, list)) and len(time_descriptor) == 2
                and all(isinstance(h, int) for h in time_descriptor)):
            time_range = tuple(time_descriptor)
        else:
            if isinstance(time_descriptor, (tuple, list)):   # e.g. LLM ["2 PM", "3 PM"]
                time_descriptor = " to ".join(str(x) for x in time_descriptor)
            elif isinstance(time_descriptor, dict):           # e.g. {"start": .., "end": ..}
                time_descriptor = " to ".join(str(x) for x in time_descriptor.values())
            time_range = self._extract_time_frame(
                time_descriptor if isinstance(time_descriptor, str) and time_descriptor
                else self.last_query
            )
        if time_range:
            filters["time_range"] = time_range

        return filters

    def _rule_based_parse(self, query: str) -> Dict[str, Any]:
        parsed: Dict[str, Any] = {}
        lower_query = query.lower()
        tokens = re.split(r"\W+", lower_query)

        for token in tokens:
            if not token:
                continue

            obj = self.OBJECT_ALIAS.get(token)
            if obj:
                parsed["object"] = obj

            event = self.EVENT_ALIAS.get(token)
            if event:
                parsed["event"] = event

        track_id = self._extract_track_id(lower_query)
        if track_id is not None:
            parsed["track_id"] = track_id

        global_id = self._extract_global_id(lower_query)
        if global_id is not None:
            parsed["global_id"] = global_id

        camera_id = self._extract_camera_id(lower_query)
        if camera_id is not None:
            parsed["camera_id"] = camera_id

        zone_candidate = self._zone_from_query(lower_query)
        if zone_candidate is not None:
            parsed["zone"] = zone_candidate

        time_range = self._extract_time_frame(lower_query)
        if time_range:
            parsed["time"] = time_range

        if not parsed:
            parsed["query"] = query

        return parsed

    def _zone_from_query(self, query: str) -> Optional[int]:
        direct = self._fallback_zone(query)
        if direct is not None:
            return direct

        keyword_map = {
            "restricted": ["entry", "restricted"],
            "parking": ["parking"],
            "drop": ["drop-off", "dropoff", "drop"],
        }

        zones = get_all_zones()
        if not zones:
            return None

        for keyword, patterns in keyword_map.items():
            if keyword not in query:
                continue

            for zone in zones:
                name = zone.get("name", "").lower()
                if any(pattern in name for pattern in patterns):
                    return zone.get("id")

        return None

    def _normalize_object(self, value: str) -> Optional[str]:
        if not isinstance(value, str):  # LLM output may be a number/list/dict
            return None
        return self.OBJECT_ALIAS.get(value.lower())

    def _fallback_object(self, query: str) -> Optional[str]:
        tokens = re.split(r"\W+", query.lower())
        for token in tokens:
            normalized = self.OBJECT_ALIAS.get(token)
            if normalized:
                return normalized
        return None

    def _normalize_event(self, value: str) -> Optional[str]:
        if not isinstance(value, str):  # LLM output may be a number/list/dict
            return None
        lowered = value.lower()
        if lowered in self.EVENT_ALIAS:
            return self.EVENT_ALIAS[lowered]
        # Already-normalized values (e.g. "fall_detected" from the rule parser
        # or the LLM parser) must pass through, not be dropped.
        if lowered in self.EVENT_ALIAS.values():
            return lowered
        return None

    def _extract_track_id(self, query: str) -> Optional[int]:
        match = re.search(r"track(?:_?id)?\s*[:=#]?\s*(\d+)", query)
        if match:
            return int(match.group(1))
        return None

    def _extract_global_id(self, query: str) -> Optional[int]:
        # Accept "global id 5", "global_id=5", and the "GID 5" abbreviation the
        # dashboard shows everywhere ("gid 5", "gid#5", "g id 5").
        match = re.search(r"(?:global(?:[_\s]?id)?|g\s?id)\s*[:=#]?\s*(\d+)", query)
        if match:
            return int(match.group(1))
        return None

    def _extract_camera_id(self, query: str) -> Optional[int]:
        match = re.search(r"camera\s*(\d+)", query)
        if match:
            return int(match.group(1))
        return None

    def _fallback_zone(self, query: str) -> Optional[int]:
        match = re.search(r"zone\s*(\d+)", query)
        if match:
            return int(match.group(1))
        return None

    def _extract_time_frame(self, text: str) -> Optional[Tuple[int, int]]:
        if not text:
            return None

        text_lower = text.lower()
        for keyword, hours in self.TIME_KEYWORDS.items():
            if keyword in text_lower:
                return hours

        range_match = re.search(
            r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*(?:to|-)\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?",
            text_lower,
        )

        if range_match:
            start_hour = self._to_24h(
                int(range_match.group(1)), range_match.group(3)
            )
            end_hour = self._to_24h(
                int(range_match.group(4)), range_match.group(6)
            )
            return start_hour, end_hour

        single_match = re.search(r"at\s+(\d{1,2})(?:[:](\d{2}))?\s*(am|pm)?", text_lower)
        if single_match:
            hour = self._to_24h(int(single_match.group(1)), single_match.group(3))
            return hour, hour

        return None

    def _to_24h(self, hour: int, suffix: Optional[str]) -> int:
        hour = max(0, min(12, hour)) if hour <= 12 else max(0, min(23, hour))
        if suffix:
            suffix = suffix.lower()
            if suffix == "pm" and hour != 12:
                hour = (hour % 12) + 12
            elif suffix == "am" and hour == 12:
                hour = 0
        return max(0, min(23, hour))
