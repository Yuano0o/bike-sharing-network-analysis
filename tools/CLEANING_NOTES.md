# Cleaning implementation notes

This file records the cleaner's supported schema families and output contract.
Cleaning assumptions and thresholds are centralized in
[`docs/methodology.md`](../docs/methodology.md); runnable commands are in
[`tools/README.md`](README.md).

## Recognised schema families

`bikeshare_cleaning.py` detects several common layouts from their column names:

- **Bay Wheels / Ford GoBike legacy:** `duration_sec`, `start_time`,
  `end_time`, `start_station_id`, `end_station_id`, `user_type`
- **Capital Bikeshare legacy:** `duration`, `start date`, `end date`
- **Divvy legacy:** `tripduration`, `start_time`, `end_time`,
  `from_station_id`, `to_station_id`
- **Citi Bike / classic trip data:** `tripduration`, `starttime`, `stoptime`
- **Modern GBFS-style trips:** `started_at`, `ended_at`, `start_station_id`,
  `end_station_id`, `member_casual`
- **Portland hub export:** route, payment-plan, hub, date, time, and duration
  fields used by historical BIKETOWN exports

Station metadata files are detected separately and skipped as trip inputs.
Unsupported trip columns raise a schema error instead of being mapped by
position.

## Standardized trip fields

Recognised inputs are mapped to a shared event representation containing the
available values for:

- `started_at`, `ended_at`, and `duration_min`
- `start_station_id`, `end_station_id`
- start and end station names
- start and end coordinates
- normalized `user_type`
- derived city, source file, self-loop flag, and year-month fields

## Output contract

| File | Contents |
| --- | --- |
| `summary_overall.csv` | input and removal counts for the full run |
| `summary_by_user_type.csv` | retained row counts by user type |
| `duration_threshold_comparison.csv` | retained/removed counts for candidate cutoffs |
| `station_pairs_normalized.csv` | directed pair counts and origin-normalized weights |
| `station_lookup_canonical.csv` | consolidated station names and coordinates |
| `station_id_conflicts.csv` | observed metadata variation by station ID |
| `cleaned_customer.csv` | standardized customer event stream |
| `cleaned_subscriber.csv` | standardized subscriber event stream |

Row-level files are omitted when `--skip-cleaned-csv` is used. If Matplotlib
is installed, the cleaner also writes lightweight duration and top-pair plots
under the selected output directory.
