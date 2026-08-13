"""Static schema + relationship guide for the SQL agent (never includes credentials)."""

from __future__ import annotations

SCHEMA_DESCRIPTION = """
You are generating read-only PostgreSQL for a UK taxi/PHV operations database.

TABLES AND COLUMNS
------------------
zones(
  zone_id TEXT PK,          -- e.g. Z0001
  city TEXT,                -- London, Manchester, Birmingham, Leeds, Liverpool
  zone_name TEXT,           -- e.g. Heathrow, Westminster
  latitude FLOAT,
  longitude FLOAT,
  zone_type TEXT            -- AIRPORT, URBAN, ...
)

drivers(
  driver_id TEXT PK,        -- e.g. DR-00001
  driver_name TEXT,
  city TEXT,
  licence_type TEXT,        -- Taxi, PHV
  licence_status TEXT,      -- ACTIVE, ...
  rating NUMERIC(3,2),
  years_experience INT,
  wheelchair_training BOOLEAN
)

vehicles(
  vehicle_id TEXT PK,       -- e.g. TX-00001
  registration TEXT,
  driver_id TEXT FK → drivers.driver_id,
  city TEXT,
  vehicle_type TEXT,        -- Saloon, Black Cab, Estate, ...
  make TEXT,
  model TEXT,
  fuel_type TEXT,
  seats INT,
  wheelchair_accessible BOOLEAN,
  zone_id TEXT FK → zones.zone_id,
  current_lat FLOAT,
  current_lon FLOAT,
  status TEXT,              -- AVAILABLE, BUSY, EN_ROUTE_PICKUP, MAINTENANCE, OFFLINE
  last_status_update TIMESTAMP
)

fares(
  city TEXT,
  vehicle_type TEXT,
  base_fare_gbp NUMERIC,
  per_mile_gbp NUMERIC,
  per_minute_gbp NUMERIC,
  PRIMARY KEY (city, vehicle_type)
)

trips(
  trip_id TEXT PK,
  vehicle_id TEXT FK → vehicles.vehicle_id,
  driver_id TEXT FK → drivers.driver_id,
  city TEXT,
  pickup_time TIMESTAMP,
  distance_miles NUMERIC,
  duration_minutes INT,
  dropoff_time TIMESTAMP,
  pickup_zone_id TEXT FK → zones.zone_id,
  pickup_zone TEXT,         -- denormalized zone name
  dropoff_zone_id TEXT FK → zones.zone_id,
  dropoff_zone TEXT,
  pickup_lat FLOAT,
  pickup_lon FLOAT,
  dropoff_lat FLOAT,
  dropoff_lon FLOAT,
  passenger_count INT,
  fare_gbp NUMERIC,
  payment_type TEXT,
  status TEXT               -- COMPLETED, ...
)

bookings(
  booking_id TEXT PK,
  trip_id TEXT FK → trips.trip_id,
  city TEXT,
  booking_time TIMESTAMP,
  requested_pickup_time TIMESTAMP,
  booking_channel TEXT,
  booking_status TEXT,      -- COMPLETED, CANCELLED, ...
  passenger_count INT
)

vehicle_events(
  event_id TEXT PK,
  vehicle_id TEXT FK → vehicles.vehicle_id,
  driver_id TEXT FK → drivers.driver_id,
  timestamp TIMESTAMP,
  city TEXT,
  zone_id TEXT FK → zones.zone_id,
  latitude FLOAT,
  longitude FLOAT,
  status TEXT
)

demand(
  timestamp TIMESTAMP,
  zone_id TEXT FK → zones.zone_id,
  city TEXT,
  zone TEXT,                -- zone name
  demand_requests INT,
  available_vehicles INT,
  unserved_requests INT,
  demand_index NUMERIC,
  PRIMARY KEY (timestamp, zone_id)
)

RELATIONSHIP GUIDANCE
---------------------
- "Available vehicles in <city>" → vehicles WHERE status = 'AVAILABLE' AND city = '<city>'
- "Near Heathrow" / zone names → JOIN vehicles.zone_id = zones.zone_id AND zones.zone_name = 'Heathrow'
  (Heathrow zone_id is Z0001 in London; prefer joining zones rather than hardcoding IDs)
- Wheelchair accessible → vehicles.wheelchair_accessible = TRUE
- Trips from a zone → trips.pickup_zone = 'Heathrow' OR trips.pickup_zone_id / JOIN zones
- Average trip distance/duration/fare → AVG(trips.distance_miles) / AVG(duration_minutes) / AVG(fare_gbp)
- Revenue → SUM(trips.fare_gbp)
- Highest demand city/zone → aggregate demand.demand_requests GROUP BY city or zone
- Driver performance → drivers joined with trips (counts, ratings)
- Booking cancellation rate → bookings WHERE booking_status = 'CANCELLED' / COUNT(*)

RULES
-----
1. Output ONE PostgreSQL SELECT (or WITH ... SELECT) only.
2. Never write INSERT/UPDATE/DELETE/DDL.
3. Prefer aggregates (COUNT/AVG/SUM) over returning raw rows.
4. Always LIMIT raw row listings to at most 50.
5. Use exact status values and boolean TRUE/FALSE.
6. Do not invent tables or columns.
""".strip()


ALLOWED_TABLES = frozenset(
    {
        "vehicles",
        "drivers",
        "trips",
        "bookings",
        "vehicle_events",
        "zones",
        "demand",
        "fares",
    }
)

INTENT_HINTS = (
    "vehicle_availability",
    "accessible_vehicles",
    "trip_lookup",
    "average_distance",
    "average_duration",
    "revenue",
    "demand",
    "top_zones",
    "driver_performance",
    "booking_cancellation",
    "general_query",
)
