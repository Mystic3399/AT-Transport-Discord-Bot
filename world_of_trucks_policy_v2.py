"""World of Trucks event classification policy for A&T mileage ingestion.

WoTr is a tag, never a mileage category. A job must be verified upstream
(provider ownership, completed status, distance, and duplicate review).
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class EventMileageClassification:
    provider: str
    mileage_type: str
    world_of_trucks: bool


def classify_event_mileage(provider: str, statistics: str):
    """Parse explicit classifications without inferring Real/Race from WoTr.

    Returns None for ambiguous, unsupported, or malformed classifications.
    TrucksBook Race is deliberately excluded from the unified mileage engine.
    """
    if provider not in ("trucksbook", "trucky") or not isinstance(statistics, str):
        return None
    labels = [part.strip().casefold() for part in statistics.split(",")]
    if not labels or any(not label for label in labels):
        return None
    if len(set(labels)) != len(labels):
        return None
    is_wotr = "wotr" in labels
    core = [label for label in labels if label != "wotr"]
    if len(core) != 1 or core[0] not in ("real", "race"):
        return None
    if provider == "trucksbook" and core[0] == "race":
        return None
    return EventMileageClassification(provider, core[0], is_wotr)



def exceeds_mileage_speed_limit(speed, unit):
    """Compare against the 112 mph ceiling without rounding."""
    from decimal import Decimal, InvalidOperation

    if isinstance(speed, bool) or unit not in ("mph", "km/h", "kph"):
        return False
    try:
        value = Decimal(str(speed))
    except (InvalidOperation, ValueError, TypeError):
        return False
    ceiling = Decimal("112") if unit == "mph" else Decimal("112") * Decimal("1.609344")
    return value.is_finite() and value > ceiling


def excluded_trucksbook_details(details):
    """Reject explicit excluded statuses and reported speeds above the ceiling."""
    import re

    for line in details.splitlines():
        label, _, value = line.replace("**", "").partition(":")
        label = label.strip().casefold()
        if label in ("status", "statistics") and any(
            part.strip().casefold() in ("cancelled", "canceled", "unlisted")
            for part in value.split(",")
        ):
            return True
        if label in ("maximal reached speed", "maximum speed", "max speed"):
            speed = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*(mph|km/h|kph)\s*", value, re.I)
            if speed and exceeds_mileage_speed_limit(speed[1], speed[2].lower()):
                return True
    return False


def normalize_vehicle_type(value):
    """Accept explicit authenticated vehicle categories, not cargo or names."""
    if value is None:
        return None
    if type(value) is not str:
        return "unknown"
    kind = value.strip().casefold().replace("-", "_").replace(" ", "_")
    if kind in {"bus", "coach", "motorcoach", "tourist_coach", "coach_bus"}:
        return "coach"
    if kind in {"car", "passenger_car", "sedan", "suv", "pickup", "pickup_truck",
                "sports_car", "muscle_car", "classic_car", "off_road_vehicle"}:
        return "car"
    if kind in {"truck", "tractor", "semi", "tractor_unit", "lorry"}:
        return "truck"
    return "unknown"


def authenticated_vehicle_type(job):
    """Read only explicit scalar types from the authenticated provider job.

    A car-cargo delivery is not proof that a car was driven. Conflicting or
    unknown typed evidence is held, never silently classed as an ordinary truck.
    """
    if not isinstance(job, dict):
        return "unknown"
    kinds = [normalize_vehicle_type(job[key])
             for key in ("vehicle_type", "vehicle_class", "vehicle_category")
             if key in job and job[key] is not None]
    if not kinds:
        return None
    return kinds[0] if len(set(kinds)) == 1 else "unknown"


def classify_job_mileage(provider, statistics, *, game, max_speed=None, speed_unit=None,
                         vehicle_type=None):
    """Apply A&T vehicle-specific Real/Race speed limits without rounding.

    ETS2 truck: 62 mph; ETS2 coach: 68 mph; ATS car: 85 mph.
    Existing ATS unspecified/truck evidence retains the prior 81 mph rule.
    Untyped ETS2 jobs retain provider classifications until vehicle evidence
    exists; only authenticated explicit vehicle type can receive exceptions.
    Every category is invalid above the independent 112 mph ceiling.

    This never establishes driver ownership or authorizes an award.
    """
    from decimal import Decimal, InvalidOperation

    if provider not in ("trucky", "trucksbook") or game not in ("ATS", "ETS2"):
        return None
    parsed = classify_event_mileage("trucky", statistics)
    if parsed is None or vehicle_type == "unknown":
        return None
    kind = normalize_vehicle_type(vehicle_type)
    if kind == "unknown" or (game == "ETS2" and kind == "car") or (
            game == "ATS" and kind == "coach"):
        return None

    if exceeds_mileage_speed_limit(max_speed, speed_unit):
        return None
    if game == "ETS2" and kind is None:
        # Preserve legacy verified TrucksBook/Trucky handling for jobs with
        # no authenticated vehicle category; do not infer a coach from cargo.
        return classify_event_mileage(provider, statistics)
    if isinstance(max_speed, bool) or speed_unit not in ("mph", "km/h", "kph"):
        return None
    try:
        speed = Decimal(str(max_speed))
        if not speed.is_finite() or speed < 0:
            return None
        mph = speed if speed_unit == "mph" else speed / Decimal("1.609344")
    except (InvalidOperation, ValueError, TypeError):
        return None

    if game == "ETS2":
        limit = Decimal("68") if kind == "coach" else Decimal("62")
    else:
        limit = Decimal("85") if kind == "car" else Decimal("81")

    category = "Real" if mph <= limit else "Race"
    wotr_suffix = ", WoTr" if parsed.world_of_trucks else ""
    # TrucksBook Race is never included in A&T mileage, even when supplied
    # with an explicit vehicle class or when its Real threshold is exceeded.
    return classify_event_mileage(provider, category + wotr_suffix)
