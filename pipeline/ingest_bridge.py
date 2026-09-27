"""Machine-readable wrapper for the loopback ingest UI; normal CLI output is unchanged."""

import json
import sys

import ingest


if __name__ == "__main__":
    result = ingest.main(sys.argv[1:])
    print("GRIDLOCK_INGEST_RESULT " + json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)
