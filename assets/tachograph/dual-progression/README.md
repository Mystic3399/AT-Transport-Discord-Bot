# Real and Race card progression

The front has independent blue Real and amber Trucky Race progression rows. Each uses its own credited mileage, current shield, next rank and remaining distance. Both tracks use the existing 22 rank thresholds. Race badges retain the approved shield and add the reusable amber overlay in design-details/race-shield-overlay.webp.

The back repeats both tracks alongside the existing Real career statistics, permanent Real achievements and Hall of Fame. The Path to Immortal grid unlocks a shield when either track earns it. Cyan and amber markers identify the current rank for each track. An unavailable Race total shows Unavailable, while a credited zero starts at New Driver.

Names, totals, ranks, bars and locked states are rendered dynamically by tachograph.py. The preview images use example totals of 174,023 Real and 18,915 Race miles. The Discord front/back button continues to switch the two faces.

## November 2026 portrait and identity update

The front separates a bracketed numeric driver ID from the display name. `[001] CEO | Mystic` renders as driver number `001`, name `Mystic`; the Discord linked-account row retains the complete display name. Known job-title prefixes are removed from the card name only. The role still comes from current Discord roles. Unnumbered names remain supported, and no number is invented.

Monthly Real mileage has a 10,000-mile target and uses the same UK calendar-month boundaries and credited-job duplicate rules as the leaderboard. Pending and held mileage is excluded. The joining date is explicitly the Discord join date. The latest award and Real/Race club honours come from stored career awards. Preview figures are sample data.

All fleet portraits are dedicated blue/black A&T truck artwork, generated with the built-in image generator and packaged as WebP assets. Existing selection keys are preserved. New choices: Scania Streamline, Scania 164L, Mercedes MP6 Longline, Iveco S-Way Longline, Volvo FH16 Heavy Haul, Mercedes Actros Wrecker, Kenworth T680 and Peterbilt 389.

Asset prompt set: one portrait per named truck model; correct model cab, grille, mirrors, wheels and chassis; electric-blue/black livery with silver wolf graphics and small A&T visor lettering; close three-quarter automotive framing with the entire cab visible; turquoise aurora, mountain pines and wet reflective road; no extra text, borders or people. Longline, bonneted and recovery variants retain their respective body equipment.

## Connected Hub and native career controls

Driver cards now include a role-backed division ribbon, a selectable earned Real or Race club honour, monthly achievement percentage and recorded-history target streak, and monthly/all-time Real standing. `!change achievement 100k`, `!change achievement race 40k`, `!change achievement auto` and `!change division Recovery` update the current driver's preferences after eligibility checks.

Career Details is a native ephemeral Discord response, with five credited deliveries per page, Real/Race/game filters, best recorded month and job counts. Signed website lookups populate the Drivers Hub account row only for active, uniquely connected Hub identities. Native link buttons open the matching member profile on the existing website. The website projects credited records, supports filters and job details, and submits only the signed-in driver's appearance settings for bot validation. Preference revisions are acknowledged once, so a website save cannot repeatedly overwrite later Discord commands.

The Hub's View Tachograph action proxies a signed, authenticated request to the bot's existing HTTP service. It uses the same renderer and current data as Discord, without a public card-data endpoint or exposing service credentials.
