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
