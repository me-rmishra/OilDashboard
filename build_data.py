"""Writes docs/data/*.json for static hosting (GitHub Pages). Run by the GitHub Action."""
import json, sys
from pathlib import Path
import server

out = Path(__file__).parent / "docs" / "data"; out.mkdir(parents=True, exist_ok=True)
jobs = {"prices": (server.build_prices, lambda v: bool(v["series"])),
        "eia": (server.build_eia, lambda v: bool(v["series"])),
        "news": (server.build_news, lambda v: any(not i["title"].startswith("[feed") for i in v))}
failed = []
for name, (fn, ok) in jobs.items():
    v = fn()
    if ok(v): (out / f"{name}.json").write_text(json.dumps(v), encoding="utf-8"); print("wrote", name)
    else: failed.append(name); print("FAILED", name, "- keeping previous file", file=sys.stderr)
sys.exit(1 if len(failed) == len(jobs) else 0)  # partial failure is fine; the old file stays
