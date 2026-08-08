"""
Graph view over the events/alerts tables + camera topology, for questions a flat
WHERE-clause search can't answer: multi-hop person traces, recurring actors across
alerts weeks apart, and cascading incidents across adjacent cameras.

Nodes: ("person", global_id) | ("camera", camera_id) | ("alert", alert_id)
Edges:
  SEEN_AT      person -> camera   (from `events` rows: an ordinary tracking sighting)
  TRIGGERED    person -> alert    (from `alerts` rows)
  AT_CAMERA    alert  -> camera
  ADJACENT_TO  camera -> camera   (from camera_registry.get_topology(), bidirectional)

Read-only and rebuilt per call - alerts/events volume is small relative to raw
tracking_data, so there's no persisted graph to keep in sync.
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import networkx as nx

import camera_registry
from db_schema import adapt_query, connect_db


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


class EventGraph:
    def __init__(self, conn=None):
        self._graph = self._build(conn)

    def _build(self, conn=None) -> nx.MultiDiGraph:
        graph = nx.MultiDiGraph()

        for edge in camera_registry.get_topology():
            a, b = edge.get("a"), edge.get("b")
            if a is None or b is None:
                continue
            graph.add_edge(("camera", a), ("camera", b), kind="ADJACENT_TO")
            graph.add_edge(("camera", b), ("camera", a), kind="ADJACENT_TO")

        own_conn = conn is None
        conn = conn or connect_db(validate_schema=False)
        cursor = conn.cursor()

        cursor.execute(adapt_query(
            "SELECT global_id, camera_id, zone_id, entry_time FROM events "
            "WHERE global_id IS NOT NULL AND camera_id IS NOT NULL AND entry_time IS NOT NULL"
        ))
        for global_id, camera_id, zone_id, entry_time in cursor.fetchall():
            person, camera = ("person", global_id), ("camera", camera_id)
            graph.add_edge(person, camera, kind="SEEN_AT", ts=entry_time, zone_id=zone_id)

        cursor.execute(adapt_query(
            "SELECT id, timestamp, alert_type, camera_id, global_id, zone_id, zone_name FROM alerts"
        ))
        for alert_id, ts, alert_type, camera_id, global_id, zone_id, zone_name in cursor.fetchall():
            alert, camera = ("alert", alert_id), ("camera", camera_id)
            graph.add_node(alert, kind="alert", type=alert_type, ts=ts, camera_id=camera_id,
                            zone_id=zone_id, zone_name=zone_name)
            graph.add_edge(alert, camera, kind="AT_CAMERA")
            if global_id is not None:
                graph.add_edge(("person", global_id), alert, kind="TRIGGERED", ts=ts)

        if own_conn:
            conn.close()
        return graph

    def trace(self, global_id: int, before: Any, window_seconds: float = 7200) -> List[Dict]:
        """Everywhere `global_id` was seen in the `window_seconds` before `before`."""
        before_ts = before if isinstance(before, datetime) else _parse_ts(before)
        person = ("person", global_id)
        if before_ts is None or person not in self._graph:
            return []
        window_start = before_ts - timedelta(seconds=window_seconds)

        trail = []
        for _, camera, data in self._graph.out_edges(person, data=True):
            if data.get("kind") != "SEEN_AT":
                continue
            ts = _parse_ts(data.get("ts"))
            if ts and window_start <= ts <= before_ts:
                trail.append({"ts": ts.isoformat(), "camera_id": camera[1], "zone_id": data.get("zone_id")})
        trail.sort(key=lambda row: row["ts"])
        return trail

    def recurring_actors(self, min_alerts: int = 2, min_span_seconds: float = 86400) -> List[Dict]:
        """Persons linked to >= min_alerts alerts spanning >= min_span_seconds."""
        results = []
        for node, data in self._graph.nodes(data=True):
            if data.get("kind") == "alert" or not isinstance(node, tuple) or node[0] != "person":
                continue
            triggered = sorted(
                (
                    (parsed, alert)
                    for _, alert, edata in self._graph.out_edges(node, data=True)
                    if edata.get("kind") == "TRIGGERED" and (parsed := _parse_ts(edata.get("ts")))
                ),
                key=lambda row: row[0],
            )
            if len(triggered) < min_alerts:
                continue
            span = (triggered[-1][0] - triggered[0][0]).total_seconds()
            if span < min_span_seconds:
                continue
            results.append({
                "global_id": node[1],
                "alert_count": len(triggered),
                "span_seconds": span,
                "alerts": [
                    {"alert_id": alert[1], "type": self._graph.nodes[alert].get("type"), "ts": ts.isoformat()}
                    for ts, alert in triggered
                ],
            })
        results.sort(key=lambda row: row["span_seconds"], reverse=True)
        return results

    def cascades(self, alert_id: int, max_hops: int = 2, window_seconds: float = 600) -> List[Dict]:
        """Other alerts within `max_hops` camera-hops and `window_seconds` of `alert_id`."""
        alert = ("alert", alert_id)
        if alert not in self._graph:
            return []
        anchor = self._graph.nodes[alert]
        anchor_ts = _parse_ts(anchor.get("ts"))
        anchor_camera = ("camera", anchor.get("camera_id"))
        if anchor_ts is None or anchor_camera not in self._graph:
            return []

        camera_nodes = [n for n, d in self._graph.nodes(data=True) if isinstance(n, tuple) and n[0] == "camera"]
        camera_graph = self._graph.subgraph(camera_nodes)
        nearby = nx.single_source_shortest_path_length(camera_graph, anchor_camera, cutoff=max_hops)

        results = []
        for node, data in self._graph.nodes(data=True):
            if data.get("kind") != "alert" or node == alert:
                continue
            camera = ("camera", data.get("camera_id"))
            if camera not in nearby:
                continue
            ts = _parse_ts(data.get("ts"))
            if ts is None:
                continue
            delta = abs((ts - anchor_ts).total_seconds())
            if delta <= window_seconds:
                results.append({
                    "alert_id": node[1],
                    "type": data.get("type"),
                    "camera_id": data.get("camera_id"),
                    "seconds_from_anchor": delta,
                    "camera_hops": nearby[camera],
                })
        results.sort(key=lambda row: row["seconds_from_anchor"])
        return results
