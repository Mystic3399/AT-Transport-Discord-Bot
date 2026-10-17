"""Isolated V2 staging mileage decisions. Never updates legacy mileage."""
SCHEMA = """
CREATE TABLE IF NOT EXISTS unified_staging_mileage_ledger (
 provider TEXT NOT NULL,
 provider_job_id TEXT NOT NULL,
 discord_user_id BIGINT NOT NULL REFERENCES unified_driver_profiles(discord_user_id),
 real_miles NUMERIC(12,2) NOT NULL DEFAULT 0,
 race_miles NUMERIC(12,2) NOT NULL DEFAULT 0,
 status TEXT NOT NULL CHECK(status IN ('review','credited','duplicate')),
 reviewed_by BIGINT,
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 PRIMARY KEY(provider,provider_job_id),
 FOREIGN KEY(provider,provider_job_id)
 REFERENCES unified_provider_job_events(provider,provider_job_id)
);
"""
