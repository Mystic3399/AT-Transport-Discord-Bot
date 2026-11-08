# Tachograph driver profile

!profile and !profile @Driver display the front of the driver card. View Back and View Front update the same Discord message, reload the driver's existing credited mileage and retain the displayed driver's identity. Persistent button routing is registered at startup.

Use /country with autocomplete, or !country Australia, !country IE or !country CLEAR. Country is self-reported and is independent of account verification. Empty values show NOT SET.

The additive driver_card_preferences table stores country and truck by Discord user ID. Truck allocation is serialised with a PostgreSQL transaction advisory lock, chooses randomly among the least-used available models, and never overwrites an existing assignment. Existing linked drivers are allocated on startup and new links within five minutes. Opening a profile allocates immediately if necessary. This table has no membership deletion cascade.

Deploy this change on top of fix/dual-provider-mileage-desktop. It uses that branch's existing profile data reader and does not recalculate mileage. The schema is created idempotently during startup. Discord slash commands sync through the existing startup flow. discord.py 2.4 or later is required for persistent dynamic buttons.

The artwork is in assets/tachograph. The renderer loads the shield and country archives directly, avoiding a runtime extraction step. Truck artwork remains the generated portrait set. Steam is shown by verified Steam ID because the current identity schema stores no Steam display name. Drivers Hub shows Not linked until a supported account source supplies it.

Validation: pytest tests includes rendering rank boundaries, every truck, both shield states, country image dimensions, saved allocation reuse, balanced allocation and button reconstruction after restart. Live PostgreSQL migration and an actual Discord round trip still require deployment verification.
