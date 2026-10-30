#!/usr/bin/env python3
"""Afgør om en udløser skal hente nyheder; ingen tredjepartsafhængigheder."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

DEEP_SCHEDULE = "17 3 * * *"
AUDIT_SCHEDULE = "23 4 1 * *"
FRESH_MINUTES = 20


def update_mode(event: str, schedule: str, full_audit: bool) -> str:
    if full_audit or (event == "schedule" and schedule == AUDIT_SCHEDULE):
        return "full"
    if event == "schedule" and schedule == DEEP_SCHEDULE:
        return "deep"
    return "fast"


def both_views_fresh(payloads: list[dict], now: datetime, minutes: int = FRESH_MINUTES) -> bool:
    if len(payloads) != 2:
        return False
    versions = set()
    for payload in payloads:
        try:
            stamp = datetime.fromisoformat(str(payload["updated_at"]).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                return False
            age = now - stamp.astimezone(timezone.utc)
            if age < -timedelta(minutes=5) or age >= timedelta(minutes=minutes):
                return False
            version = str(payload.get("version", ""))
            if not version:
                return False
            versions.add(version)
        except (KeyError, ValueError, TypeError):
            return False
    return len(versions) == 1


def should_check_freshness(event: str, mode: str, only_if_stale: bool) -> bool:
    # Upload, manuel kontrol og dybe kontroller skal aldrig springes over.
    return mode == "fast" and (event == "schedule" or only_if_stale)


def read_public_status(base_url: str) -> list[dict]:
    payloads = []
    for suffix in ("status.json", "styrelsesnyt/status.json"):
        request = Request(base_url.rstrip("/") + "/" + suffix,
                          headers={"User-Agent": "Ministerienyt-update-check/8.0", "Cache-Control": "no-cache"})
        with urlopen(request, timeout=15) as response:
            payload = json.load(response)
        if not isinstance(payload, dict):
            raise ValueError("Status var ikke et objekt")
        payloads.append(payload)
    return payloads


def main() -> int:
    event = os.environ.get("UPDATE_EVENT", "workflow_dispatch")
    mode = update_mode(event, os.environ.get("UPDATE_SCHEDULE", ""), os.environ.get("FULL_AUDIT", "false") == "true")
    only_if_stale = os.environ.get("ONLY_IF_STALE", "false") == "true"
    run = True
    reason = "Planlagt kontrol eller manuelt valgt opdatering."
    if should_check_freshness(event, mode, only_if_stale):
        try:
            run = not both_views_fresh(read_public_status(os.environ["PAGES_URL"]), datetime.now(timezone.utc))
            reason = "Opdatering nødvendig." if run else "Begge visninger er opdateret inden for 20 minutter; ekstra hentning springes over."
        except Exception as exc:
            # En statusfejl må ikke blokere genopretning af en gammel hjemmeside.
            reason = "Status kunne ikke bekræftes; opdaterer. " + type(exc).__name__
    output = f"run={'true' if run else 'false'}\nmode={mode}\n"
    if os.environ.get("GITHUB_OUTPUT"):
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as stream:
            stream.write(output)
    print(reason)
    print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
