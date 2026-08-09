from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


SKIN_RE = re.compile(r"\[\s*(\d+)\s*\]\s*=\s*\"([^\"]*)\"")


def parse_names(path: Path) -> dict[int, str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return {int(match.group(1)): match.group(2).strip() for match in SKIN_RE.finditer(text)}


def parse_peds(path: Path) -> dict[int, tuple[str, str]]:
    result: dict[int, tuple[str, str]] = {}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = [part.strip() for part in raw.split(",")]
        if len(parts) < 3 or not parts[0].isdigit():
            continue
        result[int(parts[0])] = (parts[1], parts[2])
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the bundled UG MOD HUB skin index")
    parser.add_argument("lua", type=Path)
    parser.add_argument("peds", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    names = parse_names(args.lua)
    models = parse_peds(args.peds)
    rows = []
    for skin_id, title in sorted(names.items()):
        model = models.get(skin_id)
        if not model or model[0].lower() == "null":
            continue
        rows.append({"id": skin_id, "name": title, "dff": model[0], "txd": model[1]})
    if not rows:
        raise SystemExit("No skin mappings were generated")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps({"version": 1, "skins": rows}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Generated {len(rows)} skin mappings: {args.output}")


if __name__ == "__main__":
    main()
