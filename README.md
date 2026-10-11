# storymap-gen

![CI](https://github.com/hazelxue/storymap-gen/actions/workflows/ci.yml/badge.svg)

**Give it a place and a question. Get an ArcGIS StoryMap with maps, built from real data, where every number is checked.**

![A terminal replay made from the real output of the commands: describe the data, list the findings, a failing quality gate, a passing one](docs/demo/terminal-demo.svg)

*An animated replay of real output, rebuilt by `python scripts/make_demo_svg.py`. A script for recording the live demo: [docs/demo/demo-script.md](docs/demo/demo-script.md).*

## Get started

**New to this? Follow the [step-by-step guide with screenshots](docs/step-by-step.md)** (install, a one-time ArcGIS setup, sign in, make a story, publish a private draft; each step ends with what you should see).

Version 0.5. [What it does and where it stops](docs/getting-started.md#what-it-does-and-where-it-stops) · [Tell us how it went](https://github.com/hazelxue/storymap-gen/issues/new/choose)

**1. Try it in two minutes** (Python 3.10 or newer, no account, no internet needed):
```bash
git clone https://github.com/hazelxue/storymap-gen
cd storymap-gen
python -m pip install -e .
python -m storymap_gen run examples/gracia_barcelona_full.json --manifest fixtures/gracia/manifest.json
python -m storymap_gen explore --manifest fixtures/gracia/manifest.json --open
```
That checks 46 claims about a Barcelona district against real data and opens a map of the data in your browser.

**2. Make a story about your own place** (the easy way, with [Claude Code](https://claude.com/claude-code)):
```bash
python -m pip install -e ".[mcp]"          # inside the folder you cloned: adds the Claude Code (MCP) support
python scripts/install_skill.py            # copies the skill into ~/.claude/skills/storymap-gen
```
Then, in Claude Code: *"Make a StoryMap about transport in Hexi District, Tianjin."* It asks for the place, shows you the data, asks for your outline, writes the story, checks every number, and creates a **private draft** in your ArcGIS account if you want one.
No Claude Code? Every step is also a command: [docs/getting-started.md](docs/getting-started.md).

**3. Put it in ArcGIS** needs a one-time setup (an OAuth credential item in your ArcGIS Online account, three minutes, with screenshots in the [step-by-step guide](docs/step-by-step.md#step-5-one-time-arcgis-setup-so-the-tool-may-sign-in-as-you)). After that, signing in is `storymap-gen login`: a browser tab opens, you click Allow, nothing to copy. The tool never sees your password and never publishes for you.

**Stuck, or it worked? Tell us**: [open the short feedback form](https://github.com/hazelxue/storymap-gen/issues/new/choose) (no code needed; a story that failed is as useful as one that worked). Pull requests are welcome but never required ([CONTRIBUTING.md](CONTRIBUTING.md)).

## What it is
You say where (a neighbourhood or district anywhere), what you want to understand, and optionally an outline. The data can be your own
files, public data downloaded for you (OpenStreetMap, any public ArcGIS layer), or both. A language model writes the story as a typed
spec; storymap-gen measures the data, rejects any claim or number the data does not support, builds the maps, and creates a private draft in your ArcGIS account.

Why the checking matters: a language model writes fluent map stories, and about one in five of the data claims it made in our experiment was wrong
(most often calling Northern Boulevard "diagonal to the grid"; measured, it runs parallel to the avenues).

```text
$ storymap-gen run examples/jackson_heights_broken.json --data streets_centerline=fixtures/streets_centerline.geojson
Claims (3)
  FAIL  northern-diagonal: NORTHERN BLVD: median deviation from grid axis = 0.1 deg (grid axis 81.3 deg, tolerance 15.0 deg) -> parallel; claim says diagonal_to_grid
        fix: NORTHERN BLVD measures as parallel: set relation to 'parallel_to_grid', or choose a street that really is diagonal
  ERROR broadway-diagonal: street 'BROADWY' not found in layer 'streets_centerline'
        fix: did you mean: BROADWAY?
  PASS  avg-width: mean(st_width) = 32.62, claimed 32.6 +/- 0.1
Numbers in prose: 1 NOT backed
  UNBACKED 35 in section 'Streets' intro
        fix: attach a claim that states the number (claim_ids), or list it in allow_numbers
Citations: ok (sections with sources: 1/1)
Depth (standard): 13 words, 1 sections, 0 subsections, 0 maps (0 swipes), 0 key-figure rows, 0 pictures, 3 claims
Quality gate FAILED; nothing written.
```

## The five steps (what the Claude Code skill walks you through)
1. **Sign in to ArcGIS.** `storymap-gen login`: a browser tab opens, you click Allow, done (nothing to copy; the tool never sees your password). `storymap-gen status` shows whether you are signed in.
2. **Choose the study area.** A place name becomes a boundary: `storymap-gen region "Gràcia, Barcelona"`.
3. **Look at the data.** `fetch` downloads public data (or you give files), `describe` lists what is in it, `explore` draws it on a map in your browser, and `findings` measures what is notable (where a service does not reach, which half of the area holds what, ...) as claims that already pass.
4. **Give an outline.** Pasted, or a framework to start from (`systems`, `essay`, `brief`); with a question or thesis and a depth (`brief`, `standard`, `full`).
5. **Get the story.** The skeleton is written to your outline, filled in with measured facts, checked, and created as a private draft in your ArcGIS account.

```bash
storymap-gen region "Park Slope, Brooklyn" --out region.json                 # place name -> boundary (several names = one combined area)
storymap-gen fetch --region region.json --layers streets parks transit --out data/   # public data, cut to the boundary
storymap-gen describe --manifest data/manifest.json                          # what is really in the data, with named highlights
storymap-gen explore --manifest data/manifest.json --open                    # look at it on a map
storymap-gen findings --manifest data/manifest.json --claims-out findings.json   # what is notable, with verified claims
storymap-gen context "Park Slope, Brooklyn" --out context.json            # Wikipedia background and reusable pictures, with credits
storymap-gen init --region region.json --topic "Green space in Park Slope" --manifest data/manifest.json --outline outline.md --out spec.json
#  ... write the story (a model, or you): replace every TODO, back every number with a claim ...
storymap-gen run spec.json --manifest data/manifest.json                     # verify, build, quality gate, depth report
storymap-gen login                                                           # a browser tab opens: click Allow
storymap-gen publish-spec spec.json --manifest data/manifest.json --title "Green space in Park Slope" --download-images   # --download-images only if the story has pictures
```
Use your own data instead (or as well) with `--data name=file.geojson`. The easiest way to drive all of this is the **Claude Code skill**
([`skill/SKILL.md`](skill/SKILL.md)): it asks for the place, topic, outline and data source, fetches and inspects the data, drafts, verifies, and hands you a draft link.

## What a story can contain
Sections with subsections, paragraphs, bullet lists, quotes, maps beside the text, **graduated-colour maps** (colour areas by a number: your own table joined with `join`, or per-district measures from `aggregate`; see [docs/thematic-maps.md](docs/thematic-maps.md)), **swipe** maps that compare two layers, rows of key numbers, pictures (with alt text, caption and credit), links, and a numbered source list.
Guidance for writing it well, drawn from a study of 31 public district StoryMaps: [docs/writing-guide.md](docs/writing-guide.md) and [docs/case-study-patterns.md](docs/case-study-patterns.md).
A depth report after every run says where a story is thin (too short, a map with no caption, a section with no measured fact).

## How it works
```mermaid
flowchart LR
    A["Place, question, outline"] --> B["region: boundary"]
    B --> C["fetch / local files"]
    C --> D["describe: what the data says"]
    D --> E["Model writes the spec"]
    E --> F{"Quality gate"}
    F -- "fails: fix the sentence" --> E
    F -- passes --> G["Maps built from the data"]
    G --> H["Private draft in ArcGIS"]
```
The gate checks that the spec is valid, every claim is true against the data, every number in the prose is backed by a claim, no placeholder text is left, citations
are consistent, and the node graph is valid. Details: [docs/concepts.md](docs/concepts.md). Data sources, themes and caveats: [docs/data-sources.md](docs/data-sources.md).

## Use it
| how | when |
|---|---|
| **Claude Code skill** [`skill/SKILL.md`](skill/SKILL.md) | guided flow from a place and a topic to a draft |
| **CLI** `storymap-gen region / fetch / describe / init / run / facts / preview / schema / login / publish-spec` | scripting and CI |
| **MCP server** `storymap-gen-mcp` ([docs/mcp.md](docs/mcp.md)) | let an agent run the loop; fixed public data sources only, no publishing |
| **Python** `from storymap_gen.api import build, load_store, publish_spec` | notebooks, your own pipeline |

Claim types ([docs/claims.md](docs/claims.md)): `layer_stat`, `layer_area`, `layer_length`, `within_distance`, `street_orientation`. Layouts: `docked-sidecar`, `floating-sidecar`,
`longform`. Spec schema: [docs/spec.schema.json](docs/spec.schema.json).

## Signing in and publishing
`publish-spec` creates **private** Web Maps and a **private** story draft; it never touches an existing item. Signing in uses ArcGIS's own OAuth page, and the
token is received by a one-shot listener on your own computer, never a chat window:
```bash
storymap-gen login --client-id <Client ID>      # opens the browser; you click Allow; nothing to copy or paste
storymap-gen logout                             # deletes the saved token
```
One-time setup: in your ArcGIS organisation create a "Developer credentials" item with **OAuth 2.0 (user authentication)** and the redirect URL
`http://localhost:8765/storymap-gen-callback`; the item page shows the Client ID (it is not a secret). The token lasts about half an hour (two weeks with `--stay-signed-in`) and is kept in a user-only
file until it expires (`--no-save` keeps nothing). You can also set `ARCGIS_TOKEN` yourself. The token is only ever sent to an `*.arcgis.com` portal over https
(ArcGIS Enterprise hosts must be listed in `STORYMAP_ALLOWED_PORTAL_HOSTS`), and redirects are never followed. Review the draft in the StoryMaps Builder and publish it yourself.

## Security and reliability
Inputs are treated as untrusted, because the spec comes from a model that may have read hostile text. Highlights (full audit: [docs/security.md](docs/security.md)):
claim filters are parsed with a whitelist instead of evaluated; model-supplied regexes cannot cause catastrophic backtracking; every size is bounded; downloads refuse
private addresses, redirects and oversized responses; the token cannot be sent to a non-ArcGIS host; the MCP server reads and writes only inside a data root and has no publish tool;
tests never touch a real token or cache. A 20,000-input random soak test produced no crash, and fuzz tests run in CI. Report vulnerabilities as described in [SECURITY.md](SECURITY.md).

## Evidence
- **Tests**: unit, property-based, fuzz, MCP protocol, security regressions, an offline end-to-end run from a place name to a draft, and a contract test against the live StoryMaps service (`pytest -m network`, also weekly in CI).
- **Real services**: geocoding, Overpass downloads and clipping were run against the live services for an unseen district; the first run exposed a flaw (a nature reserve inflated a district's park area 160x) that mocks could not, now fixed and pinned by tests.
- **Opened in the real Builder**: all three layouts rendered from compiled drafts, now including maps generated from data. See the worked example [docs/showcase/gracia.md](docs/showcase/gracia.md) and what the real service exposed, [docs/validation-log.md](docs/validation-log.md).
- **Experiment with real model calls** ([experiments/RESULTS.md](experiments/RESULTS.md)): measuring the data before writing raised first-draft acceptance from 0% to 100% and halved the cost; asking scoping questions removed preference rework but not factual rework.

## Limits
- StoryMaps item JSON is not an officially documented API; the format was reverse-engineered from a published story and is guarded by the contract test.
- **Generated maps** (features embedded in a Web Map) were opened in the live ArcGIS service and in all three layouts; that run found four defects that tests had missed, all fixed ([validation log](docs/validation-log.md)). Only a few places have been checked live, so expect more.
- OpenStreetMap coverage varies by place, and it has no demographics. Only what is in the data can be claimed; numbers about people need data you supply.
- Only numbers in prose are bound to claims. Qualitative statements are not verified.
- Places are limited to 150 km2 (neighbourhood or district scale); maps hold up to 20,000 features per layer and about 8 MB.
- One theme (`slate`), three layouts. The experiment used simulated users and one model.

## Contributing and license
See [CONTRIBUTING.md](CONTRIBUTING.md) (adding a claim type, a data theme, a layout), [CHANGELOG.md](CHANGELOG.md). MIT, see [LICENSE](LICENSE).
Data sources and terms for the fixtures: [fixtures/README.md](fixtures/README.md).
