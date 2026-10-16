# Contributing

Thanks for considering a contribution.

## Development setup

```bash
git clone https://github.com/Ambition-0303/Co-Scraper.git
cd Co-Scraper
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Checks

Run these commands before opening a pull request:

```bash
pytest
ruff check .
```

## Guidelines

- Keep public APIs backward compatible unless the change is explicitly breaking.
- Add tests for bug fixes and new behavior.
- Do not commit generated outputs, datasets, credentials, or local paths.
- Prefer small, focused pull requests with a clear motivation.
