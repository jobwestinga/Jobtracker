# Import or export a legacy weight history

The optional importer reads a local legacy plotting project's `data/` folder.
It expects HealthManager CSVs, `WeightData.xlsx` and `HistoricWeight.xlsx`.
Original source files stay unchanged. Only use your own server; keep the inputs,
output and access token outside version control.

Install the importing computer's extra dependencies, then inspect a dry run:

```sh
python3 -m pip install -r requirements-weight-import.txt
python3 -m server.import_weights --source /path/to/legacy-project --dry-run
```

Import through the authenticated operation API:

```sh
python3 -m server.import_weights --source /path/to/legacy-project \
  --server https://your-server --token-file /path/to/sync_token
```

Precedence is the newest scale export, manual spreadsheet readings on days the
scale does not cover, then historic readings. Repeated imports preserve saved
edits and imported identities. Weight uses calendar dates, independently of the
tracking day's start time.

To export current server history for the external plotting workflow:

```sh
python3 -m server.import_weights --source /path/to/legacy-project \
  --server https://your-server --token-file /path/to/sync_token \
  --export-json /path/to/private-output/JobTrackerWeights.json
```

Synced backup restores are separate from this importer and go through the
ordered outbox. See [the write contract](../architecture/write-path.md) and
[recovery guide](incidents.md) before attempting a repair. Public test histories
are synthetic; never copy your real scale files into tests.
