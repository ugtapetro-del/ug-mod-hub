from __future__ import annotations

import argparse
import json
import re
import struct
from pathlib import Path


BLOCK_RE = re.compile(
    r"(?ms)^\s{4}([A-Za-z0-9_]+)\s*=\s*\{(.*?)^\s{4}\}[;,]"
)


def parse_lua(path: Path) -> dict[int, dict]:
    text = path.read_text(encoding="utf-8", errors="replace")
    result: dict[int, dict] = {}
    for match in BLOCK_RE.finditer(text):
        key, body = match.groups()
        model = re.search(r"\bmodel\s*=\s*(\d+)", body)
        name = re.search(r'\bname\s*=\s*"([^"]+)"', body)
        slot = re.search(r'\bslot\s*=\s*"([^"]+)"', body)
        if not model or not name:
            continue
        result[int(model.group(1))] = {
            "key": key,
            "name": name.group(1).strip(),
            "slot": slot.group(1).strip() if slot else "",
        }
    return result


def parse_ide(path: Path) -> dict[int, tuple[str, str]]:
    result = {}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) >= 3 and parts[0].isdigit():
            result[int(parts[0])] = (parts[1], parts[2])
    return result


def img_names(path: Path) -> set[str]:
    result = set()
    with path.open("rb") as handle:
        if handle.read(4) != b"VER2":
            raise ValueError("Accessory IMG must be an open VER2 archive")
        count = struct.unpack("<I", handle.read(4))[0]
        for _ in range(count):
            raw = handle.read(32)
            if len(raw) != 32:
                raise ValueError("Broken IMG directory")
            name = raw[8:32].split(b"\0", 1)[0].decode("ascii", errors="ignore")
            if name:
                result.add(name.casefold())
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("lua", type=Path)
    parser.add_argument("ide", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--img", type=Path, help="Only include targets whose DFF and TXD exist in this VER2 IMG")
    args = parser.parse_args()

    lua = parse_lua(args.lua)
    ide = parse_ide(args.ide)
    archive_names = img_names(args.img) if args.img else None
    accessories = []
    missing = []
    for model_id, info in sorted(lua.items()):
        model = ide.get(model_id)
        if not model or model[0].casefold() == "null":
            missing.append(model_id)
            continue
        if archive_names is not None and (
            (model[0] + ".dff").casefold() not in archive_names
            or (model[1] + ".txd").casefold() not in archive_names
        ):
            continue
        accessories.append({
            "id": model_id,
            "key": info["key"],
            "name": info["name"],
            "slot": info["slot"],
            "dff": model[0],
            "txd": model[1],
        })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps({"accessories": accessories}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"accessories={len(accessories)} missing_ide={len(missing)}")
    if missing:
        print("missing_ids=" + ",".join(map(str, missing[:30])))


if __name__ == "__main__":
    main()
