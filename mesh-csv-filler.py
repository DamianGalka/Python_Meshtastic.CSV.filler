# mesh-csv-filler.py
#
# Post-processes Meshtastic range-test CSV logs exported by the mobile station.
# During a range test the base node transmits numbered packets ("seq 1", "seq 2", …).
# The mobile station logs only the packets it receives, so lost packets leave gaps
# in the sequence.  This script detects those gaps and inserts synthetic rows with
# linearly interpolated position and timestamp data, producing a complete, continuous
# track that can be imported into mapping tools (QGIS, Google Maps, etc.).
#
# Usage:  python mesh-csv-filler.py
# Output: <input_name>_filled.csv written next to the input file.

import re
import pandas as pd
from datetime import datetime
import os
import csv
import glob

# Columns that must contain numeric values for interpolation to work.
NUMERIC_COLS = ['rx lat', 'rx long', 'rx elevation']


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def parse_seq(payload):
    """Return the integer N from a payload string of exactly 'seq N', or None.

    Uses a strict full-match regex so strings like '<TELEMETRY_APP>' or
    'useq 1' are never accidentally parsed as sequence numbers.
    """
    m = re.fullmatch(r'seq (\d+)', str(payload).strip())
    return int(m.group(1)) if m else None


def get_script_dir():
    """Return the absolute path of the directory that contains this script.

    Using __file__ keeps the working directory independent of where the user
    launches Python from.
    """
    return os.path.dirname(os.path.abspath(__file__))


def get_csv_files():
    """Return all unprocessed CSV files in the script directory.

    Files that already end in '_filled.csv' are excluded to prevent
    re-processing previously generated output.  The list is sorted by
    modification time, newest first, so the most recent capture appears
    at the top of the interactive picker.
    """
    script_dir = get_script_dir()
    csv_files = glob.glob(os.path.join(script_dir, "*.csv"))
    csv_files = [f for f in csv_files if not f.endswith("_filled.csv")]
    csv_files.sort(key=os.path.getmtime, reverse=True)
    return csv_files


def validate_columns(df, input_file):
    """Check that the CSV has the expected schema before processing begins.

    Verifies:
      1. All required column names are present.
      2. The three numeric columns (rx lat, rx long, rx elevation) contain at
         least some parseable numeric values — an all-empty column would make
         interpolation meaningless and crash later arithmetic.

    Returns True if the file is safe to process, False otherwise.
    """
    required = ['sender name', 'payload', 'date', 'time'] + NUMERIC_COLS
    missing = [c for c in required if c not in df.columns]
    if missing:
        print(f"   ❌ Missing columns: {missing}")
        return False
    for col in NUMERIC_COLS:
        if not pd.to_numeric(df[col], errors='coerce').notna().any():
            print(f"   ❌ Column '{col}' contains no valid numeric values")
            return False
    return True


# ---------------------------------------------------------------------------
# Core processing
# ---------------------------------------------------------------------------

def process_single_file(input_file, sender_name):
    """Read one CSV, fill sequence gaps, and write the result as *_filled.csv."""
    print(f"\n📖 Processing: {os.path.basename(input_file)}")

    # --- Load ---
    # QUOTE_ALL matches the quoting style used by the Meshtastic CSV export.
    df = pd.read_csv(input_file, quoting=csv.QUOTE_ALL)

    if not validate_columns(df, input_file):
        return

    # --- Filter to a single sender ---
    # A single CSV may contain packets from multiple nodes.  We only want the
    # range-test sender so other traffic does not interfere with gap detection.
    print(f"   🔎 Filtering for sender: {sender_name}")
    df = df[df['sender name'].astype(str).str.strip() == sender_name].copy()

    if df.empty:
        print(f"   ❌ No data found for sender: {sender_name}")
        return

    # --- Coerce numeric columns ---
    # Non-numeric cells (empty strings, NaN) become NaN so arithmetic on them
    # fails loudly rather than producing silent garbage values.
    for col in NUMERIC_COLS:
        df[col] = pd.to_numeric(df[col], errors='coerce')

    # --- Extract sequence numbers ---
    # Only rows whose payload matches "seq N" are kept; telemetry, node-info,
    # and other payload types are dropped at this step.
    df['seq'] = df['payload'].apply(parse_seq)
    df = df.dropna(subset=['seq']).copy()
    df['seq'] = df['seq'].astype(int)

    # --- Remove duplicate sequence numbers ---
    # Duplicate seq values can appear when the mobile station receives the same
    # packet more than once (e.g. via a mesh relay).  Keeping duplicates would
    # produce duplicate rows in the output without any gap between them.
    dupes = df[df.duplicated(subset=['seq'], keep=False)]['seq'].unique()
    if len(dupes):
        print(f"   ⚠️  Duplicate sequence numbers found and removed (kept first): {sorted(dupes)}")
        df = df.drop_duplicates(subset=['seq'], keep='first')

    # Sort by sequence number and build a combined datetime column used for
    # interpolation.  The original 'date' and 'time' columns are kept as-is
    # so they can be written back to the output unchanged.
    df = df.sort_values('seq').reset_index(drop=True)
    df['datetime'] = pd.to_datetime(df['date'] + ' ' + df['time'])

    # --- Gap filling ---
    # Walk consecutive pairs of received packets.  When the sequence jumps by
    # more than 1 (gap_size > 0), synthetic rows are inserted for every missing
    # step using linear interpolation so the reconstructed track is smooth.
    filled_rows = []
    i = 0
    while i < len(df):
        filled_rows.append(df.iloc[i].to_dict())

        if i + 1 < len(df):
            current_seq = int(df.iloc[i]['seq'])
            next_seq    = int(df.iloc[i + 1]['seq'])
            gap_size    = next_seq - current_seq - 1

            if gap_size > 0:
                print(f"   → Gap found: seq {current_seq} → {next_seq} ({gap_size} missing)")

                start = df.iloc[i]
                end   = df.iloc[i + 1]

                for step in range(1, gap_size + 1):
                    # fraction moves from 0 (exclusive) to 1 (exclusive) across
                    # the gap, placing each synthetic point evenly between the
                    # two surrounding received packets.
                    fraction = step / (gap_size + 1)

                    new_dt = start['datetime'] + (end['datetime'] - start['datetime']) * fraction

                    new_row = {
                        # Timestamps: linear interpolation between the two surrounding packets.
                        'date': new_dt.strftime('%Y-%m-%d'),
                        'time': new_dt.strftime('%H:%M:%S'),

                        # Sender fields are constant — the base node does not move.
                        'from':        str(start['from']),
                        'sender name': str(start['sender name']),
                        'sender lat':  start.get('sender lat', ''),
                        'sender long': start.get('sender long', ''),

                        # Receiver position: linear interpolation so the reconstructed
                        # path follows a straight line between the two known positions.
                        'rx lat':       round(start['rx lat']       + (end['rx lat']       - start['rx lat'])       * fraction, 8),
                        'rx long':      round(start['rx long']      + (end['rx long']      - start['rx long'])      * fraction, 8),
                        'rx elevation': round(start['rx elevation'] + (end['rx elevation'] - start['rx elevation']) * fraction, 1),

                        # SNR is set to -21.0 as a sentinel value indicating the packet
                        # was never received (well below the typical -20 dB noise floor).
                        'rx snr': -21.0,

                        # distance and hop limit are meaningless for a packet that was
                        # never received, so we copy the preceding row's values as a
                        # best-effort placeholder.
                        'distance(m)': start.get('distance(m)', ''),
                        'hop limit':   start.get('hop limit', ''),

                        'payload': f"seq {current_seq + step}"
                    }
                    filled_rows.append(new_row)

        i += 1

    # --- Assemble and write output ---
    # Reindex to the original column order, dropping the temporary 'datetime'
    # and 'seq' helper columns that were added during processing.
    result_df = pd.DataFrame(filled_rows)
    original_columns = [
        "date", "time", "from", "sender name", "sender lat", "sender long",
        "rx lat", "rx long", "rx elevation", "rx snr", "distance(m)", "hop limit", "payload"
    ]
    result_df = result_df[original_columns]

    base_name   = os.path.splitext(input_file)[0]
    output_file = f"{base_name}_filled.csv"

    # Write with QUOTE_ALL to match the input format exactly.
    result_df.to_csv(
        output_file,
        index=False,
        quoting=csv.QUOTE_ALL,
        quotechar='"',
        encoding='utf-8'
    )

    print(f"   ✅ Saved: {os.path.basename(output_file)}  ({len(df)} → {len(result_df)} rows)")


# ---------------------------------------------------------------------------
# Entry point / interactive UI
# ---------------------------------------------------------------------------

def main():
    default_sender = "BASE/BELCHATOW/DOLN/WEST"
    script_dir = get_script_dir()
    print(f"📂 Working directory: {script_dir}\n")

    # Ask for the sender name once and reuse it for all files in batch mode.
    sender_name = input(f"Enter sender name (default: {default_sender}): ").strip()
    if not sender_name:
        sender_name = default_sender
    print(f"Using sender: {sender_name}\n")

    csv_files = get_csv_files()

    if not csv_files:
        print("❌ No .csv files found in the script directory!")
        return

    print(f"Found {len(csv_files)} CSV file(s):")
    for i, f in enumerate(csv_files, 1):
        print(f"   {i}. {os.path.basename(f)}")

    # --- Batch or single-file mode ---
    choice = input("\nProcess ALL files? (Y/n): ").strip().lower()
    process_all = choice in ["", "y", "yes"]

    if process_all:
        print(f"\n🔄 Processing all {len(csv_files)} files with sender '{sender_name}'...")
        for file_path in csv_files:
            process_single_file(file_path, sender_name)
    else:
        # Single-file mode: prompt for a filename, defaulting to the newest file.
        default_file = csv_files[0] if csv_files else ""
        prompt = f" (default: {os.path.basename(default_file)})" if default_file else ""
        input_file = input(f"\nEnter CSV filename or path{prompt}: ").strip()

        if not input_file and default_file:
            input_file = default_file
        elif not input_file:
            print("❌ No file selected.")
            return

        # Resolve the path and confirm it stays inside the script directory
        # to prevent directory traversal (e.g. "../../sensitive.csv").
        resolved = os.path.realpath(os.path.join(script_dir, input_file))
        if not resolved.startswith(os.path.realpath(script_dir) + os.sep):
            print("❌ Path outside the script directory is not allowed.")
            return

        if not os.path.exists(resolved):
            print("❌ File not found!")
            return

        process_single_file(resolved, sender_name)

    print("\n🎉 All done!")


if __name__ == "__main__":
    main()
