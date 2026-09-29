# AT-Transport-Discord-Bot

The A&T Transport LTD Discord bot manages driver onboarding, mileage ranks,
achievements, leaderboards, and decorative role-category dividers.

## Automatic role-category dividers

When a member receives a functional role, the bot automatically adds the
matching decorative divider role. It removes the divider only when the member
no longer has any role in that category. Existing members are repaired whenever
the bot starts.

The bot account requires **Manage Roles**, and its highest role must sit above
the seven divider roles in the Discord role hierarchy. Administrator permission
is not required for this feature.
