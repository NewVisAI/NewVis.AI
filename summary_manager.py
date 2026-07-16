import os
import time
from typing import List, Dict, Optional
import alerts
from llm_parser import LLMParser

class SummaryManager:
    def __init__(self):
        self.llm_parser = None
        if os.environ.get("GROQ_API_KEY"):
            try:
                self.llm_parser = LLMParser()
            except Exception as e:
                print(f"⚠️ Failed to initialize Groq LLMParser for SummaryManager: {e}")

    def get_camera_summary(self, camera_id: int) -> str:
        """
        Gets a natural language AI summary of recent events/alerts on the specified camera.
        """
        # Fetch the last 30 alerts from all cameras, and filter by camera_id
        all_alerts = alerts.get_recent_alerts(limit=50)
        cam_alerts = [a for a in all_alerts if int(a.get("camera_id") or 0) == int(camera_id)]

        if not cam_alerts:
            return "No significant incidents or alerts detected on this camera recently. The area appears secure."

        # Format alerts for the summarizer
        incidents = []
        for a in cam_alerts:
            # Parse timestamp if it is full ISO or just time
            ts = a.get("timestamp") or ""
            if "T" in ts:
                # E.g. 2026-07-16T17:59:06 -> 17:59:06
                ts = ts.split("T")[-1].split(".")[0]
            
            incidents.append({
                "timestamp": ts,
                "global_id": a.get("global_id") or a.get("track_id") or 0,
                "type": a.get("alert_type") or "Alert",
                "description": a.get("message") or "Security event detected."
            })

        # Try LLM summarizer first
        if self.llm_parser and os.environ.get("GROQ_API_KEY"):
            try:
                summary = self.llm_parser.summarize_incidents(incidents[:10]) # Limit to last 10 to keep it concise
                if summary and "Error generating summary" not in summary:
                    return summary
            except Exception as e:
                print(f"⚠️ Groq summarization failed, falling back to rule-based: {e}")

        # Fallback rule-based summarizer
        return self._generate_rule_based_summary(incidents)

    def _generate_rule_based_summary(self, incidents: List[Dict]) -> str:
        """
        Generates a readable security summary by analyzing behavior counts and risk metrics.
        """
        types = {}
        high_risk_detected = False
        last_event_time = incidents[0]["timestamp"] if incidents else ""

        for inc in incidents:
            t = inc["type"]
            types[t] = types.get(t, 0) + 1
            if "restricted" in inc["description"].lower() or t in ["Intrusion", "Violence", "Fall"]:
                high_risk_detected = True

        # Build list of event counts
        counts_str = []
        for t, count in types.items():
            counts_str.append(f"{count} {t.lower()}(s)")

        summary_text = f"Recent Activity Summary (Last updated: {last_event_time}): "
        summary_text += f"Detected {', '.join(counts_str)} on this camera. "

        if high_risk_detected:
            summary_text += "⚠️ HIGH RISK behaviors observed. Immediate attention is recommended."
        else:
            summary_text += "Safety risk level remains LOW/MEDIUM. No critical safety threats detected."

        return summary_text

# Global singleton
_summary_manager = None

def get_summary(camera_id: int) -> str:
    global _summary_manager
    if _summary_manager is None:
        _summary_manager = SummaryManager()
    return _summary_manager.get_camera_summary(camera_id)
