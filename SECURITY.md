# Security policy

## Reporting a vulnerability
Please report it privately through GitHub: **Security**, **Report a vulnerability** on this repository. Do not open a public issue for something exploitable. Include what you did,
what happened, and the version (`storymap-gen --help` shows the package; `pip show storymap-gen` the version). Expect an acknowledgement within a few days.

## What is in scope
The command line, the Python API, and the MCP server: unsafe handling of specs, data files, downloads, credentials, or file paths.

## Design rules (what we promise)
- The spec is untrusted input (it comes from a model that may have read hostile text). It is validated and size-limited; it is never evaluated as code.
- Credentials are never handled in chat or by a model. Tokens go to a hidden terminal prompt or an environment variable, are sent only to a validated ArcGIS host over https, and are never logged or echoed.
- Publishing only creates new private items; it never modifies existing ones.
- The MCP server has no publish tool and no arbitrary-URL download; its file access is confined to a data root.
- Downloads refuse private and loopback addresses, redirects, and oversized responses.

Details of the threat model, the issues found during the audit and how each was fixed: [docs/security.md](docs/security.md).
