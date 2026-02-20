from datetime import datetime

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .serializers import TripRequestSerializer
from .route_service import geocode, geocode_autocomplete, get_route
from .hos_engine import calculate_trip
from .eld_generator import generate_eld_logs


@api_view(["POST"])
def plan_trip(request):
    """Plan a trip with HOS-compliant route and generate ELD logs."""
    serializer = TripRequestSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    data = serializer.validated_data

    try:
        current_loc = data.get("current_location_coords") or geocode(data["current_location"])
        pickup_loc = data.get("pickup_location_coords") or geocode(data["pickup_location"])
        dropoff_loc = data.get("dropoff_location_coords") or geocode(data["dropoff_location"])

        if not current_loc.get("name"):
            current_loc["name"] = data["current_location"]
        if not pickup_loc.get("name"):
            pickup_loc["name"] = data["pickup_location"]
        if not dropoff_loc.get("name"):
            dropoff_loc["name"] = data["dropoff_location"]

        route_to_pickup = get_route(current_loc, pickup_loc)
        route_to_dropoff = get_route(pickup_loc, dropoff_loc)

        route_legs = [
            {
                "start_location": current_loc,
                "end_location": pickup_loc,
                "distance_miles": route_to_pickup["distance_miles"],
                "duration_hours": route_to_pickup["duration_hours"],
                "leg_type": "drive_to_pickup",
            },
            {
                "start_location": pickup_loc,
                "end_location": dropoff_loc,
                "distance_miles": route_to_dropoff["distance_miles"],
                "duration_hours": route_to_dropoff["duration_hours"],
                "leg_type": "drive_to_dropoff",
            },
        ]

        start_time = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
        segments = calculate_trip(
            route_legs=route_legs,
            current_cycle_used=data["current_cycle_used"],
            start_time=start_time,
        )

        eld_logs = generate_eld_logs(segments)

        stops = []
        for seg in segments:
            if seg.segment_type in ("break", "rest", "fuel", "pickup", "dropoff"):
                stops.append({
                    "type": seg.segment_type,
                    "location": seg.start_location,
                    "start_time": seg.start_time.isoformat(),
                    "end_time": seg.end_time.isoformat(),
                    "duration_hours": round(seg.duration_hours(), 2),
                    "reason": seg.reason,
                })

        total_miles = sum(s.distance_miles for s in segments if s.segment_type == "drive")
        total_driving_hours = sum(s.duration_hours() for s in segments if s.segment_type == "drive")
        total_trip_hours = (segments[-1].end_time - segments[0].start_time).total_seconds() / 3600 if segments else 0

        return Response({
            "trip_summary": {
                "total_miles": round(total_miles, 1),
                "total_driving_hours": round(total_driving_hours, 2),
                "total_trip_hours": round(total_trip_hours, 2),
                "number_of_stops": len(stops),
                "number_of_days": len(eld_logs),
                "start_time": segments[0].start_time.isoformat() if segments else None,
                "end_time": segments[-1].end_time.isoformat() if segments else None,
            },
            "locations": {
                "current": current_loc,
                "pickup": pickup_loc,
                "dropoff": dropoff_loc,
            },
            "route_geometry": {
                "to_pickup": route_to_pickup["geometry"],
                "to_dropoff": route_to_dropoff["geometry"],
            },
            "segments": [s.to_dict() for s in segments],
            "stops": stops,
            "eld_logs": eld_logs,
        })

    except ValueError as e:
        return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        return Response(
            {"error": f"Trip planning failed: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["GET"])
def autocomplete(request):
    """Geocoding autocomplete for location search."""
    query = request.query_params.get("q", "").strip()
    if len(query) < 3:
        return Response([])
    try:
        results = geocode_autocomplete(query)
        return Response(results)
    except Exception:
        return Response([])
