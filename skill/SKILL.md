---
name: storymap-gen
description: Build an ArcGIS StoryMap from a topic, Web Maps and GIS data, with every factual claim checked against the data before anything is compiled. Asks scoping questions and shows three layout previews first, measures the data before writing, then produces a validated spec, runs a quality gate, and creates a private draft. Use when the user wants a StoryMap or map-based narrative, or to turn GIS data plus an outline into a story.
---

# storymap-gen

Never generate the whole story first. **Scope, preview, measure, write, verify, draft.**
You write a **spec** (JSON), never raw StoryMaps JSON; code validates, fact-checks and compiles it.
Why this order: [docs/concepts.md](../docs/concepts.md).

## Install (once)
```bash
pip install "storymap-gen[mcp]"      # or: git clone https://github.com/hazelxue/storymap-gen && pip install -e ".[mcp]"
storymap-gen --help
```
If the MCP server is registered (`claude mcp add storymap-gen -- storymap-gen-mcp`, see docs/mcp.md), use its tools
`get_spec_schema`, `measure_street_orientation`, `verify_spec`, `compile_spec`, `preview_layouts`. Everything below works with
the CLI otherwise.

## Step 0: Log in to ArcGIS
Open the ArcGIS Online sign-in page in the browser pane and ask the user to log in themselves. Never type or request their
username, password or SSO credentials. Wait for their confirmation and check the session (the StoryMaps "My stories" page loads).
Sessions expire: if a later step lands on a sign-in page, stop and ask them to log in again.

## Step 1: Ask the scoping questions (one AskUserQuestion call)
1. **Purpose and audience**: course report, public explainer, or stakeholder briefing?
2. **Length**: how many sections (3, 5, 7+)? Each section can hold 0-N slides.
3. **Data**: do they already have data, or nothing yet?
   - *Local files*: ask for the folder, the study area, and what each dataset contains. Inspect the files yourself.
   - *Already in ArcGIS Online*: ask for the Web Map and layer item ids.
   - *Nothing yet*: say so, and plan a sourcing pass (open-data portals, OpenStreetMap, census). Record each source's name, URL and
     licence for the reference list, clip to the study area, and confirm the shortlist before downloading.
4. **Density and style**: one sentence per slide, a short paragraph, or full prose? Any style rules to follow?

Do not proceed on guesses. Data that does not exist cannot back a claim: if it is thin, narrow the claims; never invent numbers.

## Step 2: Pick a layout before writing prose
Write a minimal spec (titles and one sample slide), then:
```bash
storymap-gen preview spec.json --out preview.html
```
Show the three layouts (`docked-sidecar`, `floating-sidecar`, `longform`) and let the user choose. The sidecar layouts need a Web Map on every slide.

## Step 3: Measure before you write
Get facts from the data first, and put them in your context:
```bash
storymap-gen facts --data streets_centerline=streets.geojson --streets "BROADWAY" "NORTHERN BLVD"
```
Only claim that a street is diagonal or parallel to the grid for streets you measured. Without this step, about 20% of data claims were wrong in our
experiment (most often "Northern Blvd is diagonal"); with it, the first draft passed 10 of 10 times.
For cities without numbered streets, use `"grid_regex": null` and the layer's own `name_field` in orientation claims.

## Step 4: Write the full spec
Schema: [docs/spec.schema.json](../docs/spec.schema.json); claim types: [docs/claims.md](../docs/claims.md). Rules:
- Every statement about the data becomes a `claims` entry (`street_orientation`, `layer_stat`, `layer_area`, `within_distance`) attached through `claim_ids`.
- **Every number in the prose must be backed by a claim attached to that block or its section.** If you quote a number from a source
  rather than the data, list it in `allow_numbers`. Years, ordinals and street names are ignored.
- Every section cites at least one source, and every source is cited at least once.
- Follow the style rules from Step 1. Keep slide text short.

## Step 5: Verify, compile, gate (one command)
```bash
storymap-gen run spec.json --data <layer>=<file> ... --template <layout> --out story.json
```
Exit 0 means the gate passed: valid spec, all claims PASS, no unbacked numbers, citations consistent, node graph valid.
On failure, **fix the spec or the wording, never the data or the story JSON.** Each failure prints a `fix:` line.

| output | meaning | do this |
|---|---|---|
| `FAIL <claim>` | the sentence is wrong against the data | rewrite the sentence, or pick the street/number that is true |
| `ERROR <claim>` with "did you mean" | name not found in the layer | use the suggested spelling |
| `ERROR` with "grid_regex" | the grid definition matched no streets | set `grid_regex` (or `null`) for this city |
| `UNBACKED <n>` | a number in the prose has no claim | add a claim and attach it, round to a backed value, or use `allow_numbers` |
| `Citations: uncited sources` | a source is never cited | cite it in a section or remove it |
| `The spec is not valid` | schema or reference error | fix the field it names |

## Step 6: Draft in ArcGIS
```bash
export ARCGIS_TOKEN=...       # obtained by the user approving an OAuth consent; never ask for passwords
storymap-gen publish story.json --title "..." --username <user>
```
This creates a **new private draft** (it never overwrites a story). Open the draft in the StoryMaps Builder to review it; the user publishes it
themselves. Do not publish on the user's behalf.

## What the user sees
> **You:** scoping questions (purpose, length, data, density) &rarr; **User:** answers.
> **You:** "Here are three layouts" (preview.html) &rarr; **User:** picks docked-sidecar.
> **You:** "I measured the streets: Broadway is 37 degrees off the grid, Northern Blvd 0.1" &rarr; writes the spec.
> **You:** runs the gate; "one claim failed, I fixed the sentence and re-ran: passed" &rarr; creates a private draft and shares the link.
