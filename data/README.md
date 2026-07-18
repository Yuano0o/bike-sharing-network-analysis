# Data directory (not versioned)

This directory is intentionally excluded from Git. The project processes large
public trip-history files, and neither raw nor row-level cleaned trip data is
included in the repository.

Use the downloader from the repository root to create per-city folders and a
local download manifest:

```bash
python tools/download_bikeshare_data.py \
  --cities san_francisco washington chicago portland \
  --output-root data
```

The downloader is configured with the official data endpoints below. Data
availability, schemas, and provider terms can change, so always review the
current provider page before redistributing or processing a download.

| City | Configured source |
| --- | --- |
| San Francisco (Bay Wheels) | <https://s3.amazonaws.com/baywheels-data> |
| Washington, DC (Capital Bikeshare) | <https://s3.amazonaws.com/capitalbikeshare-data> |
| Chicago (Divvy) | <https://divvy-tripdata.s3.amazonaws.com> |
| Portland (BIKETOWN) | <https://s3.amazonaws.com/biketown-tripdata-public> |
| New York City (Citi Bike) | <https://citibikenyc.com/system-data> |

Expected local layout after downloading and processing:

```text
data/
├── san_francisco/          # downloaded files and local manifest
├── washington/
├── chicago/
└── portland/
```

Write derived outputs outside `data/`, for example under `outputs/`, so that
data acquisition and generated analysis artefacts remain separate from source
code.
