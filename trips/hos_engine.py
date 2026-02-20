"""
Hours of Service (HOS) Calculation Engine

FMCSA rules for property-carrying drivers (70hr/8day cycle):
- 11-Hour Driving Limit: Max 11 hrs driving after 10 consecutive hrs off duty
- 14-Hour Window: Cannot drive beyond 14th consecutive hour after coming on duty
- 30-Minute Break: Required after 8 cumulative hours of driving
- 70-Hour/8-Day Cycle: Cannot drive after 70 hrs on-duty in 8 consecutive days
- 10-Hour Off Duty: Required reset before driving again

Assumptions:
- Average truck speed: 55 mph
- Fuel stop every 1,000 miles (30 min, on-duty not driving)
- 1 hour for pickup, 1 hour for dropoff (on-duty not driving)
- No adverse driving conditions
"""

from datetime import datetime, timedelta
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class DutyStatus(str, Enum):
    OFF_DUTY = "off_duty"
    SLEEPER = "sleeper_berth"
    DRIVING = "driving"
    ON_DUTY = "on_duty_not_driving"


class StopType(str, Enum):
    BREAK = "break"
    REST = "rest"
    FUEL = "fuel"
    PICKUP = "pickup"
    DROPOFF = "dropoff"


AVG_SPEED_MPH = 55
MAX_DRIVING_HOURS = 11.0
MAX_WINDOW_HOURS = 14.0
BREAK_AFTER_HOURS = 8.0
BREAK_DURATION_HOURS = 0.5
REST_DURATION_HOURS = 10.0
CYCLE_RESTART_HOURS = 34.0  # 34-hour restart to reset 70hr cycle
MAX_CYCLE_HOURS = 70.0
FUEL_INTERVAL_MILES = 1000
FUEL_STOP_DURATION_HOURS = 0.5
PICKUP_DURATION_HOURS = 1.0
DROPOFF_DURATION_HOURS = 1.0


@dataclass
class Location:
    lat: float
    lng: float
    name: str = ""


@dataclass
class TripSegment:
    segment_type: str  # "drive", "break", "rest", "fuel", "pickup", "dropoff"
    duty_status: str
    start_time: datetime
    end_time: datetime
    start_location: Optional[dict] = None
    end_location: Optional[dict] = None
    distance_miles: float = 0.0
    reason: str = ""

    def duration_hours(self):
        return (self.end_time - self.start_time).total_seconds() / 3600

    def to_dict(self):
        return {
            "segment_type": self.segment_type,
            "duty_status": self.duty_status,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "start_location": self.start_location,
            "end_location": self.end_location,
            "distance_miles": round(self.distance_miles, 1),
            "duration_hours": round(self.duration_hours(), 2),
            "reason": self.reason,
        }


@dataclass
class HOSState:
    """Tracks the current HOS state of the driver."""
    driving_hours: float = 0.0       # Hours driven since last 10hr rest
    window_hours: float = 0.0        # Hours since coming on duty (14hr window)
    hours_since_break: float = 0.0   # Driving hours since last 30min+ break
    cycle_hours: float = 0.0         # Total on-duty hours in cycle (70hr limit)
    on_duty: bool = False

    def remaining_driving(self):
        """Hours of driving left before any limit is hit."""
        by_driving = MAX_DRIVING_HOURS - self.driving_hours
        by_window = MAX_WINDOW_HOURS - self.window_hours
        by_break = BREAK_AFTER_HOURS - self.hours_since_break
        by_cycle = MAX_CYCLE_HOURS - self.cycle_hours
        return max(0, min(by_driving, by_window, by_break, by_cycle))

    def remaining_before_break(self):
        return max(0, BREAK_AFTER_HOURS - self.hours_since_break)

    def remaining_drive_limit(self):
        return max(0, MAX_DRIVING_HOURS - self.driving_hours)

    def remaining_window(self):
        return max(0, MAX_WINDOW_HOURS - self.window_hours)

    def remaining_cycle(self):
        return max(0, MAX_CYCLE_HOURS - self.cycle_hours)

    def needs_break(self):
        return self.hours_since_break >= BREAK_AFTER_HOURS

    def needs_rest(self):
        return (self.driving_hours >= MAX_DRIVING_HOURS or
                self.window_hours >= MAX_WINDOW_HOURS)

    def needs_cycle_reset(self):
        return self.cycle_hours >= MAX_CYCLE_HOURS

    def add_driving(self, hours):
        self.driving_hours += hours
        self.window_hours += hours
        self.hours_since_break += hours
        self.cycle_hours += hours
        self.on_duty = True

    def add_on_duty(self, hours):
        self.window_hours += hours
        self.cycle_hours += hours
        self.on_duty = True

    def take_break(self):
        self.hours_since_break = 0.0

    def take_rest(self):
        self.driving_hours = 0.0
        self.window_hours = 0.0
        self.hours_since_break = 0.0
        self.on_duty = False


def interpolate_location(start_loc, end_loc, fraction):
    """Linearly interpolate between two locations."""
    lat = start_loc["lat"] + (end_loc["lat"] - start_loc["lat"]) * fraction
    lng = start_loc["lng"] + (end_loc["lng"] - start_loc["lng"]) * fraction
    return {"lat": round(lat, 6), "lng": round(lng, 6), "name": ""}


def calculate_trip(
    route_legs,
    current_cycle_used: float,
    start_time: datetime = None,
):
    """
    Calculate a trip plan with HOS-compliant segments.

    route_legs: list of dicts with keys:
        - distance_miles: float
        - duration_hours: float  (estimated by routing engine)
        - start_location: {"lat": ..., "lng": ..., "name": ...}
        - end_location: {"lat": ..., "lng": ..., "name": ...}
        - leg_type: "drive_to_pickup", "drive_to_dropoff"

    Returns a list of TripSegment objects.
    """
    if start_time is None:
        start_time = datetime.utcnow().replace(second=0, microsecond=0)

    state = HOSState(cycle_hours=current_cycle_used)
    segments = []
    current_time = start_time

    for leg in route_legs:
        leg_type = leg.get("leg_type", "drive")
        start_loc = leg["start_location"]
        end_loc = leg["end_location"]
        total_distance = leg["distance_miles"]
        total_drive_time = total_distance / AVG_SPEED_MPH  # Use our speed, not routing estimate

        # Handle pickup/dropoff on-duty time
        if leg_type == "drive_to_pickup":
            # First, drive to pickup
            segments.extend(
                _plan_driving_segment(state, current_time, start_loc, end_loc, total_distance, total_drive_time)
            )
            current_time = segments[-1].end_time

            # Then do pickup (1 hr on-duty not driving)
            _check_and_add_rest_if_needed(state, segments, current_time, end_loc)
            current_time = segments[-1].end_time if segments else current_time

            pickup_seg = TripSegment(
                segment_type="pickup",
                duty_status=DutyStatus.ON_DUTY,
                start_time=current_time,
                end_time=current_time + timedelta(hours=PICKUP_DURATION_HOURS),
                start_location=end_loc,
                end_location=end_loc,
                reason="Pickup - Loading",
            )
            state.add_on_duty(PICKUP_DURATION_HOURS)
            segments.append(pickup_seg)
            current_time = pickup_seg.end_time

        elif leg_type == "drive_to_dropoff":
            # Drive to dropoff
            segments.extend(
                _plan_driving_segment(state, current_time, start_loc, end_loc, total_distance, total_drive_time)
            )
            current_time = segments[-1].end_time

            # Then do dropoff (1 hr on-duty not driving)
            _check_and_add_rest_if_needed(state, segments, current_time, end_loc)
            current_time = segments[-1].end_time if segments else current_time

            dropoff_seg = TripSegment(
                segment_type="dropoff",
                duty_status=DutyStatus.ON_DUTY,
                start_time=current_time,
                end_time=current_time + timedelta(hours=DROPOFF_DURATION_HOURS),
                start_location=end_loc,
                end_location=end_loc,
                reason="Dropoff - Unloading",
            )
            state.add_on_duty(DROPOFF_DURATION_HOURS)
            segments.append(dropoff_seg)
            current_time = dropoff_seg.end_time

    return segments


def _check_and_add_rest_if_needed(state, segments, current_time, location):
    """Add rest if the driver needs one before doing on-duty work."""
    if state.needs_cycle_reset():
        # 34-hour restart for cycle limit
        rest_seg = TripSegment(
            segment_type="rest",
            duty_status=DutyStatus.OFF_DUTY,
            start_time=current_time,
            end_time=current_time + timedelta(hours=CYCLE_RESTART_HOURS),
            start_location=location,
            end_location=location,
            reason="Required 34-hour restart (70hr cycle limit)",
        )
        segments.append(rest_seg)
        state.take_rest()
        state.cycle_hours = 0.0
    elif state.needs_rest():
        rest_seg = TripSegment(
            segment_type="rest",
            duty_status=DutyStatus.OFF_DUTY,
            start_time=current_time,
            end_time=current_time + timedelta(hours=REST_DURATION_HOURS),
            start_location=location,
            end_location=location,
            reason="Required 10-hour rest",
        )
        segments.append(rest_seg)
        state.take_rest()


def _plan_driving_segment(state, start_time, start_loc, end_loc, total_miles, total_hours):
    """
    Plan a driving segment from start to end, inserting breaks, rests, and fuel stops as needed.
    Returns list of TripSegment objects.
    """
    segments = []
    miles_remaining = total_miles
    current_time = start_time
    miles_since_fuel = 0.0

    while miles_remaining > 0.1:
        # Determine how long we can drive before hitting any limit
        drive_limit = state.remaining_driving()

        if drive_limit <= 0:
            fraction_done = 1 - (miles_remaining / total_miles) if total_miles > 0 else 0
            loc = interpolate_location(start_loc, end_loc, fraction_done)

            if state.needs_cycle_reset():
                # 70-hour cycle exhausted - need 34-hour restart
                rest_seg = TripSegment(
                    segment_type="rest",
                    duty_status=DutyStatus.OFF_DUTY,
                    start_time=current_time,
                    end_time=current_time + timedelta(hours=CYCLE_RESTART_HOURS),
                    start_location=loc,
                    end_location=loc,
                    reason="Required 34-hour restart (70hr cycle limit)",
                )
                segments.append(rest_seg)
                state.take_rest()
                state.cycle_hours = 0.0  # 34hr restart resets the cycle
                current_time = rest_seg.end_time
                continue
            elif state.needs_break() and not state.needs_rest():
                # Take a 30-minute break
                break_seg = TripSegment(
                    segment_type="break",
                    duty_status=DutyStatus.OFF_DUTY,
                    start_time=current_time,
                    end_time=current_time + timedelta(hours=BREAK_DURATION_HOURS),
                    start_location=loc,
                    end_location=loc,
                    reason="Required 30-minute break (8hr driving limit)",
                )
                segments.append(break_seg)
                state.take_break()
                current_time = break_seg.end_time
                continue
            else:
                # Need a full 10-hour rest (daily driving/window limit)
                rest_seg = TripSegment(
                    segment_type="rest",
                    duty_status=DutyStatus.OFF_DUTY,
                    start_time=current_time,
                    end_time=current_time + timedelta(hours=REST_DURATION_HOURS),
                    start_location=loc,
                    end_location=loc,
                    reason="Required 10-hour rest (driving/window limit)",
                )
                segments.append(rest_seg)
                state.take_rest()
                current_time = rest_seg.end_time
                continue

        # Calculate miles we can drive in the available time
        miles_can_drive = drive_limit * AVG_SPEED_MPH
        miles_this_segment = min(miles_remaining, miles_can_drive)

        # Check fuel stop
        miles_to_fuel = FUEL_INTERVAL_MILES - miles_since_fuel
        if miles_to_fuel <= 0:
            miles_to_fuel = FUEL_INTERVAL_MILES

        need_fuel = miles_this_segment >= miles_to_fuel and miles_remaining > miles_to_fuel

        if need_fuel:
            miles_this_segment = miles_to_fuel

        hours_this_segment = miles_this_segment / AVG_SPEED_MPH

        # Check if we need a 30-min break before completing this segment
        hours_to_break = state.remaining_before_break()
        if hours_this_segment > hours_to_break and hours_to_break > 0:
            # Drive until break needed
            miles_before_break = hours_to_break * AVG_SPEED_MPH
            if miles_before_break > 0.1:
                fraction_start = 1 - (miles_remaining / total_miles) if total_miles > 0 else 0
                fraction_end = 1 - ((miles_remaining - miles_before_break) / total_miles) if total_miles > 0 else 1
                seg_start = interpolate_location(start_loc, end_loc, fraction_start)
                seg_end = interpolate_location(start_loc, end_loc, fraction_end)

                drive_seg = TripSegment(
                    segment_type="drive",
                    duty_status=DutyStatus.DRIVING,
                    start_time=current_time,
                    end_time=current_time + timedelta(hours=hours_to_break),
                    start_location=seg_start,
                    end_location=seg_end,
                    distance_miles=miles_before_break,
                    reason="Driving",
                )
                segments.append(drive_seg)
                state.add_driving(hours_to_break)
                miles_remaining -= miles_before_break
                miles_since_fuel += miles_before_break
                current_time = drive_seg.end_time

            # Now take the break (possibly combined with fuel)
            fraction_done = 1 - (miles_remaining / total_miles) if total_miles > 0 else 1
            loc = interpolate_location(start_loc, end_loc, fraction_done)

            if need_fuel and abs(miles_since_fuel - FUEL_INTERVAL_MILES) < 100:
                # Combine break + fuel
                break_seg = TripSegment(
                    segment_type="fuel",
                    duty_status=DutyStatus.OFF_DUTY,
                    start_time=current_time,
                    end_time=current_time + timedelta(hours=BREAK_DURATION_HOURS),
                    start_location=loc,
                    end_location=loc,
                    reason="Fuel stop + 30-minute break",
                )
                miles_since_fuel = 0
            else:
                break_seg = TripSegment(
                    segment_type="break",
                    duty_status=DutyStatus.OFF_DUTY,
                    start_time=current_time,
                    end_time=current_time + timedelta(hours=BREAK_DURATION_HOURS),
                    start_location=loc,
                    end_location=loc,
                    reason="Required 30-minute break (8hr driving limit)",
                )
            segments.append(break_seg)
            state.take_break()
            current_time = break_seg.end_time
            continue

        # Drive this segment
        fraction_start = 1 - (miles_remaining / total_miles) if total_miles > 0 else 0
        fraction_end = 1 - ((miles_remaining - miles_this_segment) / total_miles) if total_miles > 0 else 1
        seg_start = interpolate_location(start_loc, end_loc, fraction_start)
        seg_end = interpolate_location(start_loc, end_loc, fraction_end)

        drive_seg = TripSegment(
            segment_type="drive",
            duty_status=DutyStatus.DRIVING,
            start_time=current_time,
            end_time=current_time + timedelta(hours=hours_this_segment),
            start_location=seg_start,
            end_location=seg_end,
            distance_miles=miles_this_segment,
            reason="Driving",
        )
        segments.append(drive_seg)
        state.add_driving(hours_this_segment)
        miles_remaining -= miles_this_segment
        miles_since_fuel += miles_this_segment
        current_time = drive_seg.end_time

        # Add fuel stop if needed
        if need_fuel and miles_remaining > 0.1:
            loc = seg_end
            fuel_seg = TripSegment(
                segment_type="fuel",
                duty_status=DutyStatus.ON_DUTY,
                start_time=current_time,
                end_time=current_time + timedelta(hours=FUEL_STOP_DURATION_HOURS),
                start_location=loc,
                end_location=loc,
                reason="Fuel stop",
            )
            segments.append(fuel_seg)
            state.add_on_duty(FUEL_STOP_DURATION_HOURS)
            miles_since_fuel = 0
            current_time = fuel_seg.end_time

    return segments
