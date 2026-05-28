# Meshtastic CSV Gap Filler

A CLI post-processing tool for [Meshtastic](https://meshtastic.org/) range-test CSV logs. It detects missing sequence numbers in the packet log and fills the gaps with linearly interpolated rows, producing a complete, continuous track suitable for import into mapping tools.

## Problem it solves

During a Meshtastic range test the base node broadcasts numbered packets (`seq 1`, `seq 2`, …) at regular intervals. The mobile station logs every packet it receives. When a packet is lost (too far, obstacle, fade) no row is written — leaving a gap in the sequence. This creates broken tracks when the log is visualised on a map.

This tool detects those gaps and synthesises placeholder rows so the full path is represented even for packets that were never received.

## Getting started

```bash
git clone https://github.com/DamianGalka/Python_Meshtastic.CSV.filler.git
cd Python_Meshtastic.CSV.filler
pip install pandas
python mesh-csv-filler.py
```

## Project structure

```
Python_Meshtastic.CSV.filler/
├── mesh-csv-filler.py                 # Main script
├── README.md                          # This file
├── Meshtastic_rangetest_*.csv         # Input files (place in same directory as script)
└── Meshtastic_rangetest_*_filled.csv  # Output files (generated automatically)
```

## Usage

```bash
python mesh-csv-filler.py
```

The script is interactive:

1. **Sender name** — enter the `sender name` value to filter on, or press Enter to use the default (`BASE/BELCHATOW/DOLN/WEST`).
2. **File selection** — choose to process all `.csv` files found in the script directory, or select a single file. Already-processed `_filled.csv` files are automatically excluded.

Output files are written as `<original_name>_filled.csv` in the same directory.

## Input CSV format

The script expects CSV files exported from a Meshtastic mobile station with the following columns (all fields quoted):

| Column | Example | Notes |
|---|---|---|
| `date` | `2026-05-24` | ISO date of reception |
| `time` | `10:29:31` | Time of reception |
| `from` | `173442116` | Numeric Meshtastic node ID |
| `sender name` | `BASE/BELCHATOW/DOLN/WEST` | Human-readable node name (filter key) |
| `sender lat` | `51.358468` | Transmitter GPS latitude |
| `sender long` | `19.35424` | Transmitter GPS longitude |
| `rx lat` | `51.357401` | Receiver GPS latitude |
| `rx long` | `19.355757` | Receiver GPS longitude |
| `rx elevation` | `98` | Receiver elevation in metres |
| `rx snr` | `13.0` | Signal-to-noise ratio in dB |
| `distance(m)` | `159` | Sender–receiver distance |
| `hop limit` | `0` | Remaining LoRa hop count |
| `payload` | `seq 1` | Range-test payload; non-`seq` rows are ignored |

## How gap filling works

For each pair of consecutive received packets where the sequence numbers are not contiguous (e.g. `seq 5` → `seq 8`), the script inserts synthetic rows for every missing step (`seq 6`, `seq 7`).

Each synthetic row is placed at an evenly spaced fraction between the two surrounding packets (`fraction = step / (gap_size + 1)`):

| Field | Method |
|---|---|
| `date` / `time` | Linear interpolation between the two surrounding timestamps |
| `rx lat` / `rx long` | Linear interpolation — straight-line path between the two known positions |
| `rx elevation` | Linear interpolation |
| `rx snr` | Hardcoded to `-21.0` (sentinel value — well below the noise floor, indicating the packet was never received) |
| `sender lat/long`, `from`, `sender name` | Copied from the preceding row (base node does not move) |
| `distance(m)`, `hop limit` | Copied from the preceding row (meaningless for a packet that was never received) |

## Input validation

Before processing each file the script checks:

- All required columns are present (`date`, `time`, `from`, `sender name`, `rx lat`, `rx long`, `rx elevation`, `payload`).
- `rx lat`, `rx long`, and `rx elevation` contain at least some parseable numeric values.

If either check fails, the file is skipped with a clear error message.

## Duplicate sequence numbers

If the mobile station logs the same packet twice (e.g. received via different mesh relays), the script detects the duplicate sequence numbers, prints a warning listing them, and keeps only the first occurrence before gap filling.

## Visualising the output

The `_filled.csv` files produced by this script can be fed directly into [**mesh-rangetest-map**](https://github.com/TheCommsChannel/mesh-rangetest-map), a companion tool by TheCommsChannel that renders the range-test track on an interactive map, colour-coded by SNR.

Typical workflow:

1. Run `mesh-csv-filler.py` to produce `<name>_filled.csv`.
2. Open / deploy `mesh-rangetest-map` and load the `_filled.csv` file.
3. The map displays the full continuous path, including the interpolated positions for packets that were never received (identifiable by their `rx snr` value of `-21.0`).

## Requirements

- Python 3.8+
- `pandas`

Install dependencies:

```bash
pip install pandas
```

## Notes

- Only rows where `payload` matches exactly `seq <number>` are processed; telemetry, node-info, and other payload types are silently ignored.
- The script filters the CSV to a single sender before processing, so multi-sender log files are supported.
- Files are sorted by modification time (newest first) in the interactive file picker.
- In single-file mode, paths are resolved and validated to prevent directory traversal outside the script directory.
