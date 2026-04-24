#!/usr/bin/env python3
"""Summarize settled journal samples without mixing live, shadow, and experiments."""

import argparse
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime

import models

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def _read_with_retry(fn, label, attempts=6, base_delay_sec=2.0):
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt >= attempts:
                raise
            wait_sec = min(base_delay_sec * attempt, 10.0)
            print(
                f"Backtest read waiting for live DB lock: {label} "
                f"(attempt {attempt}/{attempts}, retry in {wait_sec:.1f}s)",
                file=sys.stderr,
            )
            time.sleep(wait_sec)


def _rows(days):
    since_ts = time.time() - float(days) * 86400

    def _load():
        with models.db() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT *
                    FROM trade_journal
                    WHERE entry_timestamp >= ?
                      AND exit_timestamp IS NOT NULL
                    ORDER BY entry_timestamp DESC
                    """,
                    (since_ts,),
                ).fetchall()
            ]

    return _read_with_retry(_load, "settled_journal")


def _sample_type(row):
    return (row.get("sample_type") or "executed").strip().lower() or "executed"


def _source(row):
    return (row.get("signal_source") or "copy").strip().lower() or "copy"


def summarize(rows):
    buckets = defaultdict(
        lambda: {
            "sample_type": "",
            "source": "",
            "entries": 0,
            "wins": 0,
            "losses": 0,
            "flat": 0,
            "pnl": 0.0,
        }
    )
    for row in rows:
        key = (_sample_type(row), _source(row))
        bucket = buckets[key]
        bucket["sample_type"], bucket["source"] = key
        bucket["entries"] += 1
        pnl = float(row.get("realized_pnl", 0) or 0)
        bucket["pnl"] += pnl
        if pnl > 0:
            bucket["wins"] += 1
        elif pnl < 0:
            bucket["losses"] += 1
        else:
            bucket["flat"] += 1

    results = []
    for bucket in buckets.values():
        decisions = bucket["wins"] + bucket["losses"]
        bucket["decision_count"] = decisions
        bucket["win_rate"] = bucket["wins"] / decisions * 100 if decisions else None
        results.append(bucket)
    return sorted(results, key=lambda row: (row["sample_type"], row["source"]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=float, default=7)
    args = parser.parse_args()

    rows = _rows(args.days)
    print("Polymarket Journal Backtest")
    print(f"Window: last {args.days:g} day(s), generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    print("sample_type  source       entries  decs  win_rate  pnl")
    for row in summarize(rows):
        win_rate = "N/A" if row["win_rate"] is None else f"{row['win_rate']:.1f}%"
        print(
            f"{row['sample_type']:<11}  {row['source']:<11}  "
            f"{row['entries']:>7}  {row['decision_count']:>4}  "
            f"{win_rate:>8}  ${row['pnl']:>7.2f}"
        )
    print()
    print("Note: sample types are intentionally isolated; shadow and experiment rows are hypothesis data only.")


if __name__ == "__main__":
    main()
