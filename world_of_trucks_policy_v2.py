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



def classify_job_mileage(provider, statistics, *, game, max_speed=None, speed_unit=None):
    """Apply the owner's ATS-only maximum-speed policy without guessing units.

    ATS 0–81 mph is Real; 82–112 mph is Race. The open fractional interval
    remains pending, with no rounding. ETS2 retains explicit provider labels.
    Invalid/road-trip provider classifications never become eligible jobs.
    This classifies evidence only; ownership/completion/dedup remain separate.
    """
    from decimal import Decimal, InvalidOperation
    if provider not in ('trucky', 'trucksbook') or game not in ('ATS','ETS2'):
        return None
    if game == 'ETS2':
        return classify_event_mileage(provider, statistics)
    if statistics is not None and classify_event_mileage('trucky', statistics) is None:
        return None
    if isinstance(max_speed,bool) or speed_unit not in ('mph','km/h','kph'):
        return None
    try:
        speed=Decimal(str(max_speed))
        if not speed.is_finite() or speed<0:
            return None
        mph=speed if speed_unit=='mph' else speed/Decimal('1.609344')
    except (InvalidOperation, ValueError, TypeError):
        return None
    kind='Real' if mph<=81 else 'Race' if 82<=mph<=112 else None
    if kind is None:
        return None
    wotr = isinstance(statistics,str) and 'wotr' in [s.strip().lower() for s in statistics.split(',')]
    return classify_event_mileage(provider, kind + (', WoTr' if wotr else ''))
