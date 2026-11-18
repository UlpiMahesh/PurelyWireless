from __future__ import annotations

import argparse
import json
import sys
import traceback


def emit(event):
    sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--automation")
    parser.add_argument("--params", default="{}")
    args = parser.parse_args()
    if not args.run:
        parser.error("runner must be started with --run")
    try:
        params = json.loads(args.params)
        if args.automation == "demo":
            from automations.adapters.demo import run
            run(params, emit)
        else:
            raise ValueError(f"Unknown automation: {args.automation}")
    except BaseException as exc:
        emit({"type": "error", "message": str(exc), "traceback": traceback.format_exc()})
        return 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
