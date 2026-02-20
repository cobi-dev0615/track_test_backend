"""
ELD Log Sheet Generator

Takes a list of TripSegments and produces daily log sheet data
matching the standard DOT daily log format.

Each daily log covers midnight-to-midnight and contains:
- Date
- List of status entries with start/end times (in 24hr format)
- Total hours per duty status
- Total miles driven that day
"""

from datetime import datetime, timedelta
from collections import defaultdict


def generate_eld_logs(segments, trip_start_date=None):
    """
    Generate daily ELD log sheets from trip segments.

    Returns list of daily logs, each containing:
    - date: "YYYY-MM-DD"
    - entries: list of {status, start_hour, end_hour} (0-24 scale)
    - total_hours: {off_duty, sleeper_berth, driving, on_duty_not_driving}
    - total_miles: float
    - remarks: list of strings
    """
    if not segments:
        return []

    # Determine the date range
    first_start = segments[0].start_time
    last_end = segments[-1].end_time

    if trip_start_date:
        start_date = trip_start_date
    else:
        start_date = first_start.date()

    end_date = last_end.date()

    # Generate a log for each day
    logs = []
    current_date = start_date
    day_num = 0

    while current_date <= end_date:
        day_start = datetime(current_date.year, current_date.month, current_date.day)
        day_end = day_start + timedelta(days=1)

        day_entries = []
        day_miles = 0.0
        day_remarks = []
        hours_by_status = defaultdict(float)

        for seg in segments:
            # Check if segment overlaps with this day
            seg_start = max(seg.start_time, day_start)
            seg_end = min(seg.end_time, day_end)

            if seg_start >= seg_end:
                continue

            start_hour = (seg_start - day_start).total_seconds() / 3600
            end_hour = (seg_end - day_start).total_seconds() / 3600

            status = _map_duty_status(seg.duty_status)

            day_entries.append({
                "status": status,
                "start_hour": round(start_hour, 4),
                "end_hour": round(end_hour, 4),
                "segment_type": seg.segment_type,
            })

            duration = end_hour - start_hour
            hours_by_status[status] += duration

            # Calculate miles driven on this day for this segment
            if seg.duty_status == "driving" and seg.distance_miles > 0:
                seg_total_hours = seg.duration_hours()
                if seg_total_hours > 0:
                    fraction_on_day = duration / seg_total_hours
                    day_miles += seg.distance_miles * fraction_on_day

            # Add remarks for stops
            if seg.reason and seg_start >= seg.start_time:
                time_str = seg_start.strftime("%H:%M")
                day_remarks.append(f"{time_str} - {seg.reason}")

        # Fill gaps with off-duty
        day_entries = _fill_gaps(day_entries)

        # Recalculate hours with filled gaps
        hours_by_status = defaultdict(float)
        for entry in day_entries:
            duration = entry["end_hour"] - entry["start_hour"]
            hours_by_status[entry["status"]] += duration

        day_num += 1
        logs.append({
            "date": current_date.isoformat(),
            "day_number": day_num,
            "entries": day_entries,
            "total_hours": {
                "off_duty": round(hours_by_status.get("off_duty", 0), 2),
                "sleeper_berth": round(hours_by_status.get("sleeper_berth", 0), 2),
                "driving": round(hours_by_status.get("driving", 0), 2),
                "on_duty_not_driving": round(hours_by_status.get("on_duty_not_driving", 0), 2),
            },
            "total_miles": round(day_miles, 1),
            "remarks": day_remarks,
        })

        current_date += timedelta(days=1)

    return logs


def _map_duty_status(status: str) -> str:
    """Map internal status to standard ELD status names."""
    mapping = {
        "off_duty": "off_duty",
        "sleeper_berth": "sleeper_berth",
        "driving": "driving",
        "on_duty_not_driving": "on_duty_not_driving",
        "on_duty": "on_duty_not_driving",  # Alias
    }
    return mapping.get(status, "off_duty")


def _fill_gaps(entries):
    """Fill any gaps in the 24-hour day with off-duty status."""
    if not entries:
        return [{"status": "off_duty", "start_hour": 0, "end_hour": 24, "segment_type": "off_duty"}]

    # Sort by start time
    entries.sort(key=lambda e: e["start_hour"])
    filled = []
    current_hour = 0.0

    for entry in entries:
        if entry["start_hour"] > current_hour + 0.01:
            filled.append({
                "status": "off_duty",
                "start_hour": round(current_hour, 4),
                "end_hour": round(entry["start_hour"], 4),
                "segment_type": "off_duty",
            })
        filled.append(entry)
        current_hour = entry["end_hour"]

    if current_hour < 23.99:
        filled.append({
            "status": "off_duty",
            "start_hour": round(current_hour, 4),
            "end_hour": 24.0,
            "segment_type": "off_duty",
        })

    return filled
