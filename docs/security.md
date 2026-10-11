# Security audit

This is what was checked, what was found, how each issue was fixed, and what risk remains. Every fix is pinned by a test (`tests/test_security.py`,
`tests/test_net_and_sources.py`, `tests/test_robustness.py`), so a regression fails CI.

## Threat model
The dangerous input is not the user; it is **content the model has read**. A place name, a downloaded layer, a web page or a pasted outline can carry instructions, and the
model may then write a hostile spec or call a tool with hostile arguments. So everything a model can influence is untrusted:

| input | what could go wrong |
|---|---|
| the spec (claims, text, URLs, ids) | code execution through a filter or regex, resource exhaustion, script or file URLs in the story |
| file paths and folder names (CLI, MCP) | reading or writing files outside the project, reading secrets |
| downloads (place names, Overpass, ArcGIS, GeoJSON) | requests to internal addresses (SSRF), redirects, huge responses, partial or poisoned data |
| the portal URL and user name | sending the token to an attacker's server |
| the token | leaking into chat, logs, errors, files, or git |

## Findings and fixes
| # | found by | issue | fix | test |
|---|---|---|---|---|
| 1 | code review | **Token exfiltration.** `--portal <any URL>` received the token in a POST body; redirects (307/308) re-send the body to another host; the user name was concatenated into the URL path | portal must be https and `*.arcgis.com` (Enterprise hosts opt in with `STORYMAP_ALLOWED_PORTAL_HOSTS`); redirects are never followed; user names are validated and encoded; a refused portal makes no request | `test_hostile_portal_urls_are_refused`, `test_redirects_are_never_followed...` |
| 2 | code review | **Code execution risk.** A claim's `where` was passed to `DataFrame.query(engine="python")`, which pandas documents as unsafe for untrusted input | replaced by a parser with an explicit whitelist (column names, comparisons, `in`, `and/or/not`, `.notna()/.isna()`); anything else is an error | `test_safe_filter_rejects_everything_else`, `test_hostile_where_reaches_the_user_as_an_error...` |
| 3 | code review | **ReDoS.** `grid_regex` is a model-written regular expression, and `re` has no timeout | refused if it quantifies a group, uses backreferences or lookarounds, is longer than 120 characters, or is ambiguous | `test_dangerous_regexes_are_refused_quickly` |
| 4 | code review | **Unbounded inputs.** No limits on spec size, data files, maps, or downloads | field and list bounds, 500,000-character text budget, 200 MB data files, 8 MB / 20,000-feature maps, 100 MB downloads, 150 km2 areas | `test_oversized_or_malformed_specs_are_rejected`, `test_size_limits_protect_the_item` |
| 5 | code review | **MCP could read any local `.json`** (no default root) and error messages exposed absolute paths | the data root defaults to the working directory; only GIS file types; paths are not echoed to the model | `test_mcp_reads_only_inside_the_working_directory_by_default`, `test_mcp_never_reads_non_gis_files` |
| 6 | code review | image URLs were not validated (`javascript:`, `file:`, `data:`) | https only | `test_oversized_or_malformed_specs_are_rejected` |
| 7 | tests | **SSRF gap.** multicast addresses and IPv4-mapped IPv6 (`::ffff:10.0.0.1`) passed Python's `is_global` | explicit check for private, loopback, link-local, multicast, reserved and unspecified, after unmapping IPv6 | `test_hosts_that_resolve_to_private_addresses_are_refused` |
| 8 | tests | projected coordinates in a GeoJSON without a CRS were silently read as longitude/latitude | refused at load with an explanation | `test_projected_coordinates_without_a_crs_are_refused` |
| 9 | soak test | ambiguous regex constructs raise a `FutureWarning`, which a strict environment turns into a crash | refused as invalid | `test_ambiguous_regex_constructs_are_refused_not_warned_about` |
| 10 | live test | not security, but dangerous to readers: unclipped OpenStreetMap data made a district's park area 160 times too large | all downloads are clipped to the place boundary | `test_a_huge_polygon_is_cut_to_the_study_area` |

Also verified: no secrets, names, e-mail addresses or private paths in any tracked file or in the full git history (`test_no_secrets_private_paths_or_personal_emails_in_tracked_files`,
which fails if a token, personal e-mail or home path is planted); tests run with an isolated token and cache directory so they can never use a real login.

## Credentials
- `storymap-gen login` runs ArcGIS's own consent page and takes the token through a **hidden terminal prompt**; there is no way to pass a token as a command-line argument.
- The token is never printed, logged, or put in an error message (tested). It is stored only if you do not pass `--no-save`, in a user-only file in your profile until it expires (about an hour);
  `logout` deletes it; the file is bound to its portal.
- The MCP server has no tool that reads, writes or sends a credential, and no publish tool.

## Evidence
- Static analysis: `bandit` (no medium or high findings; one intentional low: a malformed OpenStreetMap feature is skipped rather than failing the layer), `ruff`, `mypy`.
- Dependencies: `pip-audit` reported no known vulnerabilities for the declared dependencies.
- Dynamic: property and fuzz tests (random JSON into the parser, random mutations of valid specs through the whole pipeline, random claim parameters, corrupt data files), and
  `scripts/soak.py` (final code, seed 7): **20,000 randomly mutated specs, no crash** (19,150 rejected cleanly by validation, 850 processed), slowest run 725 ms, memory flat (84 KiB growth over 19,500 runs).

## Remaining risks (honest list)
- **DNS rebinding.** The host name is checked before the request; a hostile DNS server could answer differently for the real connection. Only fetch from sources you trust; the MCP server only uses two fixed hosts.
- **Parsers.** GDAL/GeoPandas parse data files and `osm2geojson` parses Overpass JSON. They are third-party code reading untrusted bytes; sizes are capped, but a parser bug is possible. Keep dependencies updated.
- **Prompt injection still affects content.** It cannot make the tool run code or leak credentials (by the rules above), but a model can still be talked into a misleading story. The gate catches
  false numbers and unbacked claims, not slanted wording.
- **Token at rest.** If you keep the saved token, it sits unencrypted in your profile for up to an hour. Use `--no-save` or `logout` on shared machines.
- **Supply chain.** Runtime dependencies are `pydantic`, `geopandas`, `shapely`, `pyproj`, `numpy`, `requests`, `osm2geojson` (and `mcp` for the server). Pin and audit them in your own environment.
- **ArcGIS side.** Web Maps and drafts are created private; what you later share or publish is up to you.

Report anything else as described in [SECURITY.md](../SECURITY.md).

## Pictures
The Builder only shows pictures that are stored on the story item, so `publish-spec --download-images` fetches each picture named in the spec and uploads it to the draft. This is the one place where addresses written by a model are downloaded, so:
- it happens **only with the flag**; without it, pictures are left out and the command says so; the command lists the hosts it will contact;
- every fetch uses the same guarded client as other downloads: https only, public addresses only (private, loopback, link-local, multicast and mapped addresses are refused), no redirects, a size limit of 8 MB, at most 30 pictures per story;
- a file is accepted only if its first bytes say it is a JPEG, PNG, GIF or WebP, whatever its name or the server's content type claims; HTML or anything else is refused;
- the MCP server has no publishing tool and does not download pictures.
