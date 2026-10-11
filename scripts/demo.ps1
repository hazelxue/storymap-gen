# A paced, offline demo of storymap-gen for screen recording (Windows PowerShell 5.1 or PowerShell 7).
#
#   cd <the project folder>
#   .\scripts\demo.ps1
#
# It needs no account and no network: it runs on the data samples in fixtures/. Start your recorder (Win+G, or OBS), then run it.
# Each step prints what it is about to show, pauses, runs the real command, and pauses again so a viewer can read the output.
# It never prints your organisation, your login or any token. Do not run `storymap-gen status` or `login` on camera.
#
#   -Fast      shorter pauses (for rehearsing)
#   -NoBrowser skip opening the data explorer in the browser

param([switch]$Fast, [switch]$NoBrowser)

$ErrorActionPreference = "Continue"
$env:PYTHONUTF8 = "1"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$scale = if ($Fast) { 0.3 } else { 1.0 }
$tmp = Join-Path $env:TEMP "storymap-demo"
New-Item -ItemType Directory -Force -Path $tmp | Out-Null

function Pause-For([double]$seconds) { Start-Sleep -Milliseconds ([int]($seconds * 1000 * $scale)) }
function Say([string]$text) { Write-Host ""; Write-Host "# $text" -ForegroundColor Cyan; Pause-For 2.5 }
function Run([string]$display, [scriptblock]$block) {
    Write-Host "PS> $display" -ForegroundColor Green
    Pause-For 1.2
    & $block
    Pause-For 4
}
function Sg { & python -m storymap_gen @args }

Clear-Host
Say "storymap-gen: from a place and a topic to an ArcGIS StoryMap, with every number checked against the data."

Say "1. Look at the data first. Here: OpenStreetMap data for Gracia, Barcelona, already downloaded."
if (-not $NoBrowser) {
    Run "storymap-gen explore --manifest data/manifest.json --open" { Sg explore --manifest fixtures/gracia/manifest.json --title "Gracia, Barcelona" --out (Join-Path $tmp "explore.html") --open }
    Say "(a map of the data opens in the browser: toggle layers, zoom, click a feature to see its attributes)"
    Pause-For 8
}
Run "storymap-gen describe --manifest data/manifest.json | Select-String '^## |named features'" {
    Sg describe --manifest fixtures/gracia/manifest.json | Select-String -Pattern "^## |named features" | Select-Object -First 12 | ForEach-Object { $_.Line.Substring(0, [Math]::Min(150, $_.Line.Length)) }
}

Say "2. Ask the data what is notable: gaps, which half of the area holds what, how layers relate. Each result carries a claim that already passes."
Run "storymap-gen findings --manifest data/manifest.json --top 6" {
    Sg findings --manifest fixtures/gracia/manifest.json --top 6 | Select-Object -First 16 | ForEach-Object { $_.Substring(0, [Math]::Min(150, $_.Length)) }
}

Say "3. The quality gate. A story with a wrong claim, a misspelt street and a number nobody measured:"
Run "storymap-gen run spec.json --data streets_centerline=streets.geojson" {
    try { Sg run examples/jackson_heights_broken.json --data streets_centerline=fixtures/streets_centerline.geojson | Select-Object -First 11 } catch { }
}
Say "It refuses to build, says what is wrong, and says how to fix it. That is the point: a fluent model writes plausible sentences; this checks them."

Say "4. A full story that passes: 46 claims, every number backed, maps, a swipe, pictures with credits."
Run "storymap-gen run spec.json --manifest data/manifest.json --out story.json" {
    Sg run examples/gracia_barcelona_full.json --manifest fixtures/gracia/manifest.json --out (Join-Path $tmp "story.json") | Select-Object -Last 8 | ForEach-Object { $_.Substring(0, [Math]::Min(150, $_.Length)) }
}

Say "5. Six districts of a Chinese city as one study area, with a map coloured by district (a graduated map)."
Run "storymap-gen run examples/tianjin_transport.json --manifest fixtures/tianjin/manifest.json" {
    Sg run examples/tianjin_transport.json --manifest fixtures/tianjin/manifest.json --out (Join-Path $tmp "tj.json") | Select-Object -Last 6 | ForEach-Object { $_.Substring(0, [Math]::Min(150, $_.Length)) }
}

Say "6. The last step is yours: publishing makes a PRIVATE draft in your own ArcGIS account (you sign in yourself; the tool never sees a password). Show the draft in the browser now."
Write-Host ""
Write-Host "End of demo. Real results of every step are in docs/validation-log.md." -ForegroundColor Cyan
Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
