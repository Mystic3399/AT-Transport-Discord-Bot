# Tachograph assembly handoff

Use [the approved front/back mockup](design-reference.webp) to judge the assembled layout. The preview script uses sample data, the original badge library and a fallback typeface.

| Layer | Source | Assembly requirement |
| --- | --- | --- |
| Card shells and brand | backgrounds/front.webp, backgrounds/back.webp, company-logo.webp, design-details/footer-front.webp and footer-back.webp | Compare both shells with the approved reference. Keep the A&T branding fixed. |
| Front identity and portrait | design-details/smart-chip.webp, uk-map-watermark.webp, wolf-seal.webp, truck-window.webp; trucks/ | Use the truck-window crop for framing reference only: it contains the sample truck and plate. Check all 18 concept portraits against the approved fleet models and liveries. Persist each driver's assignment. |
| Linked accounts and stats | design-details/ account icons, verified-account-tick.webp, road-icon.webp, jobs-icon.webp, trophy-icon.webp, career-pin.webp | Render labels, values and verification ticks from verified account data. Draw panels, dividers, and progress fill separately. |
| Country | country-flags/ archive and country lookup | Use the 99 × 54 px asset next to the chip and the same country name in the identity block. Leave the slot blank if unset. |
| Ranks | rank-badges/shield-edition/ archive | Use the redesigned 22 pairs for dynamic slots. Use the approved crop grid and placements.csv to check position and fit. Crop labels and backgrounds are baked in, so use the separate badge masters for live progress. |
| Insignias | insignias/ standalone exports; design-details/ exact visual references | Match Verified Driver, Mileage Club, permanent achievements and Hall of Fame against the reference. Choose badges from stored eligibility. |
| Reverse | design-details/reverse-compass-seal.webp, tachograph-symbol.webp, footer-back.webp | Assemble current and next rank, career statistics, permanent achievements, Hall of Fame and Path to Immortal. |
| Typography | assets/profile/fonts/ | Preview uses Liberation Sans. Confirm the production face and title effects against the approved reference. |

Before production: check which exact crops need transparent replacements for clean compositing. Record front/back coordinates for all non-rank fields, in addition to the existing rank map. Render both faces with the new shield assets and realistic driver data, compare them at Discord display size, and verify front/back switching, missing profile values, rank boundaries and mileage source labels. The existing preview does not satisfy this acceptance check.
