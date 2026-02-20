from rest_framework import serializers


class TripRequestSerializer(serializers.Serializer):
    current_location = serializers.CharField(max_length=500)
    pickup_location = serializers.CharField(max_length=500)
    dropoff_location = serializers.CharField(max_length=500)
    current_cycle_used = serializers.FloatField(min_value=0, max_value=70)

    # Optional: pre-geocoded coordinates
    current_location_coords = serializers.DictField(required=False)
    pickup_location_coords = serializers.DictField(required=False)
    dropoff_location_coords = serializers.DictField(required=False)
