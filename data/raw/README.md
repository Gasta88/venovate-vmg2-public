# data/raw

Committed snapshots from API pulls, one directory per source per day:

    data/raw/<source>/<YYYY-MM-DD>/...

Each pull also writes a `manifest.json` next to the snapshot: query, URL,
timestamp, record count, sha256. Reproducible = re-derivable from the
snapshot, re-pullable if wanted. APIs drift and OpenAlex now costs money.

Sources: openalex, ctgov, cordis. Populated by scripts/pull_*.py.