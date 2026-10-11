# Writing guide: from outline to a story worth reading

The gate checks that every number is true. It cannot check that the story is interesting, specific or fair. This guide is the other half: how to turn an outline and a set of measured facts into text
that reads like the best district stories ([what they have in common](case-study-patterns.md)). A worked example that follows it: [examples/gracia_barcelona_full.json](../examples/gracia_barcelona_full.json).

## 1. Start from a thesis, not a topic
Before any paragraph, write one sentence that the whole story proves: *"Gràcia is a district of short distances, with three gaps in the north-east."* Not "This story is about Gràcia."
A thesis is something a measurement can support or contradict. Get it from `describe` and `explore`: the data usually suggests one (a split, a gap, an outlier). The title can carry it.

Run `storymap-gen findings` first: it measures reach, gaps, which half of the area holds what, concentration and how layers relate, and ranks them. Choosing the thesis from the top gaps and skews is how a story gets an argument instead of a list of counts.

## 2. Map the user's outline onto sections and subsections
- Each top-level line of the outline becomes a section (h2); each sub-point becomes a subsection (h3). If the outline is flat, split each section into two or three subsections yourself.
- Pick a framework when there is no outline: `systems` (introduction, hard systems, soft systems, interactions, proposal), `essay` (why this place, how we looked, what the data shows, two layers compared, what we learned) or `brief`.
- Keep the user's words for headings, and add specifics ("Parks" becomes "Thirteen percent, in three big pieces" once you know the number).
- Choose the depth with the user: `brief` (about 500 words, 3 maps), `standard` (about 1,500 words, 5 maps, 4 subsections), `full` (about 3,000 words, 7 maps, 8 subsections). The depth report warns when the story is below it.

## 3. Write each subsection in the same four moves
1. **Finding.** One sentence with a number the data backs. Lead with the finding, not with the background.
2. **Evidence.** A second or third sentence that qualifies it: where it is, what it is compared to, what the exceptions are.
3. **Map.** Place the map block directly after, with a caption that tells the reader what to look at ("Metro stations: dense in the south, absent in the north-east").
4. **Meaning.** One or two sentences on what it means for this place: what it supports, what it strains, what is unusual.

Keep paragraphs short: 40 to 100 words (the median in the studied stories is 41). After a paragraph or two, break the text with a map, a list, a swipe or a row of key figures.

## 4. Choose the claim that fits the sentence
| you want to say | claim | example |
|---|---|---|
| how many, how long, how large | `layer_stat`, `layer_length`, `layer_area` | "91.9 km of streets" |
| what share of a layer is of a kind | `layer_share` | "29% of street length is living streets" |
| how concentrated per area | `layer_density` | "42 bus stops per km2" |
| how much of the place is reached | `area_coverage` | "99.5% of the district is within 300 m of a bus stop" |
| how close two things are | `within_distance` | "every school is within 400 m of a park" |
| how a street relates to the grid | `street_orientation` | "this road cuts across the grid" |

Prefer the second and third rows to bare counts. A count alone ("6 stations") gives the reader nothing to weigh; the share of the district it reaches does.

## 5. Be specific about this place
- Name things. `describe` lists the longest streets, the largest parks and the station names; use them. "Parc Güell, the Collserola natural park and Parc de la Creueta del Coll hold 82% of the green area" beats "a few large parks hold most of it".
- Say where, not only how much: north or south of the centre, which side of the river. Check any direction you state against the data (`facts` for streets; the layer bounds for the rest).
- Say what is unusual compared with what a reader would expect, and only compare with something you measured.

## 5b. Compare the parts
When the place is several districts or tracts, a story that treats it as one lump hides the point. Use `subareas` and `aggregate` for per-part numbers and a graduated map to show them, and write one sentence per extreme (the highest, the lowest) with a claim on each. When the user has a table of figures, `join` it to a polygon layer and colour by it.

## 6. Do not claim what the data cannot show
- OpenStreetMap records what exists, not how well it works: it has bus stops, not frequency; schools, not capacity; no population, income or health.
- Read the layer definitions: `transit` holds stations and their entrances (filter to `railway == 'station'` before you count stations), `bike` holds streets that carry a lane, a track or a shared lane on at least one side (its length is the length of the whole street).
- If a subsection needs data you do not have (waste, demographics, culture), either ask the user for it or write "this part needs ..." and leave it out. Never write around a gap with plausible-sounding text.
- Qualitative statements are not checked by the gate. Keep them modest, and verify them yourself or leave them out ("lies north of the district's centre" can be checked; "feels calm" cannot).

## 7. Make maps tell the same story as the text
- A map shows what the sentence counts: if a claim counts stations, filter the map to stations (`"filters": {"transit": "railway == 'station'"}`).
- Use a **swipe** to compare two layers (green space against metro stations); layers on neither side, such as a street base, show on both.
- Caption every map. Say what to look at, not what the map is ("Living streets only: the 26.7 km where pedestrians have priority").
- Use `figures` (two to four big numbers) at the top of a section for the headline facts; each value is checked against a claim.

## 8. Pictures and links
- `storymap-gen context "<place>"` lists photographs of the place that may be reused, with the credit line and a description to start the alt text from, and a Wikipedia summary with a source entry. Look at each one before using it: a search result can be loosely related. History and names from Wikipedia are background to check, not facts to repeat blindly; cite the article.
- A picture needs `alt` text (what it shows, for readers who cannot see it), a caption and a credit with its licence. Use pictures you have the right to use (your own, or Wikimedia Commons with the licence stated).
- Link a source in the sentence with `[label](https://...)`, and list every source in `sources`. Only `https` links are kept.

## 9. End honestly
Close with what the evidence suggests and a **Limits** subsection: what the data does not show, where it is thin, what to add next (usually demographic data, to say who is served and who is not).

## 10. Check, then fix the warnings
```bash
storymap-gen run spec.json --manifest data/manifest.json
```
- `FAIL`, `UNBACKED`, `PLACEHOLDER`, `MAP`: the gate stops until they are fixed.
- `THIN`: the depth report. Treat each line as a to-do: a subsection with fewer than 40 words, a map with no caption, a section that states no measured fact, a claim never used in the text.

## Patterns to avoid
- A story that is a list of counts. Add a share, a reach or a comparison.
- A map that shows more than the sentence counts.
- A heading that repeats the outline ("Parks") when the number gives a better one.
- A paragraph over 150 words. Split it, or put the numbers in a list.
- Numbers repeated in three places without adding anything. Say it once, where it matters.
- Advice with no finding behind it. Every recommendation should point back at a number above.
