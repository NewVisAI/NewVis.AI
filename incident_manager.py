import time
from typing import Dict, List, Optional, Tuple

class IncidentManager:
    """
    Manages threat detection and risk scoring for Sentinel AI.
    Analyzes behavior patterns like loitering, intrusion, and abnormal activity.
    """
    
    RISK_LEVELS = {
        "LOW": (0, 30),
        "MEDIUM": (31, 70),
        "HIGH": (71, 100)
    }
    
    LOITERING_THRESHOLD = 15.0  # seconds
    INTRUSION_THRESHOLD = 0.5   # seconds (immediate)
    
    def __init__(self):
        # global_id -> {score: int, behaviors: List[str], last_update: float}
        self.risk_profiles: Dict[int, Dict] = {}
        self.incidents: List[Dict] = []
        import threading
        self.lock = threading.Lock()
        
        # Zone types metadata (could be loaded from zones.json later)
        self.zone_metadata = {
            "restricted": {"multiplier": 2.5, "base_risk": 20},
            "entry": {"multiplier": 1.0, "base_risk": 0},
            "parking": {"multiplier": 1.2, "base_risk": 5},
            "default": {"multiplier": 1.0, "base_risk": 0}
        }

    def _get_zone_type(self, zone_name: str) -> str:
        name_lower = zone_name.lower()
        if "restricted" in name_lower or "vault" in name_lower or "server" in name_lower:
            return "restricted"
        if "entry" in name_lower or "gate" in name_lower:
            return "entry"
        if "parking" in name_lower:
            return "parking"
        return "default"

    def prune_stale_profiles(self) -> None:
        """Periodically removes profile records that have not been seen for over 10 minutes to prevent unbounded memory growth."""
        now = time.time()
        stale_ids = [gid for gid, prof in self.risk_profiles.items() if now - prof.get("last_seen", 0) > 600.0]
        for gid in stale_ids:
            self.risk_profiles.pop(gid, None)

    def update_risk(self, global_id: int, event_data: Dict) -> Dict:
        """
        Updates the risk profile for a global ID based on current activity.
        """
        with self.lock:
            self.prune_stale_profiles()
            if global_id not in self.risk_profiles:
                self.risk_profiles[global_id] = {
                    "score": 0,
                    "behaviors": set(),
                    "last_seen": time.time(),
                    "history": []
                }
        
            profile = self.risk_profiles[global_id]
            
            # Apply time-based risk decay: reduce risk score by 1 point per 10 seconds of inactivity
            now = time.time()
            elapsed = now - profile.get("last_seen", now)
            if elapsed > 10.0:
                decay_amount = int(elapsed // 10.0)
                profile["score"] = max(0, profile["score"] - decay_amount)
                # If risk decayed back to 0, clear registered behavior memory tags so they can be re-evaluated
                if profile["score"] == 0:
                    profile["behaviors"].clear()

            score = profile["score"]
            behaviors = profile["behaviors"]
            
            # 1. Zone-based risk
            zone_name = event_data.get("zone_name", "Unknown")
            zone_type = self._get_zone_type(zone_name)
            metadata = self.zone_metadata.get(zone_type, self.zone_metadata["default"])
            
            # 2. Loitering Detection
            duration = event_data.get("duration", 0.0)
            if duration > self.LOITERING_THRESHOLD:
                if "Loitering" not in behaviors:
                    behaviors.add("Loitering")
                    score += 20 * metadata["multiplier"]
                    self._add_incident(global_id, "Loitering", f"Person loitering in {zone_name}")

            # 3. Intrusion Detection
            if zone_type == "restricted" and duration > self.INTRUSION_THRESHOLD:
                if "Intrusion" not in behaviors:
                    behaviors.add("Intrusion")
                    score += 50 * metadata["multiplier"]
                    self._add_incident(global_id, "Intrusion", f"Unauthorized entry into {zone_name}")

            # 4. Temporal Risk (Night time)
            video_time_val = event_data.get("video_time")
            if video_time_val is not None:
                # Simple heuristic: map video_time to "hour" if we don't have absolute timestamps
                # Or just use system time if video_time doesn't represent actual hour
                current_hour = time.localtime().tm_hour 
            else:
                current_hour = time.localtime().tm_hour
                
            if current_hour < 6 or current_hour > 21:
                if "Night Activity" not in behaviors:
                    behaviors.add("Night Activity")
                    score += 15
            
            # Clamp score
            profile["score"] = min(100, int(score))
            profile["last_seen"] = now
            
            return {
                "score": profile["score"],
                "level": self.get_risk_level(profile["score"]),
                "behaviors": list(behaviors)
            }

    def get_risk_level(self, score: int) -> str:
        if score >= 71: return "HIGH"
        if score >= 31: return "MEDIUM"
        return "LOW"

    def _add_incident(self, global_id: int, type: str, description: str):
        self.incidents.append({
            "timestamp": time.strftime("%H:%M:%S"),
            "global_id": global_id,
            "type": type,
            "description": description
        })
        # Keep only last 50 incidents
        if len(self.incidents) > 50:
            self.incidents.pop(0)

    def get_recent_incidents(self, limit=5) -> List[Dict]:
        return self.incidents[-limit:][::-1]
