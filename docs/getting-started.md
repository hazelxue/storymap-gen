# Getting started

You will have a story in three levels of effort. Start at level 1 (two minutes, nothing to sign in to) and stop whenever you have what you need.

| level | what you do | you need |
|---|---|---|
| 1. Try it | run the checks and look at a finished story on sample data | Python 3.10 or newer |
| 2. Make a story about a place you choose | ask Claude Code for it (or run the commands yourself) | + internet, + Claude Code for the easy way |
| 3. Put it in ArcGIS | publish a private draft in your own ArcGIS Online account | + an ArcGIS Online account (publisher role) and a one-time setup |

## 1. Try it (two minutes, offline)
```bash
git clone https://github.com/hazelxue/storymap-gen
cd storymap-gen
python -m pip install -e .
python -m storymap_gen run examples/gracia_barcelona_full.json --manifest fixtures/gracia/manifest.json
python -m storymap_gen explore --manifest fixtures/gracia/manifest.json --open
```
The first command checks 46 claims about a Barcelona district against real data and prints `Quality gate passed`. The second opens a map of the data in your browser (works offline).
On Windows PowerShell use one command per line (it has no `&&`).

## 2. Make a story about a place you choose

### The easy way: with Claude Code
1. Install the tool with its Claude Code extras, and the skill, from the folder you cloned in step 1:
   ```bash
   python -m pip install -e ".[mcp]"
   python scripts/install_skill.py
   ```
   The last command copies the skill into `~/.claude/skills/storymap-gen` (add `--project <folder>` to install it for one project only). Run it again after updating the tool.
   If you also want Claude Code to call the tool directly (not only through the commands), register the server: `claude mcp add storymap-gen -- storymap-gen-mcp` ([docs/mcp.md](mcp.md)).
2. Open Claude Code in an empty folder for your project and ask in plain language, for example:
   > Make a StoryMap about transport in Hexi District, Tianjin.
3. Claude walks you through five steps and asks you, in this order:
   1. **Sign in** to ArcGIS (only needed at the end; you can skip it and still get the story file).
   2. **The study area**: a place name, or several places that together make the area.
   3. **The data**: it downloads public OpenStreetMap data (it says what it will contact and asks first), or you give your own files. You see counts, the biggest streets and parks, a map of the data, and what is notable in it.
   4. **Your outline and your question**: paste an outline, or choose a structure (`systems`, `essay`, `brief`); say the audience and how long.
   5. **The story**: Claude writes it, the tool checks every number against the data and refuses to continue until they are right, and you review a draft.
4. **What makes it better:** your own photographs, a table of numbers (income, population), what you know about the place. The tool cannot invent those and should not.

### The manual way: the commands
```bash
storymap-gen region "Gràcia, Barcelona" --out region.json                     # a place name becomes a boundary
storymap-gen fetch --region region.json --layers streets parks transit bus_stops schools --out data/
storymap-gen describe --manifest data/manifest.json                           # what is really in the data
storymap-gen explore --manifest data/manifest.json --open                     # look at it on a map
storymap-gen findings --manifest data/manifest.json --claims-out findings.json   # what is notable, with verified claims
storymap-gen context "Gràcia, Barcelona" --out context.json                  # Wikipedia background, reusable pictures with credits
storymap-gen init --region region.json --topic "Green space in Gràcia" --manifest data/manifest.json --out spec.json
#   edit spec.json: replace every TODO, and back every number with a claim (docs/writing-guide.md, docs/claims.md)
storymap-gen run spec.json --manifest data/manifest.json --out story.json     # check everything; exit 0 means it passed
```
Several places make one study area: `storymap-gen region "Hexi District, Tianjin, China" "Nankai District, Tianjin, China" --name "Two districts"`. A combined area over 150 km2 needs `STORYMAP_MAX_AREA_KM2=400` in the environment.
Use your own files with `--data name=file.geojson`; colour areas by your own table of numbers with `storymap-gen join` ([docs/thematic-maps.md](thematic-maps.md)).

## 3. Put it in ArcGIS (a private draft in your own account)
This is the only step that needs an account. The tool signs in the standard way: you approve on ArcGIS's own page and paste the result into a hidden prompt in your own terminal. **The tool never sees your password**, and the sign-in lasts about an hour.

**One-time setup** (about five minutes; it needs a publisher role in your ArcGIS Online organisation, ask your administrator if the button is missing):
1. In ArcGIS Online open **Content**, then **New item**, then **Developer credentials**.
2. Choose **OAuth 2.0 credentials (user authentication)**. Set the redirect URL to exactly `https://localhost:8765/storymap-gen-callback`. Give it any name.
3. Copy the **Client ID** shown on the new item. It is not a secret on its own, but keep it out of anything you publish.

**Each time:**
```bash
storymap-gen login --client-id <your Client ID> --portal <your-organisation>.maps.arcgis.com    # first time; later just: storymap-gen login
storymap-gen publish-spec spec.json --manifest data/manifest.json --title "My story" --download-images
```
`login` prints an address: open it, click **Allow**; the next page will fail to load, which is expected; copy its full address and paste it at the prompt that waits in the terminal (nothing is shown while you paste; press Enter).
`publish-spec` creates **private** Web Maps and a **private** story draft and prints the link. Review it in the StoryMaps Builder, then publish and share it yourself. The tool never publishes, never overwrites an existing story, and never deletes anything (remove test items in ArcGIS yourself).
`--download-images` is only needed when the story has pictures: the Builder does not load pictures from web addresses, so each one is downloaded (public https addresses only) and uploaded to the draft; without the flag pictures are left out.
Do not paste a token into a chat window. If you already did, sign out and sign in again.

## When something goes wrong
| you see | it means | do this |
|---|---|---|
| `No module named storymap_gen` | the package is not installed for the Python you are running, or you are in another folder | `python -m pip install ...` with the same `python`; run from the project folder |
| `the area is ... km2, above the limit` | the place is bigger than the download limit (150 km2) | choose a district, or set `STORYMAP_MAX_AREA_KM2=400` for a combined area |
| `no place found` | the geocoder did not recognise the name | use a more specific name with the city and country, in English; check the boundary area it reports |
| `FAIL <claim>` | a sentence is wrong against the data | change the sentence to what the data says (the `fix:` line gives the value), never the data |
| `UNBACKED <n>` | a number in the text has no claim | attach a claim that states it, or round to a backed value |
| `PLACEHOLDER` | the text still contains `TODO` | write it |
| `MAP ...` | a map names a missing layer, or a filter selects nothing | fix the layer name or the filter |
| `THIN ...` | the story is short or a map has no caption | not blocking; a to-do list for you |
| Overpass is busy / a download fails | the public server is overloaded | wait a few minutes and run again (it already retries other servers); never use partial data |
| `Not signed in` | the sign-in expired (about an hour) | `storymap-gen login` again |
| the paste at `login` shows nothing | that is how a hidden prompt works | right-click or `Ctrl+V`, then Enter; do not paste it anywhere else |
| pictures are missing in the Builder | they were left out | publish with `--download-images` |
| accents or Chinese look broken in PowerShell | the console encoding | run `$env:PYTHONUTF8 = "1"` first |

## What it does and where it stops
- It **checks the numbers**: every number in the text must be backed by a measurement on the data, and a wrong one stops the build. It does **not** check that a true number belongs to the sentence it sits in, nor captions and qualitative statements; read the story once as a reader.
- It does **not make the writing vivid**. Pictures, names, history, interviews and your own observations do, and they have to come from you (or from `context`, which you must check).
- The free map data (OpenStreetMap) is uneven: bike lanes and small clinics are often missing, and there is no population or income. Say so in the story; the tool's examples do.
- It works for neighbourhoods and districts (up to 150 km2 per download), not whole cities, and for one theme and three layouts. Charts, timelines and galleries are not supported yet.
- **Privacy and safety:** the tool contacts only OpenStreetMap services (Nominatim, Overpass), Wikipedia and Wikimedia Commons, and (when you publish) your own ArcGIS organisation. It stores no password or key; the sign-in token lives about an hour in a file only you can read. Details: [security.md](security.md).

## Tell us what happened
You do not need to write code. Open an issue with **Tell us how it went** (a short form: what you tried, whether it worked, where you got stuck): <https://github.com/hazelxue/storymap-gen/issues/new/choose>. A story that failed is as useful as one that worked.
If you want to fix or add something yourself, see [CONTRIBUTING.md](../CONTRIBUTING.md); pull requests are welcome but never required.
**Before you post anything**, remove your organisation's name, your Client ID, tokens and personal data from what you paste.
