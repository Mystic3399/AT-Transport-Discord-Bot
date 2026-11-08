# Tachograph country flags

254 individual RGBA PNGs at 99 × 54 pixels, matching the approved Union Jack slot. The archive includes 249 ISO entries and Kosovo, England, Northern Ireland, Scotland and Wales. The upstream 4:3 variants are centred without further stretching. The included countries.json maps supported codes to display names and paths.

Artwork source: [flag-icons](https://github.com/lipis/flag-icons/tree/ac7e0eb3259eb62aa6100c435607e2fe03896a97), MIT license included in the archive.

## Bot integration

1. Add an optional country_code field to the existing driver profile. Store uppercase keys from countries.json against the existing Discord user ID.
2. Provide a searchable country selection (for example /profile country with autocomplete), and a clear option. Validate the submitted code server-side against countries.json.
3. Extract the archive during packaging. Resolve the local PNG and name by code when rendering the card. Composite the flag at x=192, y=169 on the full approved reference coordinate system, or apply the equivalent offset for a cropped card. Use the same selected name under the role.
4. For unset or unknown codes, leave the flag slot empty and display NOT SET. Keep the company logo and its Union Jack unchanged.

Country selection is self-reported and independent of account verification, mileage and rank. The pack supplies artwork and metadata. The live Discord command and database field are integration work.
