---
name: storymap-builder
description: Build an ArcGIS StoryMap from a topic, Web Maps and source data. Asks four scoping questions and shows three layout previews before generating anything, then produces a validated structured spec, checks its factual claims against the GIS data, and compiles it to StoryMaps JSON. Use when the user wants a StoryMap, a map-based narrative, or to turn GIS data plus an outline into a story.
---

# StoryMap Builder

Never generate the whole story first. Scope, preview, then build. The LLM writes a
**spec** (JSON), never raw StoryMaps JSON; code validates, fact-checks and compiles it.

## Step 0: Log in to ArcGIS first
Open the ArcGIS Online / StoryMaps sign-in page in the browser pane and ask the user to log in
themselves. Never type or request their username, password, or SSO credentials. Wait for them to
confirm, then check the session is live (e.g. the StoryMaps "My stories" page loads) before
anything else. Everything later (reading their layers, publishing a draft) depends on this session,
and sessions expire: if a later step lands on a sign-in page, stop and ask them to log in again.

## Step 1: Ask the scoping questions (one AskUserQuestion call)
1. **Purpose and audience**: course report, public explainer, or stakeholder briefing?
2. **Length**: how many sections (3, 5, 7+)? Each section can hold 0-N map slides.
3. **Data**: do they already have data, or nothing yet?
   - *Local data*: ask for the folder path, the study area, and what each dataset contains
     (format, fields, coordinate system). Inspect the files yourself rather than relying on a description.
   - *Already in ArcGIS Online*: ask for the Web Map / layer item ids.
   - *No data*: say so plainly and plan a sourcing pass: find open datasets for the topic and
     study area (city open-data portals, OpenStreetMap, census), record each source's name, URL
     and licence for the reference list, then clip to the study area. Confirm the shortlist with
     the user before downloading.
4. **Density**: one sentence per slide, short paragraph, or full prose?

Do not proceed on guesses; missing answers are questions, not defaults. Data that does not exist
cannot back a claim, so if the data in question 3 is thin, narrow the claims instead of inventing numbers.

## Step 2: Pick a layout before writing prose
Draft only titles and one sample slide, write them into a minimal spec, then:

```
python -m storymap_gen preview spec.json --out preview.html
```

Show `preview.html` (three layouts: `docked-sidecar`, `floating-sidecar`, `longform`) and
let the user choose. Theme is fixed to `slate`.

## Step 2b: Measure before you write
Before drafting any text about the data, get the facts from the data and put them in your context:
```
python -m storymap_gen facts --data <layer>=<geojson> --streets "BROADWAY" "NORTHERN BLVD" ...
```
Only state street-vs-grid claims for streets you have measured. In our experiment, models that skipped this
wrote wrong claims about 20% of the time (most often "Northern Blvd is diagonal"); with measured facts the
first draft passed in 10 of 10 runs.

## Step 3: Write the full spec
Schema: `storymap_gen/spec.py` (`StorySpec`). Rules:
- Every statement about the data (a count, an average, "street X is diagonal") becomes a
  `claims` entry (`street_orientation` or `layer_stat`) and is attached via `claim_ids`.
- Every section cites sources by key; every source is cited at least once.
- No dashes as sentence punctuation; keep slide text short.

## Step 4: Verify, compile, evaluate (one command)
```
python -m storymap_gen run spec.json --data <layer>=<geojson> --template <layout> --out story.json
```
Exit 0 means: spec valid, all claims PASS against the data, node graph valid, citations
consistent. On failure, **fix the spec or the wording, never the data or the story JSON**.
A `FAIL` claim means the sentence is wrong; rewrite it or pick the correct street/number.

## Step 5: Publish a draft
`story.json` is the item-data payload of a StoryMap item. `storymap_gen/publish.py` creates a
NEW private draft (never overwrites an existing story). It needs an OAuth token that the user
obtains by clicking Allow on the consent page; read it from `ARCGIS_TOKEN`, never ask for or
store passwords. Open the draft in the Builder to review; the user publishes it themselves.
