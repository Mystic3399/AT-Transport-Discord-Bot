"""Conservative canonical journey selection for staging.

No route/time fingerprint is a unique journey ID. Only an independently
verified shared delivery ID can merge providers automatically. Otherwise
retain provider-scoped IDs and require the reconciliation completeness gate.
"""
import re

def canonical_delivery_key(provider, provider_job_id, *, shared_journey_id=None,
                           shared_id_verified=False):
    if provider not in ("trucky", "trucksbook"):
        raise ValueError("Unsupported provider")
    if not isinstance(provider_job_id, str) or not provider_job_id.strip():
        raise ValueError("Provider job ID required")
    if shared_journey_id is not None:
        if not shared_id_verified:
            raise ValueError("Unverified shared journey identity cannot be used")
        if not isinstance(shared_journey_id, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", shared_journey_id
        ):
            raise ValueError("Invalid shared journey ID")
        return "shared:" + shared_journey_id
    # Explicitly separate provider namespaces. Do not claim these dedupe
    # the same physical trip across providers.
    return "provider:" + provider + ":" + provider_job_id
