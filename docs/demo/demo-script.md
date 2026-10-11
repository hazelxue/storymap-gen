# Demo script: three minutes, offline, nothing private on screen

**Goal of the demo:** a viewer who has never seen the tool should understand, in three minutes, what it does and why a fact-checked story is different from a fluent one.
**One sentence to land:** *"A language model writes fluent map stories; this checks every number against the data before anything is built."*

Everything below runs on the data samples in `fixtures/`, so it needs no account, no login and no network (except opening the pictures' hosts if you show a finished draft).
An animated replay made from the real output is in [terminal-demo.svg](terminal-demo.svg); this page is for recording the live version.

## Before you record (privacy checklist: do all of it)
1. **Do not run `storymap-gen status` or `login` on camera.** `status` prints your organisation's address; `login` prints a URL containing your client id.
2. **Do not show a browser address bar for ArcGIS Map Viewer** (its URL contains your organisation's name). The StoryMaps Builder address (`storymaps.arcgis.com/stories/<id>/edit`) is fine. Record the browser window with the address bar hidden (full-screen, F11) or crop it.
3. Open a private/incognito window for the browser part, so no bookmarks, tabs, profile names or avatars of other accounts appear. Close every other tab.
4. Hide the taskbar notifications (Focus assist / Do not disturb) so no message previews pop up.
5. Use a clean terminal at a readable size (font 18 or larger), the project folder as the current directory (`PS F:\...>` is fine; do not show your user name in a longer path: open the terminal in the project folder).
6. After recording, check the video frame by frame at 1x for a name, an e-mail address, a token or an organisation name. If the Builder shows your avatar or name in the top-right corner, crop it out.
7. If you publish a draft during the demo, it is **private** by default; delete the test items afterwards in ArcGIS (the tool never deletes).

## How to record on Windows
- **Xbox Game Bar:** press `Win + G`, click the record button (or `Win + Alt + R`). It records one window; start it with the terminal focused.
- **Snipping Tool (Windows 11):** `Win + Shift + S`, choose the video camera icon, select the area, start.
- **OBS Studio** (free) if you want to add narration and cut sections.
Narrate live, or record silently and add the narration below as captions. Keep it under three minutes: a viewer decides in thirty seconds.

## The run
In the project folder:
```powershell
.\scripts\demo.ps1
```
It prints each step, pauses so a viewer can read, runs the real command, and pauses again (`-Fast` for rehearsing, `-NoBrowser` to skip opening the data explorer).

## Timeline and narration
| time | on screen | say (about 20 seconds each) |
|---|---|---|
| 0:00 | Title line of the script | "Map stories are easy to write and easy to get wrong. This tool takes a place and a topic, measures the real data, and refuses to build a story with a number the data does not support." |
| 0:15 | Step 1: the data explorer opens in the browser; toggle two layers; click a station | "First, look at the data. This is OpenStreetMap data for a district of Barcelona. Counts are real; I can click a feature to see its attributes." |
| 0:45 | `describe` output (layers, named features) | "The profile lists what is really in each layer, including the names I can safely mention: the longest streets, the biggest parks." |
| 1:05 | Step 2: `findings` | "Then it measures what is notable: which half of the area holds what, how far services reach. Ninety percent of the green space is in the north half. Each result comes with a claim that already passes." |
| 1:30 | Step 3: the failing gate | "Now the point of the project. A story with a street that is not diagonal, a misspelt name and a number nobody measured. It refuses, says what is wrong, and says how to fix it: 'did you mean Broadway'." |
| 2:00 | Step 4: the passing gate | "A full story passes: forty-six claims, every number backed, nine maps, a swipe. Nothing is built until this is green." |
| 2:20 | Step 5: Tianjin, six districts | "The same pipeline on a Chinese city: six districts as one study area, and a map coloured by district." |
| 2:40 | Switch to the browser: the private draft in the StoryMaps Builder; scroll to a swipe map and slide it | "And this is the result in ArcGIS: a private draft the author reviews and publishes themselves." |
| 2:55 | Closing line (end of the script) | "It checks every number. It does not make the writing good: that is still the author's job, and the project says so." |

## Say this, and not that
- **Say:** "checks every number against the data", "private draft", "found by opening it in the real service", "verified in two different places".
- **Do not say:** "generates high-quality stories automatically", "ready for production", "adopted by" anything. The validation log lists what the tool cannot do (it cannot tell a true number attached to the wrong sentence, and it does not make prose vivid); mentioning that on camera builds trust.
- If asked "does it use an API key or a model?": the tool itself calls no model; it runs inside Claude Code or any MCP client, with the user's own session, and stores no key.

## If you want a second, longer take (6 minutes)
Add, between steps 4 and 5: the validation log (`docs/validation-log.md`, 13 defects found by using the real services) and the fresh-model test at its end (what a model with only the skill produced, and what the gate could not catch). Those two pages are the strongest evidence that the project is honest.
