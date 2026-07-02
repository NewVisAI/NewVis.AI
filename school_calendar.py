import json
import os
from datetime import date
from typing import List

CALENDAR_PATH = "school_calendar.json"


def _ensure_file_exists() -> None:
    if not os.path.exists(CALENDAR_PATH):
        with open(CALENDAR_PATH, "w", encoding="utf-8") as handle:
            json.dump({"holidays": []}, handle, indent=2)


def _load() -> List[str]:
    _ensure_file_exists()
    try:
        with open(CALENDAR_PATH, encoding="utf-8") as handle:
            payload = json.load(handle)
    except json.JSONDecodeError:
        return []
    return sorted(set(payload.get("holidays", [])))


def _save(holidays: List[str]) -> None:
    with open(CALENDAR_PATH, "w", encoding="utf-8") as handle:
        json.dump({"holidays": sorted(set(holidays))}, handle, indent=2)


def list_holidays() -> List[str]:
    return _load()


def add_holiday(date_str: str) -> bool:
    try:
        date.fromisoformat(date_str)
    except ValueError:
        return False

    holidays = _load()
    if date_str not in holidays:
        holidays.append(date_str)
        _save(holidays)
    return True


def remove_holiday(date_str: str) -> bool:
    holidays = _load()
    if date_str not in holidays:
        return False
    holidays.remove(date_str)
    _save(holidays)
    return True


def is_holiday(day: date) -> bool:
    return day.isoformat() in _load()


def configure_holidays_interactive() -> None:
    while True:
        holidays = list_holidays()
        print("\n📅 School Holidays (non-school days, applies to all zones)")
        if holidays:
            for idx, holiday in enumerate(holidays, start=1):
                print(f"  {idx}. {holiday}")
        else:
            print("  (none configured)")

        print("\n  a) Add a holiday date   r) Remove a holiday date   b) Back")
        choice = input("  Choice: ").strip().lower()

        if choice == "a":
            date_str = input("    Enter date (YYYY-MM-DD): ").strip()
            if add_holiday(date_str):
                print(f"    ✅ Added {date_str} as a non-school day.")
            else:
                print("    ⚠️ Invalid date format, expected YYYY-MM-DD.")
        elif choice == "r":
            date_str = input("    Enter date to remove (YYYY-MM-DD): ").strip()
            if remove_holiday(date_str):
                print(f"    🗑️ Removed {date_str}.")
            else:
                print("    ⚠️ Date not found in the holiday list.")
        elif choice in ("b", ""):
            return
        else:
            print("    ⚠️ Invalid choice.")
