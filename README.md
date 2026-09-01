# CIRCABC RSS Monitor

Generates an RSS feed for selected CIRCABC folders using GitHub Actions.

## What it does

The GitHub Action periodically:

1. Opens the configured CIRCABC library.
2. Crawls the configured folders.
3. Recursively discovers subfolders.
4. Detects new documents.
5. Detects modified documents.
6. Detects new document versions where metadata exposes the version.
7. Generates `docs/feed.xml`.
8. Commits the updated feed and state.

## Configuration

Edit `config.yml`.

Example:

```yaml
folders:

  - name: "CARACAL"
    url: "https://circabc.europa.eu/ui/group/a0b483a2-4c05-4058-addf-2a4de71b9a98/library/84998de9-01ff-4434-b566-85367d2fae5b"
    recursive: true
