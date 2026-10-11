## What this changes and why

## What you measured
<!-- A bug fix starts with a test that fails. A new claim, block or data theme needs a test and, if it changes what the Builder shows, a look at the real result. -->

## Checklist
- [ ] `ruff check .`, `mypy storymap_gen` and `python -m pytest -q` pass
- [ ] I ran `python scripts/install_hooks.py`, so the privacy check ran before my push (no organisation names, client ids, tokens, personal e-mail addresses or paths in any file or commit)
- [ ] My commits use a `...@users.noreply.github.com` address
- [ ] User-visible changes are in `CHANGELOG.md`
