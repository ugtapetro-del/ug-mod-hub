from __future__ import annotations

import json
import hashlib
import base64
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.parse
import urllib.error
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from build_version import VERSION as EMBEDDED_APP_VERSION
from fastman_img import (
    FastmanImgError,
    FastmanOperationCancelled,
    archive_contains as fastman_archive_contains,
    normalize_keys as normalize_fastman_keys,
    parse_fastman_archive,
    repack_fastman_archive,
)


if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
else:
    APP_DIR = Path(__file__).resolve().parent
    RESOURCE_DIR = APP_DIR
CATALOG_PATH = RESOURCE_DIR / "catalog.json"
BUILD_CONFIG_PATH = RESOURCE_DIR / "build_config.json"
PUBLIC_WEB_BASE_URL = "https://ug-mods-hub.qniks.me"
TRUSTED_ORIGIN_BASE_URL = "http://149.50.111.56:20069"
DEFAULT_WEB_BASE_URL = TRUSTED_ORIGIN_BASE_URL
DEFAULT_WEB_HEALTH_URL = DEFAULT_WEB_BASE_URL + "/api/health.php"
DEFAULT_CATALOG_URL = DEFAULT_WEB_BASE_URL + "/api/catalog.php"
DEFAULT_UPDATE_URL = DEFAULT_WEB_BASE_URL + "/api/latest.php"
DEFAULT_ACCOUNT_URL = DEFAULT_WEB_BASE_URL + "/api/account.php"
LEGACY_WEB_BASE_URL = PUBLIC_WEB_BASE_URL + "/main"
OFFICIAL_PUBLIC_KEY_SHA256 = "cff0f3e400b98b6b1a11b1065d02337dc3bc97674f6b25d7031d40876f3d4606"


def is_trusted_origin_url(value: str) -> bool:
    """Allow plain HTTP only for the owner's exact origin address."""
    try:
        parsed = urllib.parse.urlsplit(str(value).strip())
        return (
            parsed.scheme.lower() == "http"
            and parsed.hostname == "149.50.111.56"
            and parsed.port == 20069
            and not parsed.username
            and not parsed.password
        )
    except (TypeError, ValueError):
        return False


def is_allowed_remote_url(value: str) -> bool:
    try:
        parsed = urllib.parse.urlsplit(str(value).strip())
        if parsed.scheme.lower() == "https":
            return bool(parsed.hostname) and not parsed.username and not parsed.password
    except (TypeError, ValueError):
        return False
    return is_trusted_origin_url(value)


def origin_url_for_official(value: str) -> str:
    """Route official domain URLs through the direct origin when Cloudflare blocks the EXE."""
    value = str(value).strip()
    if is_trusted_origin_url(value):
        return value
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        return value
    if (
        parsed.scheme.lower() == "https"
        and parsed.hostname == "ug-mods-hub.qniks.me"
        and parsed.port in (None, 443)
        and not parsed.username
        and not parsed.password
    ):
        origin = urllib.parse.urlsplit(TRUSTED_ORIGIN_BASE_URL)
        return urllib.parse.urlunsplit((origin.scheme, origin.netloc, parsed.path, parsed.query, ""))
    return value


def user_data_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return base / "MTAModHub"


DATA_DIR = user_data_dir()
SETTINGS_PATH = DATA_DIR / "settings.json"
STATE_PATH = DATA_DIR / "installed.json"
BACKUP_DIR = DATA_DIR / "backups"
FAVORITES_PATH = DATA_DIR / "favorites.json"
HISTORY_PATH = DATA_DIR / "history.json"
IMG_KEY_CACHE_PATH = DATA_DIR / "img-access.dat"

_FASTMAN_ACCOUNT_URL = ""
_FASTMAN_ACCOUNT_TOKEN = ""
_FASTMAN_TEST_KEYS: dict[int, bytes] | None = None
ONLINE_CATALOG_PATH = DATA_DIR / "online_catalog.json"
SKIN_INDEX_PATH = RESOURCE_DIR / "skin_index.json"
SKIN_TEMPLATE_DIR = DATA_DIR / "img_templates"
ACCESSORY_INDEX_PATH = RESOURCE_DIR / "accessory_index.json"
ACCESSORY_TEMPLATE_DIR = DATA_DIR / "accessory_img_templates"
WEAPON_INDEX_PATH = RESOURCE_DIR / "weapon_index.json"
WEAPON_TEMPLATE_DIR = DATA_DIR / "weapon_img_templates"
AUTOSTART_TASK_NAME = "UG MOD HUB"
LEGACY_AUTOSTART_TASK_NAME = "MTA MOD HUB"
BUILTIN_OPTIMIZATION_IDS = {
    "optimization_no_grass",
    "optimization_low_effects",
    "optimization_low_map",
}
_SINGLE_INSTANCE_HANDLE = None


class ModError(RuntimeError):
    pass


class OperationCancelled(ModError):
    pass


@dataclass(frozen=True)
class SkinTarget:
    id: int
    name: str
    dff: str
    txd: str

    @property
    def dff_filename(self) -> str:
        return self.dff if self.dff.lower().endswith(".dff") else self.dff + ".dff"

    @property
    def txd_filename(self) -> str:
        return self.txd if self.txd.lower().endswith(".txd") else self.txd + ".txd"


@dataclass(frozen=True)
class AccessoryTarget(SkinTarget):
    key: str
    slot: str


@dataclass(frozen=True)
class WeaponTarget(SkinTarget):
    pass


@dataclass(frozen=True)
class ImgEntry:
    name: str
    offset_sector: int
    streaming_sectors: int
    archive_sectors: int
    directory_offset: int


def acquire_single_instance() -> bool:
    """Keep one UG MOD HUB window per signed-in Windows session."""
    global _SINGLE_INSTANCE_HANDLE
    if os.name != "nt":
        return True
    if _SINGLE_INSTANCE_HANDLE:
        return True
    import ctypes
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, "Local\\MTA_MOD_HUB_SINGLE_INSTANCE")
    if not handle:
        return True
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(handle)
        return False
    _SINGLE_INSTANCE_HANDLE = handle
    return True


def release_single_instance() -> None:
    global _SINGLE_INSTANCE_HANDLE
    if os.name == "nt" and _SINGLE_INSTANCE_HANDLE:
        import ctypes
        ctypes.windll.kernel32.CloseHandle(_SINGLE_INSTANCE_HANDLE)
    _SINGLE_INSTANCE_HANDLE = None


def _autostart_task_exists(task_name: str) -> bool:
    if os.name != "nt":
        return False
    result = subprocess.run(
        ["schtasks.exe", "/Query", "/TN", task_name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=0x08000000,
        check=False,
    )
    return result.returncode == 0


def autostart_enabled() -> bool:
    return _autostart_task_exists(AUTOSTART_TASK_NAME) or _autostart_task_exists(LEGACY_AUTOSTART_TASK_NAME)


def set_autostart(enabled: bool) -> None:
    if os.name != "nt":
        raise ModError("Автозапуск підтримується лише у Windows")
    if enabled:
        if not getattr(sys, "frozen", False):
            raise ModError("Автозапуск можна ввімкнути лише у зібраній EXE-версії")
        if _autostart_task_exists(LEGACY_AUTOSTART_TASK_NAME):
            subprocess.run(
                ["schtasks.exe", "/Delete", "/TN", LEGACY_AUTOSTART_TASK_NAME, "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=0x08000000,
                check=False,
            )
        command = [
            "schtasks.exe", "/Create",
            "/TN", AUTOSTART_TASK_NAME,
            "/SC", "ONLOGON",
            "/RL", "HIGHEST",
            "/TR", f'"{Path(sys.executable).resolve()}"',
            "/F",
        ]
    else:
        existing = [name for name in (AUTOSTART_TASK_NAME, LEGACY_AUTOSTART_TASK_NAME) if _autostart_task_exists(name)]
        if not existing:
            return
        for task_name in existing:
            result = subprocess.run(
                ["schtasks.exe", "/Delete", "/TN", task_name, "/F"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=0x08000000,
                check=False,
            )
            if result.returncode != 0:
                raise ModError(f"Не вдалося вимкнути автозапуск Windows (код {result.returncode})")
        return
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=0x08000000,
        check=False,
    )
    if result.returncode != 0:
        action = "увімкнути" if enabled else "вимкнути"
        raise ModError(f"Не вдалося {action} автозапуск Windows (код {result.returncode})")


@dataclass(frozen=True)
class Mod:
    id: str
    title: str
    category: str
    description: str
    destination: str
    accent: str = "#00b9aa"
    repeat_before_launch: bool = False
    exclusive_group: str | None = None
    hash_guard: bool = False
    user_defined: bool = False
    cover: str | None = None
    mod_type: str = "files"

    @classmethod
    def from_dict(cls, raw: dict) -> "Mod":
        fields = cls.__dataclass_fields__
        return cls(**{key: value for key, value in raw.items() if key in fields})


def _read_json(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return fallback


def is_dev_mode() -> bool:
    if (APP_DIR / "release.lock").exists():
        return False
    config = _read_json(BUILD_CONFIG_PATH, {"dev_mode": True})
    return bool(config.get("dev_mode", True))


def _atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.stem, suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def load_catalog() -> tuple[dict, list[Mod]]:
    release_catalog = APP_DIR / "release_catalog.json"
    if not is_dev_mode() and release_catalog.exists():
        raw = _read_json(release_catalog, {"app_name": "UG MOD HUB", "mods": []})
    else:
        raw = _read_json(CATALOG_PATH, {"app_name": "UG MOD HUB", "mods": []})
    # In an onedir build catalog.json is an external data file.  The updater
    # replaces the executable only, so that file can legitimately remain from
    # an older installation.  The compiled build identity is authoritative for
    # update comparisons and the version shown in the UI.
    if getattr(sys, "frozen", False):
        raw = dict(raw)
        raw["version"] = EMBEDDED_APP_VERSION
    if not is_dev_mode():
        # Після першої синхронізації сайт є єдиним джерелом каталогу.
        # Це прибирає моди, які адміністратор уже видалив або перейменував на сайті.
        online = _read_json(ONLINE_CATALOG_PATH, None)
        if isinstance(online, dict) and isinstance(online.get("mods"), list):
            return raw, [Mod.from_dict(item) for item in online["mods"] if item.get("id")]
        return raw, [Mod.from_dict(item) for item in raw.get("mods", []) if item.get("id")]
    online = _read_json(DATA_DIR / "online_catalog.json", {"mods": []})
    custom = _read_json(DATA_DIR / "custom_mods.json", {"mods": []})
    merged = list(raw.get("mods", [])) + list(online.get("mods", [])) + list(custom.get("mods", []))
    by_id = {item["id"]: item for item in merged if item.get("id")}
    return raw, [Mod.from_dict(item) for item in by_id.values()]


def _parse_skin_names(text: str) -> dict[int, str]:
    pattern = re.compile(r'\[\s*(\d+)\s*\]\s*=\s*"([^\"]*)"')
    return {int(match.group(1)): match.group(2).strip() for match in pattern.finditer(text)}


def _parse_peds_ide(text: str) -> dict[int, tuple[str, str]]:
    result: dict[int, tuple[str, str]] = {}
    for raw in text.splitlines():
        parts = [part.strip() for part in raw.split(",")]
        if len(parts) >= 3 and parts[0].isdigit():
            result[int(parts[0])] = (parts[1], parts[2])
    return result


def load_skin_targets(game_root: str | Path | None = None) -> list[SkinTarget]:
    """Load the bundled target list and refresh user-facing names from ShSkin.lua."""
    bundled = _read_json(SKIN_INDEX_PATH, {"skins": []})
    rows: dict[int, SkinTarget] = {}
    for raw in bundled.get("skins", []):
        try:
            target = SkinTarget(
                int(raw["id"]), str(raw["name"]).strip(),
                str(raw["dff"]).strip(), str(raw["txd"]).strip(),
            )
        except (KeyError, TypeError, ValueError):
            continue
        if target.name and target.dff and target.txd:
            rows[target.id] = target

    if game_root:
        try:
            root = resolve_game_root(game_root)
            lua_candidates = [
                root / "game/mods/deathmatch/resources/interfacer/Extend/ShSkin.lua",
                root / "game/mods/deathmatch/resources/ugta_game_business/Files/ShSkin.lua",
            ]
            names: dict[int, str] = {}
            for path in lua_candidates:
                if path.is_file():
                    names.update(_parse_skin_names(path.read_text(encoding="utf-8", errors="replace")))
            peds_path = root / "game/bin/data/peds.ide"
            local_models = {}
            if peds_path.is_file():
                local_models = _parse_peds_ide(peds_path.read_text(encoding="utf-8", errors="replace"))
            for skin_id, name in names.items():
                previous = rows.get(skin_id)
                model = local_models.get(skin_id)
                if model and model[0].lower() != "null":
                    rows[skin_id] = SkinTarget(skin_id, name or str(skin_id), model[0], model[1])
                elif previous:
                    rows[skin_id] = SkinTarget(previous.id, name or previous.name, previous.dff, previous.txd)
        except (OSError, ModError):
            pass
    return sorted(rows.values(), key=lambda item: (item.name.casefold(), item.id))


def load_weapon_targets(game_root: str | Path | None = None) -> list[WeaponTarget]:
    """Load the bundled list of replaceable weapon models from open gta3.img."""
    bundled = _read_json(WEAPON_INDEX_PATH, {"weapons": []})
    targets: list[WeaponTarget] = []
    for raw in bundled.get("weapons", []):
        try:
            target = WeaponTarget(
                int(raw["id"]), str(raw["name"]).strip(),
                str(raw["dff"]).strip(), str(raw["txd"]).strip(),
            )
        except (KeyError, TypeError, ValueError):
            continue
        if target.name and target.dff and target.txd:
            targets.append(target)
    return sorted(targets, key=lambda item: (item.name.casefold(), item.id))


def read_img_directory(path: str | Path) -> dict[str, ImgEntry]:
    """Read a standard open GTA IMG v2 directory without loading archive data."""
    archive = Path(path).expanduser().resolve()
    if not archive.is_file():
        raise ModError(f"IMG-шаблон не знайдено: {archive}")
    result: dict[str, ImgEntry] = {}
    try:
        with archive.open("rb") as handle:
            if handle.read(4) != b"VER2":
                raise ModError(
                    f"«{archive.name}» зашифрований або має непідтримуваний формат. "
                    "Потрібен відкритий IMG із сигнатурою VER2."
                )
            raw_count = handle.read(4)
            if len(raw_count) != 4:
                raise ModError("Пошкоджений заголовок IMG")
            count = struct.unpack("<I", raw_count)[0]
            if count <= 0 or count > 500_000:
                raise ModError("Некоректна кількість записів IMG")
            for _ in range(count):
                directory_offset = handle.tell()
                raw = handle.read(32)
                if len(raw) != 32:
                    raise ModError("Пошкоджений каталог IMG")
                offset, streaming, archived = struct.unpack("<IHH", raw[:8])
                name = raw[8:32].split(b"\0", 1)[0].decode("ascii", errors="ignore").strip()
                if name:
                    result[name.casefold()] = ImgEntry(name, offset, streaming, archived, directory_offset)
    except OSError as exc:
        raise ModError(f"Не вдалося прочитати IMG: {exc}") from exc
    return result


def img_contains_skin(path: str | Path, target: SkinTarget) -> bool:
    entries = read_img_directory(path)
    return target.dff_filename.casefold() in entries and target.txd_filename.casefold() in entries


def skin_template_candidates(settings: dict | None = None) -> list[Path]:
    settings = settings or {}
    candidates = []
    for key in ("skin_gta3_template", "skin_peds_template"):
        value = str(settings.get(key, "")).strip()
        if value:
            candidates.append(Path(value).expanduser())
    candidates.extend([
        SKIN_TEMPLATE_DIR / "gta3.img",
        SKIN_TEMPLATE_DIR / "PEDS.img",
        APP_DIR / "img_templates/gta3.img",
        APP_DIR / "img_templates/PEDS.img",
    ])
    unique: list[Path] = []
    seen = set()
    for candidate in candidates:
        key = str(candidate).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def find_skin_template(target: SkinTarget, settings: dict | None = None) -> tuple[str, Path] | None:
    for candidate in skin_template_candidates(settings):
        if not candidate.is_file():
            continue
        try:
            if img_contains_skin(candidate, target):
                name = "PEDS.img" if "peds" in candidate.name.casefold() else "gta3.img"
                return name, candidate.resolve()
        except ModError:
            continue
    return None


def _parse_accessory_info(text: str) -> dict[int, tuple[str, str, str]]:
    result = {}
    pattern = re.compile(r"(?ms)^\s{4}([A-Za-z0-9_]+)\s*=\s*\{(.*?)^\s{4}\}[;,]")
    string_pattern = r'"((?:\\.|[^"\\])*)"'

    def lua_string(field: str, body: str) -> str:
        match = re.search(rf"\b{field}\s*=\s*{string_pattern}", body)
        if not match:
            return ""
        return (match.group(1)
                .replace(r'\"', '"')
                .replace(r'\n', '\n')
                .replace(r'\t', '\t')
                .replace(r'\\', '\\')
                .strip())

    for match in pattern.finditer(text):
        key, body = match.groups()
        model = re.search(r"\bmodel\s*=\s*(\d+)", body)
        name = lua_string("name", body)
        slot = lua_string("slot", body)
        if model and name:
            result[int(model.group(1))] = (
                key, name, slot,
            )
    return result


def load_accessory_targets(game_root: str | Path | None = None) -> list[AccessoryTarget]:
    bundled = _read_json(ACCESSORY_INDEX_PATH, {"accessories": []})
    rows: dict[int, AccessoryTarget] = {}
    for raw in bundled.get("accessories", []):
        try:
            target = AccessoryTarget(
                int(raw["id"]), str(raw["name"]).strip(),
                str(raw["dff"]).strip(), str(raw["txd"]).strip(),
                str(raw.get("key", "")).strip(), str(raw.get("slot", "")).strip(),
            )
        except (KeyError, TypeError, ValueError):
            continue
        if target.name and target.dff and target.txd:
            rows[target.id] = target

    if game_root:
        try:
            root = resolve_game_root(game_root)
            lua = root / "game/mods/deathmatch/resources/interfacer/Extend/ShAccessories.lua"
            if lua.is_file():
                current = _parse_accessory_info(lua.read_text(encoding="utf-8", errors="replace"))
                for model_id, (key, name, slot) in current.items():
                    previous = rows.get(model_id)
                    if previous:
                        rows[model_id] = AccessoryTarget(
                            model_id, name or previous.name, previous.dff, previous.txd,
                            key or previous.key, slot or previous.slot,
                        )
        except (OSError, ModError):
            pass
    return sorted(rows.values(), key=lambda item: (item.name.casefold(), item.id))


def accessory_template_candidates(settings: dict | None = None) -> list[Path]:
    settings = settings or {}
    candidates = []
    configured = str(settings.get("accessory_acs_template", "")).strip()
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.extend([
        ACCESSORY_TEMPLATE_DIR / "acs.img",
        RESOURCE_DIR / "img_templates/acs.img",
        APP_DIR / "img_templates/acs.img",
    ])
    result = []
    seen = set()
    for candidate in candidates:
        key = str(candidate).casefold()
        if key not in seen:
            seen.add(key)
            result.append(candidate)
    return result


def find_accessory_template(target: AccessoryTarget, settings: dict | None = None) -> Path | None:
    for candidate in accessory_template_candidates(settings):
        if not candidate.is_file():
            continue
        try:
            if img_contains_skin(candidate, target):
                return candidate.resolve()
        except ModError:
            continue
    return None


def load_favorites() -> set[str]:
    raw = _read_json(DATA_DIR / "favorites.json", {"mods": []})
    return {str(item) for item in raw.get("mods", []) if item}


def set_favorite(mod_id: str, enabled: bool) -> set[str]:
    favorites = load_favorites()
    if enabled:
        favorites.add(mod_id)
    else:
        favorites.discard(mod_id)
    _atomic_json(DATA_DIR / "favorites.json", {"mods": sorted(favorites)})
    return favorites


def load_history() -> list[dict]:
    return list(_read_json(DATA_DIR / "history.json", {"items": []}).get("items", []))


def record_history(action: str, mod: Mod | None = None, details: str = "") -> dict:
    item = {
        "time": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "mod_id": mod.id if mod else "",
        "title": mod.title if mod else "",
        "details": details,
    }
    items = load_history()
    items.insert(0, item)
    _atomic_json(DATA_DIR / "history.json", {"items": items[:500]})
    return item


def _slug(value: str) -> str:
    aliases = str.maketrans({
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e",
        "ё": "e", "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k",
        "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
        "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c",
        "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "",
        "э": "e", "ю": "yu", "я": "ya", "і": "i", "ї": "yi", "є": "ye",
    })
    value = value.lower().translate(aliases)
    value = re.sub(r"[^a-z0-9]+", "_", value).strip("_")
    return value or "custom_mod"


def validate_destination(value: str) -> str:
    normalized = value.strip().replace("\\", "/").strip("/")
    path = Path(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts:
        raise ModError("Укажіть безпечний відносний шлях усередині UKRAINEGTA")
    if path.parts[0].lower() != "game":
        raise ModError("Шлях призначення має починатися з game/")
    return path.as_posix()


def add_custom_mod(
    title: str,
    category: str,
    destination: str,
    source_folder: str | Path,
    description: str = "Користувацький мод",
    repeat_before_launch: bool = False,
    exclusive_group: str | None = None,
    skip_previews: bool = True,
    cover_file: str | Path | None = None,
) -> Mod:
    title = title.strip()
    category = category.strip() or "Інше"
    if not title:
        raise ModError("Введіть назву мода")
    destination = validate_destination(destination)
    source = Path(source_folder).expanduser().resolve()
    if not source.is_dir():
        raise ModError("Виберіть папку з файлами мода")

    existing = {mod.id for mod in load_catalog()[1]}
    base_id = _slug(title)
    mod_id = base_id
    suffix = 2
    while mod_id in existing:
        mod_id = f"{base_id}_{suffix}"
        suffix += 1

    target_root = DATA_DIR / "mods" / mod_id
    target_payload = target_root / "payload"
    preview_extensions = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
    copied = 0
    try:
        target_payload.mkdir(parents=True, exist_ok=False)
        for item in source.rglob("*"):
            if not item.is_file() or item.is_symlink():
                continue
            if skip_previews and item.suffix.lower() in preview_extensions:
                continue
            relative = item.relative_to(source)
            output = target_payload / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, output)
            copied += 1
        if not copied:
            raise ModError("У вибраній папці немає ігрових файлів")

        cover_name = _copy_cover(cover_file, target_root)

        custom_path = DATA_DIR / "custom_mods.json"
        custom = _read_json(custom_path, {"mods": []})
        raw = {
            "id": mod_id,
            "title": title,
            "category": category,
            "description": description.strip() or "Користувацький мод",
            "destination": destination,
            "accent": "#b26cff",
            "repeat_before_launch": bool(repeat_before_launch),
            "exclusive_group": exclusive_group or None,
            "hash_guard": category in {"Кров + звук влучання", "Приціл", "HUD"},
            "user_defined": True,
            "cover": cover_name,
        }
        custom.setdefault("mods", []).append(raw)
        _atomic_json(custom_path, custom)
        return Mod.from_dict(raw)
    except Exception:
        shutil.rmtree(target_root, ignore_errors=True)
        raise


def ensure_editable_payload(mod_id: str) -> Path:
    """Create a writable DEV copy of a packaged payload when needed."""
    if not is_dev_mode():
        raise ModError("Редагування файлів доступне лише у DEV-збірці")
    target = DATA_DIR / "mods" / mod_id / "payload"
    if target.exists():
        return target
    source = APP_DIR / "mods" / mod_id / "payload"
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.exists():
        shutil.copytree(source, target)
    else:
        target.mkdir(parents=True)
    return target


def _copy_cover(cover_file: str | Path | None, target_root: Path) -> str | None:
    if not cover_file or not str(cover_file).strip():
        return None
    source = Path(cover_file).expanduser().resolve()
    if not source.is_file() or source.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
        raise ModError("Обкладинка повинна бути файлом PNG або JPG")
    target_root.mkdir(parents=True, exist_ok=True)
    normalized_name = "cover" + source.suffix.lower().replace(".jpeg", ".jpg")
    if source == (target_root / normalized_name).resolve():
        return normalized_name
    for old in target_root.glob("cover.*"):
        old.unlink(missing_ok=True)
    name = normalized_name
    shutil.copy2(source, target_root / name)
    return name


def cover_path(mod: Mod) -> Path | None:
    names = [mod.cover] if mod.cover else []
    names.extend(["cover.png", "cover.jpg", "cover.jpeg"])
    roots = [DATA_DIR / "mods" / mod.id, APP_DIR / "mods" / mod.id]
    for root in roots:
        for name in names:
            if name and (root / name).is_file():
                return root / name
    return None


def update_dev_mod(
    mod_id: str,
    title: str,
    category: str,
    destination: str,
    description: str = "Користувацький мод",
    repeat_before_launch: bool = False,
    exclusive_group: str | None = None,
    source_folder: str | Path | None = None,
    skip_previews: bool = True,
    cover_file: str | Path | None = None,
) -> Mod:
    if not is_dev_mode():
        raise ModError("Редагування модів доступне лише у DEV-збірці")
    current = next((mod for mod in load_catalog()[1] if mod.id == mod_id), None)
    if current is None:
        raise ModError("Мод не знайдено")

    title = title.strip()
    category = category.strip() or "Інше"
    if not title:
        raise ModError("Введіть назву мода")
    destination = validate_destination(destination)

    source = None
    if source_folder and str(source_folder).strip():
        source = Path(source_folder).expanduser().resolve()
        if not source.is_dir():
            raise ModError("Виберіть папку з файлами мода")

    if source is not None:
        target_root = DATA_DIR / "mods" / mod_id
        staging = DATA_DIR / "mods" / f".{mod_id}.editing"
        backup = DATA_DIR / "mods" / f".{mod_id}.backup"
        if staging.exists():
            shutil.rmtree(staging)
        if backup.exists():
            shutil.rmtree(backup)
        staging_payload = staging / "payload"
        staging_payload.mkdir(parents=True)
        existing_cover = cover_path(current)
        if existing_cover and existing_cover.parent == target_root:
            shutil.copy2(existing_cover, staging / existing_cover.name)
        preview_extensions = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
        copied = 0
        try:
            for item in source.rglob("*"):
                if not item.is_file() or item.is_symlink():
                    continue
                if skip_previews and item.suffix.lower() in preview_extensions:
                    continue
                output = staging_payload / item.relative_to(source)
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, output)
                copied += 1
            if not copied:
                raise ModError("У вибраній папці немає ігрових файлів")
            if target_root.exists():
                target_root.replace(backup)
            try:
                staging.replace(target_root)
            except Exception:
                if backup.exists() and not target_root.exists():
                    backup.replace(target_root)
                raise
            shutil.rmtree(backup, ignore_errors=True)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    cover_name = current.cover
    if cover_file and str(cover_file).strip():
        cover_name = _copy_cover(cover_file, DATA_DIR / "mods" / mod_id)

    raw = {
        "id": current.id,
        "title": title,
        "category": category,
        "description": description.strip() or "Користувацький мод",
        "destination": destination,
        "accent": current.accent,
        "repeat_before_launch": bool(repeat_before_launch),
        "exclusive_group": exclusive_group or None,
        "hash_guard": category in {"Кров + звук влучання", "Приціл", "HUD"},
        "user_defined": True,
        "cover": cover_name,
    }
    custom_path = DATA_DIR / "custom_mods.json"
    custom = _read_json(custom_path, {"mods": []})
    custom["mods"] = [item for item in custom.get("mods", []) if item.get("id") != mod_id]
    custom["mods"].append(raw)
    _atomic_json(custom_path, custom)
    return Mod.from_dict(raw)


def delete_custom_mod(mod_id: str) -> None:
    custom_path = DATA_DIR / "custom_mods.json"
    custom = _read_json(custom_path, {"mods": []})
    matches = [item for item in custom.get("mods", []) if item.get("id") == mod_id]
    if not matches:
        raise ModError("Користувацький мод не знайдено")
    if mod_id in load_state().get("installed", {}):
        raise ModError("Спочатку видаліть цей мод із гри на вкладці «Встановлено»")
    custom["mods"] = [item for item in custom.get("mods", []) if item.get("id") != mod_id]
    _atomic_json(custom_path, custom)
    target = (DATA_DIR / "mods" / mod_id).resolve()
    mods_root = (DATA_DIR / "mods").resolve()
    if _is_inside(target, mods_root):
        shutil.rmtree(target, ignore_errors=True)


def export_locked_release(version: str, destination_parent: str | Path | None = None) -> Path:
    if not is_dev_mode():
        raise ModError("Експорт доступний лише у DEV-збірці")
    if not getattr(sys, "frozen", False):
        raise ModError("Спочатку зберіть DEV-версію програми")
    version = version.strip()
    if not re.fullmatch(r"\d+\.\d+(?:\.\d+)?", version):
        raise ModError("Версія повинна мати формат 1.2 або 1.2.3")

    parent = Path(destination_parent).resolve() if destination_parent else APP_DIR.parent
    target = parent / f"UG MOD HUB {version}"
    if target.exists():
        raise ModError(f"Папка вже існує: {target.name}")
    target.mkdir(parents=True)

    try:
        for source in APP_DIR.rglob("*"):
            if not source.is_file():
                continue
            relative = source.relative_to(APP_DIR)
            if relative.as_posix() in {"release.lock", "release_catalog.json"}:
                continue
            output = target / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(source, output)
            except OSError:
                shutil.copy2(source, output)

        catalog_meta, mods = load_catalog()
        frozen_catalog = {
            "app_name": catalog_meta.get("app_name", "UG MOD HUB"),
            "version": version,
            "mods": [],
        }
        for mod in mods:
            raw = asdict(mod)
            raw["user_defined"] = False
            frozen_catalog["mods"].append(raw)
            source_payload = payload_dir(mod.id)
            if source_payload.exists():
                target_payload = target / "mods" / mod.id / "payload"
                for source in source_payload.rglob("*"):
                    if not source.is_file() or source.is_symlink():
                        continue
                    output = target_payload / source.relative_to(source_payload)
                    output.parent.mkdir(parents=True, exist_ok=True)
                    if output.exists():
                        output.unlink()
                    try:
                        os.link(source, output)
                    except OSError:
                        shutil.copy2(source, output)
            source_cover = cover_path(mod)
            if source_cover:
                target_cover = target / "mods" / mod.id / source_cover.name
                target_cover.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_cover, target_cover)

        _atomic_json(target / "release_catalog.json", frozen_catalog)
        _atomic_json(target / "release.lock", {"locked": True, "version": version})
        return target
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise


def load_settings() -> dict:
    defaults = {
        "game_root": "",
        "game_exe": "",
        "resource_guard": True,
        "animation_mode": "Повні",
        "fps_counter": False,
        "catalog_url": DEFAULT_CATALOG_URL,
        "catalog_public_key": "",
        "update_url": DEFAULT_UPDATE_URL,
        "health_url": DEFAULT_WEB_HEALTH_URL,
        "account_url": DEFAULT_ACCOUNT_URL,
        "account_token": "",
        "account_user": {},
    }
    defaults.update(_read_json(SETTINGS_PATH, {}))
    endpoints = {
        "health_url": DEFAULT_WEB_HEALTH_URL,
        "catalog_url": DEFAULT_CATALOG_URL,
        "update_url": DEFAULT_UPDATE_URL,
        "account_url": DEFAULT_ACCOUNT_URL,
    }
    for key, current in endpoints.items():
        value = str(defaults.get(key, "")).strip()
        if (
            not value
            or value.startswith(LEGACY_WEB_BASE_URL)
            or value.startswith(PUBLIC_WEB_BASE_URL + "/api/")
        ):
            defaults[key] = current
    return defaults


def save_settings(settings: dict) -> None:
    _atomic_json(SETTINGS_PATH, settings)


def protect_local_secret(value: str) -> str:
    """Protect an opaque login token with Windows DPAPI for the current user."""
    if not value:
        return ""
    raw = value.encode("utf-8")
    if os.name != "nt":
        return "local:" + base64.b64encode(raw).decode("ascii")
    try:
        import ctypes
        from ctypes import wintypes

        class DataBlob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

        buffer = ctypes.create_string_buffer(raw)
        source = DataBlob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
        output = DataBlob()
        if not ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(source), "UG MOD HUB", None, None, None, 0, ctypes.byref(output)
        ):
            raise ctypes.WinError()
        try:
            protected = ctypes.string_at(output.pbData, output.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(output.pbData)
        return "dpapi:" + base64.b64encode(protected).decode("ascii")
    except Exception as exc:
        raise ModError(f"Не вдалося захистити токен профілю: {exc}") from exc


def unprotect_local_secret(value: str) -> str:
    if not value:
        return ""
    if value.startswith("local:") and os.name != "nt":
        try:
            return base64.b64decode(value[6:]).decode("utf-8")
        except Exception:
            return ""
    if not value.startswith("dpapi:") or os.name != "nt":
        return ""
    try:
        import ctypes
        from ctypes import wintypes

        class DataBlob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

        encrypted = base64.b64decode(value[6:])
        buffer = ctypes.create_string_buffer(encrypted)
        source = DataBlob(len(encrypted), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
        output = DataBlob()
        if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(source), None, None, None, None, 0, ctypes.byref(output)
        ):
            return ""
        try:
            return ctypes.string_at(output.pbData, output.cbData).decode("utf-8")
        finally:
            ctypes.windll.kernel32.LocalFree(output.pbData)
    except Exception:
        return ""


def account_api_request(
    account_url: str,
    action: str,
    payload: dict | None = None,
    token: str = "",
    timeout: float = 10.0,
) -> dict:
    account_url = origin_url_for_official(str(account_url).strip())
    if not is_allowed_remote_url(account_url):
        raise ModError("Адреса авторизації не належить дозволеному серверу")
    method = "POST" if payload is not None else "GET"
    separator = "&" if "?" in account_url else "?"
    url = account_url + separator + urllib.parse.urlencode({"action": action})
    headers = {"Accept": "application/json", "User-Agent": f"UG-MOD-HUB/{EMBEDDED_APP_VERSION}"}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json; charset=utf-8"
        body = dict(payload)
        body["action"] = action
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=max(2.0, float(timeout))) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            result = json.loads(exc.read().decode("utf-8"))
        except Exception:
            result = {"error": f"Сервер повернув помилку HTTP {exc.code}"}
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise ModError(f"Немає зв’язку із сервісом профілів: {exc}") from exc
    if not isinstance(result, dict) or not result.get("ok"):
        raise ModError(str(result.get("error") if isinstance(result, dict) else "Некоректна відповідь сервера"))
    return result


def register_account(account_url: str, email: str, display_name: str, password: str) -> dict:
    return account_api_request(account_url, "register", {
        "email": email, "display_name": display_name, "password": password,
    })


def login_account(account_url: str, email: str, password: str) -> dict:
    return account_api_request(account_url, "login", {
        "email": email, "password": password, "device_name": "UG MOD HUB для Windows",
    })


def load_account(account_url: str, token: str) -> dict:
    return account_api_request(account_url, "me", token=token)


def logout_account(account_url: str, token: str) -> None:
    if token:
        account_api_request(account_url, "logout", {}, token=token)


def resend_account_email(account_url: str, email: str) -> dict:
    return account_api_request(account_url, "resend", {"email": email})


def configure_fastman_access(account_url: str, token: str) -> None:
    """Keep the authenticated IMG-key endpoint in process memory only."""
    global _FASTMAN_ACCOUNT_URL, _FASTMAN_ACCOUNT_TOKEN
    _FASTMAN_ACCOUNT_URL = str(account_url).strip()
    _FASTMAN_ACCOUNT_TOKEN = str(token).strip()


def clear_fastman_access(clear_cache: bool = True) -> None:
    global _FASTMAN_ACCOUNT_URL, _FASTMAN_ACCOUNT_TOKEN
    _FASTMAN_ACCOUNT_URL = ""
    _FASTMAN_ACCOUNT_TOKEN = ""
    if clear_cache:
        IMG_KEY_CACHE_PATH.unlink(missing_ok=True)


def set_fastman_test_keys(payload: dict | None) -> None:
    """Inject synthetic test keys without adding production secrets to source."""
    global _FASTMAN_TEST_KEYS
    _FASTMAN_TEST_KEYS = normalize_fastman_keys(payload) if payload is not None else None


def _read_cached_fastman_keys() -> dict[int, bytes] | None:
    try:
        protected = IMG_KEY_CACHE_PATH.read_text(encoding="utf-8").strip()
        raw = unprotect_local_secret(protected)
        payload = json.loads(raw) if raw else None
        return normalize_fastman_keys(payload) if isinstance(payload, dict) else None
    except (OSError, ValueError, json.JSONDecodeError, FastmanImgError):
        IMG_KEY_CACHE_PATH.unlink(missing_ok=True)
        return None


def _cache_fastman_keys(payload: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    protected = protect_local_secret(json.dumps(payload, separators=(",", ":")))
    temporary = IMG_KEY_CACHE_PATH.with_name(IMG_KEY_CACHE_PATH.name + ".tmp")
    try:
        temporary.write_text(protected, encoding="utf-8")
        os.replace(temporary, IMG_KEY_CACHE_PATH)
    finally:
        temporary.unlink(missing_ok=True)


def _fastman_keys() -> dict[int, bytes]:
    if _FASTMAN_TEST_KEYS is not None:
        return dict(_FASTMAN_TEST_KEYS)
    cached = _read_cached_fastman_keys()
    if cached is not None:
        return cached
    if not _FASTMAN_ACCOUNT_URL or not _FASTMAN_ACCOUNT_TOKEN:
        raise ModError("Для автоматичної заміни в IMG увійдіть у підтверджений профіль UG MOD HUB")
    result = account_api_request(
        _FASTMAN_ACCOUNT_URL, "img_keys", token=_FASTMAN_ACCOUNT_TOKEN, timeout=12.0
    )
    payload = result.get("keys")
    if not isinstance(payload, dict):
        raise ModError("Сервер не повернув ключ доступу до IMG")
    try:
        keys = normalize_fastman_keys(payload)
    except FastmanImgError as exc:
        raise ModError(str(exc)) from exc
    _cache_fastman_keys(payload)
    return keys


def check_web_hosting(health_url: str = DEFAULT_WEB_HEALTH_URL, timeout: float = 8.0) -> dict:
    """Require a healthy UG MOD HUB API before allowing the desktop client to open."""
    health_url = health_url.strip()
    if not is_allowed_remote_url(health_url):
        raise ModError("Адреса перевірки вебсервера має використовувати HTTPS")
    request = urllib.request.Request(
        health_url,
        headers={
            "Accept": "application/json",
            "Cache-Control": "no-cache",
            "User-Agent": "UG-MOD-HUB/3.4",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=max(2.0, float(timeout))) as response:
            status = getattr(response, "status", None)
            if status is None:
                status = response.getcode()
            status = int(status)
            if status != 200:
                raise ModError(f"Вебсервер повернув HTTP {status}")
            payload = json.loads(response.read(128 * 1024).decode("utf-8"))
    except ModError:
        raise
    except urllib.error.HTTPError as exc:
        raise ModError(f"Вебсервер недоступний: HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
        reason = getattr(exc, "reason", exc)
        raise ModError(f"Немає зв’язку з вебсервером: {reason}") from exc
    if not isinstance(payload, dict) or payload.get("ok") is not True or payload.get("service") != "UG MOD HUB":
        raise ModError("Вебсервер UG MOD HUB не готовий до роботи")
    if is_trusted_origin_url(health_url):
        public_key = str(payload.get("public_key", "")).strip()
        fingerprint = hashlib.sha256(public_key.encode("utf-8")).hexdigest()
        if not public_key or fingerprint != OFFICIAL_PUBLIC_KEY_SHA256:
            raise ModError("Не вдалося підтвердити ключ цифрового підпису вебсервера")
    return payload


def load_state() -> dict:
    return _read_json(STATE_PATH, {"installed": {}})


def save_state(state: dict) -> None:
    _atomic_json(STATE_PATH, state)


def installed_mod_entries(mods: Iterable[Mod] | None = None) -> list[Mod]:
    """Return installed cards, including mods removed from the web catalog."""
    installed = load_state().get("installed", {})
    known = {mod.id: mod for mod in (mods or load_catalog()[1])}
    result: list[Mod] = []
    for mod_id, record in installed.items():
        mod = known.get(mod_id)
        if mod is None:
            mod = Mod(
                id=mod_id,
                title=str(record.get("title") or mod_id),
                category="Інше",
                description="Застарілий встановлений мод. Його вже немає в онлайн-каталозі, але оригінальні файли можна відновити.",
                destination="game",
                accent="#ef5f76",
            )
        result.append(mod)
    return result


def payload_dir(mod_id: str) -> Path:
    custom = DATA_DIR / "mods" / mod_id / "payload"
    # The online catalog is allowed to rename or remove a mod while an older
    # release is still installed.  Its verified payload remains the reference
    # copy used by the pre-launch repair, so never hide that cache merely
    # because the current catalog no longer contains the old id.
    if custom.exists():
        return custom
    if is_dev_mode():
        return APP_DIR / "mods" / mod_id / "payload"
    return APP_DIR / "mods" / mod_id / "payload"


def ensure_optimization_payloads(game_root: str | Path) -> dict[str, int]:
    """Create reversible optimization payloads from this game installation."""
    ok, message = validate_game_root(game_root)
    if not ok:
        raise ModError(message)
    root = resolve_game_root(game_root)
    generated: dict[str, int] = {}

    plants_source = root / "game/bin/data/plants.dat"
    if plants_source.is_file():
        target = DATA_DIR / "mods/optimization_no_grass/payload/plants.dat"
        target.parent.mkdir(parents=True, exist_ok=True)
        content = plants_source.read_text(encoding="utf-8", errors="ignore")
        disabled = [
            line if not line.strip() or line.lstrip().startswith(";") else "; UG MOD HUB: " + line
            for line in content.splitlines()
        ]
        target.write_text("\n".join(disabled) + "\n", encoding="utf-8")
        generated["optimization_no_grass"] = 1

    stream_source = root / "game/bin/stream.ini"
    if stream_source.is_file():
        target = DATA_DIR / "mods/optimization_low_effects/payload/stream.ini"
        target.parent.mkdir(parents=True, exist_ok=True)
        content = stream_source.read_text(encoding="utf-8", errors="ignore")
        content, radiosity_count = re.subn(
            r"(?im)^\s*pe_bRadiosity\s+\S+\s*$", "pe_bRadiosity   0", content
        )
        content, vehicles_count = re.subn(
            r"(?im)^\s*vehicles\s+\d+\s*$", "vehicles\t4", content
        )
        if not radiosity_count:
            content += "\npe_bRadiosity   0"
        if not vehicles_count:
            content += "\nvehicles\t4"
        target.write_text(content.rstrip() + "\n", encoding="utf-8")
        generated["optimization_low_effects"] = 1

    low_map_source = root / "game/bin/data/gta_low.dat"
    if low_map_source.is_file():
        target = DATA_DIR / "mods/optimization_low_map/payload/gta.dat"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(low_map_source, target)
        generated["optimization_low_map"] = 1
    return generated


def payload_files(mod_id: str) -> list[Path]:
    root = payload_dir(mod_id)
    if not root.exists():
        return []
    return [
        path for path in root.rglob("*")
        if path.is_file() and not path.name.startswith((".", "_"))
    ]


def file_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_sha256_with_progress(path: Path, on_chunk, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
            on_chunk(len(chunk))
    return digest.hexdigest()


def sync_managed_resources(
    game_root: str | Path,
    expected_hashes: dict[str, tuple[int, int, str]] | None = None,
) -> dict:
    """Restore changed manager-owned files below deathmatch/resources.

    expected_hashes caches source hashes as (size, mtime_ns, sha256). Target files
    are always hashed, so a same-size server replacement is still detected.
    """
    ok, message = validate_game_root(game_root)
    if not ok:
        raise ModError(message)
    root = resolve_game_root(game_root)
    resources_root = (root / "game/mods/deathmatch/resources").resolve()
    state_document = load_state()
    state = state_document.get("installed", {})
    guarded_ids = {mod.id for mod in load_catalog()[1] if mod.hash_guard}
    cache = expected_hashes if expected_hashes is not None else {}
    checked = repaired = 0
    state_dirty = False
    errors: list[str] = []

    for mod_id, record in state.items():
        if mod_id not in guarded_ids:
            continue
        destination = Path(record.get("destination", "")).resolve()
        source_root = payload_dir(mod_id)
        for item in record.get("files", []):
            relative = Path(item.get("relative", ""))
            source = (source_root / relative).resolve()
            target = (destination / relative).resolve()
            if not _is_inside(target, resources_root) or not source.is_file():
                continue
            checked += 1
            try:
                stat = source.stat()
                cache_key = str(source).lower()
                cached = cache.get(cache_key)
                signature = (stat.st_size, stat.st_mtime_ns)
                stored_hash = str(item.get("managed_sha256", ""))
                stored_signature = (item.get("source_size"), item.get("source_mtime_ns"))
                if stored_signature == signature and re.fullmatch(r"[0-9a-f]{64}", stored_hash):
                    expected = stored_hash
                    cache[cache_key] = (signature[0], signature[1], expected)
                elif cached is None or cached[:2] != signature:
                    expected = file_sha256(source)
                    cache[cache_key] = (signature[0], signature[1], expected)
                else:
                    expected = cached[2]
                if stored_hash != expected or stored_signature != signature:
                    item["managed_sha256"] = expected
                    item["source_size"] = signature[0]
                    item["source_mtime_ns"] = signature[1]
                    state_dirty = True
                target_key = "target:" + str(target).lower()
                if target.is_file():
                    target_stat = target.stat()
                    target_cached = cache.get(target_key)
                    target_signature = (target_stat.st_size, target_stat.st_mtime_ns)
                    if target_cached is not None and target_cached[:2] == target_signature and target_cached[2] == expected:
                        current = expected
                    else:
                        current = file_sha256(target)
                        cache[target_key] = (target_signature[0], target_signature[1], current)
                else:
                    current = ""
                if current != expected:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    temp_target = target.with_name(target.name + ".mtahub.guard.tmp")
                    try:
                        shutil.copy2(source, temp_target)
                        os.replace(temp_target, target)
                    finally:
                        temp_target.unlink(missing_ok=True)
                    restored_stat = target.stat()
                    cache[target_key] = (restored_stat.st_size, restored_stat.st_mtime_ns, expected)
                    repaired += 1
            except (OSError, PermissionError) as exc:
                errors.append(f"{target.name}: {exc}")

    if state_dirty:
        save_state(state_document)
    if repaired:
        record_history("Хеш-захист відновив файли", details=f"Відновлено: {repaired}")
    return {"checked": checked, "repaired": repaired, "errors": errors, "paused": False}


def verify_installed_files(
    game_root: str | Path,
    mods: Iterable[Mod] | None = None,
    progress=None,
    cancel_event=None,
) -> dict:
    """Verify every file installed by the manager and repair mismatches.

    Unlike the continuous guard, this pre-launch pass covers every installed
    category. Stored source hashes make later checks fast while target files are
    always hashed, so same-size server replacements are detected.
    """
    ok, message = validate_game_root(game_root)
    if not ok:
        raise ModError(message)
    ensure_game_stopped()
    root = resolve_game_root(game_root)
    state_document = load_state()
    installed = state_document.get("installed", {})
    catalog = {mod.id: mod for mod in (mods or load_catalog()[1])}
    jobs: list[tuple[str, dict, Path, Path]] = []
    missing_sources: list[str] = []

    for mod_id, record in installed.items():
        mod = catalog.get(mod_id)
        title = mod.title if mod else record.get("title", mod_id)
        destination = Path(record.get("destination", "")).resolve()
        if not _is_inside(destination, root):
            raise ModError(f"Небезпечний шлях установленого мода: {title}")
        source_root = payload_dir(mod_id)
        for item in record.get("files", []):
            relative = Path(item.get("relative", ""))
            source = (source_root / relative).resolve()
            target = (destination / relative).resolve()
            if not _is_inside(target, destination):
                raise ModError(f"Небезпечний шлях файла: {relative}")
            if not source.is_file():
                missing_sources.append(f"{title}: {relative.as_posix()}")
                continue
            jobs.append((title, item, source, target))

    if missing_sources:
        preview = "\n".join(f"• {item}" for item in missing_sources[:12])
        raise ModError(f"Відсутні еталонні файли модів:\n{preview}")
    if not jobs:
        return {"checked": 0, "repaired": 0, "bytes": 0}

    total_bytes = 0
    for _title, item, source, target in jobs:
        source_stat = source.stat()
        stored_signature = (item.get("source_size"), item.get("source_mtime_ns"))
        stored_hash = str(item.get("managed_sha256", ""))
        if stored_signature != (source_stat.st_size, source_stat.st_mtime_ns) or not re.fullmatch(r"[0-9a-f]{64}", stored_hash):
            total_bytes += source_stat.st_size
        if target.is_file():
            total_bytes += target.stat().st_size

    bytes_done = 0
    checked = repaired = 0

    def consume(amount: int, index: int, filename: str):
        nonlocal bytes_done
        if cancel_event is not None and cancel_event.is_set():
            raise OperationCancelled("Передстартову перевірку скасовано")
        bytes_done += amount
        _notify_progress(progress, index, len(jobs), filename, bytes_done, max(total_bytes, 1))

    for index, (title, item, source, target) in enumerate(jobs, 1):
        if cancel_event is not None and cancel_event.is_set():
            raise OperationCancelled("Передстартову перевірку скасовано")
        relative = item.get("relative", source.name)
        shown = f"{title}  •  {relative}"
        source_stat = source.stat()
        signature = (source_stat.st_size, source_stat.st_mtime_ns)
        stored_signature = (item.get("source_size"), item.get("source_mtime_ns"))
        expected = str(item.get("managed_sha256", ""))
        if stored_signature != signature or not re.fullmatch(r"[0-9a-f]{64}", expected):
            expected = _file_sha256_with_progress(
                source, lambda amount, i=index, name=shown: consume(amount, i, name)
            )
            item["managed_sha256"] = expected
            item["source_size"] = signature[0]
            item["source_mtime_ns"] = signature[1]

        if target.is_file():
            current = _file_sha256_with_progress(
                target, lambda amount, i=index, name=shown: consume(amount, i, name)
            )
        else:
            current = ""
        checked += 1
        if current != expected:
            target.parent.mkdir(parents=True, exist_ok=True)
            temp_target = target.with_name(target.name + ".mtahub.prelaunch.tmp")
            total_bytes += source_stat.st_size
            try:
                with source.open("rb") as reader, temp_target.open("wb") as writer:
                    for chunk in iter(lambda: reader.read(1024 * 1024), b""):
                        writer.write(chunk)
                        consume(len(chunk), index, shown)
                shutil.copystat(source, temp_target)
                os.replace(temp_target, target)
            finally:
                temp_target.unlink(missing_ok=True)
            repaired += 1
        _notify_progress(progress, index, len(jobs), shown, bytes_done, max(total_bytes, 1))

    save_state(state_document)
    record_history(
        "Перевірка перед автопідключенням",
        details=f"Перевірено: {checked}; відновлено: {repaired}",
    )
    return {"checked": checked, "repaired": repaired, "bytes": bytes_done}


def resolve_game_root(raw: str | Path) -> Path:
    root = Path(raw).expanduser().resolve()
    # Користувач може вибрати як UKRAINEGTA, так і вкладену папку game.
    if root.name.lower() == "game":
        root = root.parent
    return root


def validate_game_root(raw: str | Path) -> tuple[bool, str]:
    if not raw:
        return False, "Папку гри не вибрано"
    root = resolve_game_root(raw)
    if not root.exists():
        return False, "Вибрана папка не існує"
    game = root / "game"
    if not game.is_dir():
        return False, "Усередині має бути папка game"
    if not ((game / "bin").exists() or (game / "mods").exists()):
        return False, "Папка не схожа на встановлену UKRAINEGTA"
    return True, str(root)


def _is_inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def running_game_processes() -> list[str]:
    """Return game processes that make file modification unsafe.

    The official UKRAINE GTA launcher is intentionally not blocked: it must stay
    open while MTA starts. Only the actual game/client processes are checked.
    """
    if os.name != "nt":
        return []
    blocked = {
        "gta_sa.exe", "proxy_sa.exe", "multi theft auto.exe", "mta.exe",
        "mtasa.exe", "mta client.exe",
    }
    found: set[str] = set()
    try:
        import ctypes
        from ctypes import wintypes

        class ProcessEntry32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260),
            ]

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry32W)]
        kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry32W)]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            return []
        entry = ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(entry)
        try:
            has_item = bool(kernel32.Process32FirstW(snapshot, ctypes.byref(entry)))
            while has_item:
                name = str(entry.szExeFile).casefold()
                if name in blocked:
                    found.add(name)
                has_item = bool(kernel32.Process32NextW(snapshot, ctypes.byref(entry)))
        finally:
            kernel32.CloseHandle(snapshot)
    except (AttributeError, OSError, ValueError):
        return []
    return sorted(found)


def ensure_game_stopped() -> None:
    running = running_game_processes()
    if running:
        raise ModError(
            "Закрийте UKRAINE GTA/MTA перед зміною файлів. Запущено: "
            + ", ".join(running)
        )


def payload_size(mod_id: str) -> int:
    total = 0
    for path in payload_files(mod_id):
        try:
            total += path.stat().st_size
        except OSError:
            pass
    if total:
        return total
    return sum(
        item["size"] for item in _online_file_index(mod_id)
        if item["path"].startswith("payload/")
    )


def _notify_progress(progress, current, total, filename, bytes_done, total_bytes) -> None:
    if not progress:
        return
    try:
        progress(current, total, filename, bytes_done, total_bytes)
    except TypeError:
        progress(current, total, filename)


def _copy_with_progress(
    source: Path,
    target: Path,
    progress,
    current: int,
    total: int,
    relative: str,
    bytes_before: int,
    total_bytes: int,
    cancel_event=None,
) -> int:
    copied = 0
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with source.open("rb") as reader, target.open("wb") as writer:
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise OperationCancelled("Операцію скасовано — усі незавершені зміни відновлено")
                chunk = reader.read(1024 * 1024)
                if not chunk:
                    break
                writer.write(chunk)
                copied += len(chunk)
                _notify_progress(progress, current, total, relative, bytes_before + copied, total_bytes)
        shutil.copystat(source, target)
        return copied
    except Exception:
        target.unlink(missing_ok=True)
        raise


def preflight_check(game_root: str | Path, mods: Iterable[Mod] | None = None) -> list[dict]:
    selected = list(mods or [])
    issues: list[dict] = []
    ok, message = validate_game_root(game_root)
    if not ok:
        return [{"severity": "error", "title": "Папка гри", "detail": message}]
    root = resolve_game_root(game_root)
    running = running_game_processes()
    if running:
        issues.append({
            "severity": "error", "title": "Гра вже запущена",
            "detail": "Закрийте " + ", ".join(running) + " перед зміною модів.",
        })
    missing = [mod.title for mod in selected if not mod_payload_available(mod.id)]
    if missing:
        issues.append({
            "severity": "error", "title": "Відсутні файли модів",
            "detail": ", ".join(missing[:8]),
        })
    targets: dict[str, str] = {}
    conflicts: list[str] = []
    for mod in selected:
        source = payload_dir(mod.id)
        destination = (root / mod.destination).resolve()
        local_files = payload_files(mod.id)
        relatives = [item.relative_to(source) for item in local_files]
        if not relatives:
            relatives = [
                Path(item["path"]).relative_to("payload")
                for item in _online_file_index(mod.id)
                if item["path"].startswith("payload/")
            ]
        for relative in relatives:
            target = str((destination / relative).resolve()).casefold()
            previous = targets.get(target)
            if previous and previous != mod.title:
                conflicts.append(f"{previous} ↔ {mod.title}: {relative.name}")
            targets[target] = mod.title
    if conflicts:
        issues.append({
            "severity": "error", "title": "Конфлікти модів",
            "detail": "; ".join(conflicts[:8]),
        })
    required = sum(payload_size(mod.id) for mod in selected) * 2 + 64 * 1024 * 1024
    try:
        free = shutil.disk_usage(root).free
        if free < required:
            issues.append({
                "severity": "error", "title": "Недостатньо місця",
                "detail": f"Потрібно приблизно {required / 1024**2:.0f} МБ, доступно {free / 1024**2:.0f} МБ.",
            })
        elif free < max(required * 2, 1024**3):
            issues.append({
                "severity": "warning", "title": "Мало вільного місця",
                "detail": f"Доступно лише {free / 1024**3:.1f} ГБ.",
            })
    except OSError:
        issues.append({"severity": "warning", "title": "Диск", "detail": "Не вдалося перевірити вільне місце."})
    if not issues:
        issues.append({"severity": "ok", "title": "Перевірку пройдено", "detail": "Папка, файли, конфлікти й місце на диску — гаразд."})
    return issues


def installed_file_conflicts(mod: Mod, game_root: str | Path) -> list[dict]:
    """Describe installed mods that own files targeted by ``mod``."""
    ok, message = validate_game_root(game_root)
    if not ok:
        raise ModError(message)
    root = resolve_game_root(game_root)
    destination = (root / Path(mod.destination)).resolve()
    if not _is_inside(destination, root):
        raise ModError("Небезпечний шлях призначення у каталозі")

    source = payload_dir(mod.id)
    relatives = [path.relative_to(source) for path in payload_files(mod.id)]
    if not relatives:
        relatives = [
            Path(item["path"]).relative_to("payload")
            for item in _online_file_index(mod.id)
            if item["path"].startswith("payload/")
        ]
    incoming = {str((destination / relative).resolve()).casefold() for relative in relatives}
    conflicts: list[dict] = []
    for other_id, record in load_state().get("installed", {}).items():
        if other_id == mod.id:
            continue
        other_destination = Path(record.get("destination", "")).resolve()
        shared: list[str] = []
        for item in record.get("files", []):
            relative = Path(item.get("relative", ""))
            target = str((other_destination / relative).resolve()).casefold()
            if target in incoming:
                shared.append(relative.as_posix())
        if shared:
            conflicts.append({
                "id": other_id,
                "title": str(record.get("title") or other_id),
                "files": shared,
            })
    return conflicts


def _skin_payload_pair(mod_id: str) -> tuple[Path, Path]:
    files = payload_files(mod_id)
    dff = [path for path in files if path.suffix.casefold() == ".dff"]
    txd = [path for path in files if path.suffix.casefold() == ".txd"]
    if len(dff) != 1 or len(txd) != 1:
        raise ModError("Мод скіна повинен містити рівно один файл DFF і один файл TXD")
    return dff[0], txd[0]


def _replace_img_entry(archive: Path, entry_name: str, source: Path) -> None:
    entries = read_img_directory(archive)
    entry = entries.get(entry_name.casefold())
    if entry is None:
        raise ModError(f"У шаблоні {archive.name} відсутній запис {entry_name}")
    size = source.stat().st_size
    sectors = max(1, (size + 2047) // 2048)
    if sectors > 0xFFFF:
        raise ModError(f"Файл {source.name} завеликий для IMG v2")
    capacity = max(entry.streaming_sectors, entry.archive_sectors, 1)
    try:
        with archive.open("r+b") as handle, source.open("rb") as reader:
            if sectors <= capacity and entry.offset_sector > 0:
                offset_sector = entry.offset_sector
                handle.seek(offset_sector * 2048)
                remaining = size
                while remaining:
                    chunk = reader.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ModError(f"Не вдалося повністю прочитати {source.name}")
                    handle.write(chunk)
                    remaining -= len(chunk)
                handle.write(b"\0" * (capacity * 2048 - size))
            else:
                handle.seek(0, os.SEEK_END)
                padding = (-handle.tell()) % 2048
                if padding:
                    handle.write(b"\0" * padding)
                offset_sector = handle.tell() // 2048
                shutil.copyfileobj(reader, handle, 1024 * 1024)
                handle.write(b"\0" * (sectors * 2048 - size))
            archived = sectors if entry.archive_sectors else 0
            handle.seek(entry.directory_offset)
            handle.write(struct.pack("<IHH", offset_sector, sectors, archived))
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise ModError(f"Не вдалося оновити {entry_name} в {archive.name}: {exc}") from exc


def _ensure_encrypted_img_backup(game_archive: Path, backup: Path) -> tuple[Path, dict[int, bytes]]:
    if not game_archive.is_file():
        raise ModError(f"Не знайдено ігровий архів {game_archive}")
    keys = _fastman_keys()
    if not backup.is_file():
        backup.parent.mkdir(parents=True, exist_ok=True)
        temporary = backup.with_name(backup.name + ".tmp")
        try:
            shutil.copy2(game_archive, temporary)
            parse_fastman_archive(temporary, keys)
            os.replace(temporary, backup)
        except (OSError, FastmanImgError) as exc:
            temporary.unlink(missing_ok=True)
            raise ModError(f"Не вдалося створити резервну копію {game_archive.name}: {exc}") from exc
    try:
        parse_fastman_archive(backup, keys)
    except FastmanImgError as exc:
        raise ModError(f"Резервна копія {backup.name} не є коректним зашифрованим IMG: {exc}") from exc
    return backup, keys


def _rebuild_encrypted_img(
    source_backup: Path,
    target: Path,
    records: list[dict],
    payload_pair,
    label: str,
    progress=None,
    cancel_event=None,
    allow_additions: bool = False,
) -> Path:
    keys = _fastman_keys()
    replacements: dict[str, Path] = {}
    for record in records:
        first, second = payload_pair(str(record["mod_id"]))
        replacements[str(record["target_dff"])] = first
        replacements[str(record["target_txd"])] = second
    temporary = target.with_name(target.name + ".mtahub.encrypted.tmp")
    temporary.unlink(missing_ok=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        repack_fastman_archive(
            source_backup, temporary, replacements, keys,
            progress=progress, cancel_event=cancel_event, allow_additions=allow_additions,
        )
        if cancel_event is not None and cancel_event.is_set():
            raise OperationCancelled(f"{label} скасовано")
        os.replace(temporary, target)
        return target
    except FastmanOperationCancelled as exc:
        raise OperationCancelled(str(exc)) from exc
    except FastmanImgError as exc:
        raise ModError(f"{label}: {exc}") from exc
    except OSError as exc:
        raise ModError(f"{label}: не вдалося записати {target.name}: {exc}") from exc
    finally:
        temporary.unlink(missing_ok=True)


def _skin_records_for_archive(state: dict, archive_name: str, excluded_id: str = "") -> list[dict]:
    result = []
    for mod_id, record in state.get("installed", {}).items():
        if mod_id == excluded_id or record.get("kind") != "skin":
            continue
        if str(record.get("archive", "")).casefold() != archive_name.casefold():
            continue
        item = dict(record)
        item["mod_id"] = mod_id
        result.append(item)
    return result


def _detect_skin_archive(game_root: str | Path, target: SkinTarget) -> str:
    root = resolve_game_root(game_root)
    keys = _fastman_keys()
    failures: list[str] = []
    for archive_name in ("PEDS.img", "gta3.img"):
        backup = BACKUP_DIR / "skin_archives" / archive_name
        candidate = backup if backup.is_file() else root / "game/bin/models" / archive_name
        if not candidate.is_file():
            continue
        try:
            archive = parse_fastman_archive(candidate, keys)
        except FastmanImgError as exc:
            failures.append(f"{archive_name}: {exc}")
            continue
        if fastman_archive_contains(archive, target.dff_filename, target.txd_filename):
            return archive_name
    detail = "\n".join(failures)
    raise ModError(
        f"Не знайдено {target.dff_filename} та {target.txd_filename} у PEDS.img або gta3.img"
        + (f"\n{detail}" if detail else "")
    )


def _rebuild_skin_archive(
    archive_name: str,
    game_root: str | Path,
    records: list[dict],
    progress=None,
    cancel_event=None,
) -> Path:
    root = resolve_game_root(game_root)
    target = root / "game/bin/models" / archive_name
    backup = BACKUP_DIR / "skin_archives" / archive_name
    if not backup.is_file():
        raise ModError(f"Не знайдено зашифровану резервну копію {archive_name}")
    return _rebuild_encrypted_img(
        backup, target, records, _skin_payload_pair, "Заміна скіна", progress, cancel_event
    )


def install_skin_mod(
    mod: Mod,
    game_root: str | Path,
    target: SkinTarget,
    archive_name: str,
    template_path: str | Path | None,
    progress=None,
    cancel_event=None,
) -> dict:
    ensure_game_stopped()
    root = resolve_game_root(game_root)
    if not str(archive_name).strip():
        archive_name = _detect_skin_archive(root, target)
    if archive_name.casefold() not in {"gta3.img", "peds.img"}:
        raise ModError("Для скінів підтримуються лише gta3.img та PEDS.img")
    archive_name = "PEDS.img" if archive_name.casefold() == "peds.img" else "gta3.img"
    if remote_payload_available(mod.id):
        ensure_online_payload(mod.id, progress, cancel_event)
    _skin_payload_pair(mod.id)

    state = load_state()
    state.get("installed", {}).pop(mod.id, None)
    for other_id, record in state.get("installed", {}).items():
        if record.get("kind") != "skin":
            continue
        if str(record.get("archive", "")).casefold() == archive_name.casefold() and int(record.get("target_id", -1)) == target.id:
            raise ModError(
                f"Скін «{record.get('title', other_id)}» уже замінює {target.name}"
            )

    game_archive = root / "game/bin/models" / archive_name
    original_backup = BACKUP_DIR / "skin_archives" / archive_name
    original_backup, keys = _ensure_encrypted_img_backup(game_archive, original_backup)
    try:
        encrypted = parse_fastman_archive(original_backup, keys)
    except FastmanImgError as exc:
        raise ModError(str(exc)) from exc
    if not fastman_archive_contains(encrypted, target.dff_filename, target.txd_filename):
        raise ModError(
            f"У зашифрованому {archive_name} немає {target.dff_filename} та {target.txd_filename}"
        )

    record = {
        "kind": "skin",
        "title": mod.title,
        "installed_at": datetime.now(timezone.utc).isoformat(),
        "destination": str(game_archive.parent),
        "archive": archive_name,
        "target_id": target.id,
        "target_name": target.name,
        "target_dff": target.dff_filename,
        "target_txd": target.txd_filename,
        "files": [],
    }
    records = _skin_records_for_archive(state, archive_name)
    pending = dict(record)
    pending["mod_id"] = mod.id
    records.append(pending)
    _rebuild_skin_archive(archive_name, root, records, progress, cancel_event)
    state["installed"][mod.id] = record
    save_state(state)
    record_history(
        "Заміну скіна встановлено", mod,
        target.name,
    )
    return record


def _uninstall_skin_mod(
    mod: Mod,
    game_root: str | Path,
    record: dict,
    record_action: bool = True,
) -> None:
    root = resolve_game_root(game_root)
    archive_name = str(record.get("archive", ""))
    state = load_state()
    remaining = _skin_records_for_archive(state, archive_name, excluded_id=mod.id)
    target = root / "game/bin/models" / archive_name
    if remaining:
        _rebuild_skin_archive(archive_name, root, remaining)
    else:
        original = BACKUP_DIR / "skin_archives" / archive_name
        if original.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + ".mtahub.restore.tmp")
            try:
                shutil.copy2(original, temporary)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        else:
            raise ModError(f"Не знайдено резервну копію оригінального {archive_name}")
    state["installed"].pop(mod.id, None)
    save_state(state)
    if record_action:
        record_history("Оригінальний скін відновлено", mod)


def _weapon_payload_pair(mod_id: str) -> tuple[Path, Path]:
    files = payload_files(mod_id)
    dff = [path for path in files if path.suffix.casefold() == ".dff"]
    txd = [path for path in files if path.suffix.casefold() == ".txd"]
    if len(dff) != 1 or len(txd) != 1:
        raise ModError("Мод зброї повинен містити рівно один файл DFF і один файл TXD")
    return dff[0], txd[0]


def _weapon_records(state: dict, excluded_id: str = "") -> list[dict]:
    result = []
    for mod_id, record in state.get("installed", {}).items():
        if mod_id == excluded_id or record.get("kind") != "weapon":
            continue
        item = dict(record)
        item["mod_id"] = mod_id
        result.append(item)
    return result


def _rebuild_weapon_archive(
    game_root: str | Path,
    records: list[dict],
    progress=None,
    cancel_event=None,
) -> Path:
    root = resolve_game_root(game_root)
    target = root / "game/bin/models/gta3.img"
    backup = BACKUP_DIR / "weapon_archives/gta3.img"
    if not backup.is_file():
        raise ModError("Не знайдено зашифровану резервну копію gta3.img")
    return _rebuild_encrypted_img(
        backup, target, records, _weapon_payload_pair, "Заміна зброї", progress, cancel_event
    )


def install_weapon_mod(
    mod: Mod,
    game_root: str | Path,
    target: WeaponTarget,
    template_path: str | Path | None = None,
    progress=None,
    cancel_event=None,
) -> dict:
    ensure_game_stopped()
    root = resolve_game_root(game_root)
    if remote_payload_available(mod.id):
        ensure_online_payload(mod.id, progress, cancel_event)
    _weapon_payload_pair(mod.id)

    state = load_state()
    state.get("installed", {}).pop(mod.id, None)
    for other_id, record in state.get("installed", {}).items():
        if record.get("kind") == "skin" and str(record.get("archive", "")).casefold() == "gta3.img":
            raise ModError("Спочатку видаліть стару заміну скіна, встановлену в gta3.img")
        if record.get("kind") != "weapon":
            continue
        occupied = {
            str(record.get("target_dff", "")).casefold(),
            str(record.get("target_txd", "")).casefold(),
        }
        requested = {target.dff_filename.casefold(), target.txd_filename.casefold()}
        if occupied & requested:
            raise ModError(f"«{record.get('title', other_id)}» уже замінює {target.name}")

    game_archive = root / "game/bin/models/gta3.img"
    original_backup = BACKUP_DIR / "weapon_archives/gta3.img"
    original_backup, keys = _ensure_encrypted_img_backup(game_archive, original_backup)
    try:
        encrypted = parse_fastman_archive(original_backup, keys)
    except FastmanImgError as exc:
        raise ModError(str(exc)) from exc
    if not fastman_archive_contains(encrypted, target.dff_filename, target.txd_filename):
        raise ModError(
            f"У зашифрованому gta3.img немає {target.dff_filename} та {target.txd_filename}"
        )

    record = {
        "kind": "weapon",
        "title": mod.title,
        "installed_at": datetime.now(timezone.utc).isoformat(),
        "destination": str(game_archive.parent),
        "archive": "gta3.img",
        "target_id": target.id,
        "target_name": target.name,
        "target_dff": target.dff_filename,
        "target_txd": target.txd_filename,
        "files": [],
    }
    records = _weapon_records(state)
    pending = dict(record)
    pending["mod_id"] = mod.id
    records.append(pending)
    _rebuild_weapon_archive(root, records, progress, cancel_event)
    state["installed"][mod.id] = record
    save_state(state)
    record_history("Заміну зброї встановлено", mod, target.name)
    return record


def _uninstall_weapon_mod(
    mod: Mod,
    game_root: str | Path,
    record_action: bool = True,
) -> None:
    root = resolve_game_root(game_root)
    state = load_state()
    remaining = _weapon_records(state, excluded_id=mod.id)
    target = root / "game/bin/models/gta3.img"
    if remaining:
        _rebuild_weapon_archive(root, remaining)
    else:
        original = BACKUP_DIR / "weapon_archives/gta3.img"
        if not original.is_file():
            raise ModError("Не знайдено резервну копію оригінального gta3.img")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".mtahub.restore.tmp")
        try:
            shutil.copy2(original, temporary)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    state["installed"].pop(mod.id, None)
    save_state(state)
    if record_action:
        record_history("Оригінальну зброю відновлено", mod)


def _accessory_records(state: dict, excluded_id: str = "") -> list[dict]:
    result = []
    for mod_id, record in state.get("installed", {}).items():
        if mod_id == excluded_id or record.get("kind") != "accessory":
            continue
        item = dict(record)
        item["mod_id"] = mod_id
        result.append(item)
    return result


def _rebuild_accessory_archive(
    game_root: str | Path,
    records: list[dict],
    progress=None,
    cancel_event=None,
) -> Path:
    root = resolve_game_root(game_root)
    target = root / "game/bin/data/maps/ACS/acs.img"
    backup = BACKUP_DIR / "accessory_archives/acs.img"
    if not backup.is_file():
        raise ModError("Не знайдено зашифровану резервну копію acs.img")
    return _rebuild_encrypted_img(
        backup, target, records, _skin_payload_pair, "Заміна аксесуара", progress, cancel_event,
        allow_additions=True,
    )


def install_accessory_mod(
    mod: Mod,
    game_root: str | Path,
    target: AccessoryTarget,
    template_path: str | Path | None,
    progress=None,
    cancel_event=None,
) -> dict:
    ensure_game_stopped()
    root = resolve_game_root(game_root)
    if remote_payload_available(mod.id):
        ensure_online_payload(mod.id, progress, cancel_event)
    _skin_payload_pair(mod.id)

    state = load_state()
    state.get("installed", {}).pop(mod.id, None)
    requested_entries = {target.dff_filename.casefold(), target.txd_filename.casefold()}
    for other_id, record in state.get("installed", {}).items():
        if record.get("kind") != "accessory":
            continue
        occupied = {
            str(record.get("target_dff", "")).casefold(),
            str(record.get("target_txd", "")).casefold(),
        }
        if requested_entries & occupied:
            raise ModError(
                f"Аксесуар «{record.get('title', other_id)}» використовує спільну модель або текстуру"
            )

    game_archive = root / "game/bin/data/maps/ACS/acs.img"
    original_backup = BACKUP_DIR / "accessory_archives/acs.img"
    original_backup, keys = _ensure_encrypted_img_backup(game_archive, original_backup)
    try:
        encrypted = parse_fastman_archive(original_backup, keys)
    except FastmanImgError as exc:
        raise ModError(str(exc)) from exc
    if not fastman_archive_contains(encrypted, target.dff_filename):
        raise ModError(
            f"У зашифрованому acs.img немає моделі {target.dff_filename}"
        )

    record = {
        "kind": "accessory",
        "title": mod.title,
        "installed_at": datetime.now(timezone.utc).isoformat(),
        "destination": str(game_archive.parent),
        "archive": "acs.img",
        "target_id": target.id,
        "target_name": target.name,
        "target_slot": target.slot,
        "target_key": target.key,
        "target_dff": target.dff_filename,
        "target_txd": target.txd_filename,
        "files": [],
    }
    records = _accessory_records(state)
    pending = dict(record)
    pending["mod_id"] = mod.id
    records.append(pending)
    _rebuild_accessory_archive(root, records, progress, cancel_event)
    state["installed"][mod.id] = record
    save_state(state)
    record_history(
        "Заміну аксесуара встановлено", mod,
        target.name,
    )
    return record


def _uninstall_accessory_mod(
    mod: Mod,
    game_root: str | Path,
    record_action: bool = True,
) -> None:
    root = resolve_game_root(game_root)
    state = load_state()
    remaining = _accessory_records(state, excluded_id=mod.id)
    target = root / "game/bin/data/maps/ACS/acs.img"
    if remaining:
        _rebuild_accessory_archive(root, remaining)
    else:
        original = BACKUP_DIR / "accessory_archives/acs.img"
        if not original.is_file():
            raise ModError("Не знайдено резервну копію оригінального acs.img")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".mtahub.restore.tmp")
        try:
            shutil.copy2(original, temporary)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    state["installed"].pop(mod.id, None)
    save_state(state)
    if record_action:
        record_history("Оригінальний аксесуар відновлено", mod)


def install_mod(
    mod: Mod,
    game_root: str | Path,
    progress: Callable[[int, int, str], None] | None = None,
    cancel_event=None,
    skin_target: SkinTarget | None = None,
    skin_archive: str = "",
    skin_template: str | Path | None = None,
    accessory_target: AccessoryTarget | None = None,
    accessory_template: str | Path | None = None,
    weapon_target: WeaponTarget | None = None,
    weapon_template: str | Path | None = None,
) -> dict:
    ok, message = validate_game_root(game_root)
    if not ok:
        raise ModError(message)
    ensure_game_stopped()

    if mod.mod_type == "skin" or mod.category == "Скіни":
        if skin_target is None:
            raise ModError("Оберіть, який стандартний скін потрібно замінити")
        return install_skin_mod(
            mod, game_root, skin_target, skin_archive, skin_template,
            progress, cancel_event,
        )

    if mod.mod_type == "accessory" or mod.category == "Аксесуари":
        if accessory_target is None:
            raise ModError("Оберіть, який стандартний аксесуар потрібно замінити")
        return install_accessory_mod(
            mod, game_root, accessory_target, accessory_template,
            progress, cancel_event,
        )

    if mod.mod_type == "weapon" or mod.category == "Заміна зброї":
        if weapon_target is None:
            raise ModError("Оберіть, яку стандартну зброю потрібно замінити")
        return install_weapon_mod(
            mod, game_root, weapon_target, weapon_template,
            progress, cancel_event,
        )

    root = resolve_game_root(game_root)
    if remote_payload_available(mod.id):
        ensure_online_payload(mod.id, progress, cancel_event)
    source = payload_dir(mod.id)
    files = payload_files(mod.id)
    if not files:
        raise ModError(f"Для «{mod.title}» ще не додано файлів")

    destination = (root / Path(mod.destination)).resolve()
    if not _is_inside(destination, root):
        raise ModError("Небезпечний шлях призначення в каталозі")

    state = load_state()
    previous = state["installed"].get(mod.id)
    if previous:
        # Повторне застосування не створює нескінченний ланцюжок резервних копій.
        uninstall_mod(mod, game_root, tolerate_missing=True, record_action=False)
        state = load_state()

    incoming_targets = {
        str((destination / src.relative_to(source)).resolve()).lower()
        for src in files
    }
    collisions: list[str] = []
    for other_id, other in state.get("installed", {}).items():
        other_destination = Path(other.get("destination", ""))
        for item in other.get("files", []):
            other_target = str((other_destination / item["relative"]).resolve()).lower()
            if other_target in incoming_targets:
                collisions.append(f"{other.get('title', other_id)}: {Path(item['relative']).name}")
    if collisions:
        preview = "\n".join(f"• {item}" for item in collisions[:6])
        raise ModError(
            "Виявлено конфлікт із уже встановленим модом:\n"
            f"{preview}\n\nСпочатку видаліть конфліктний мод."
        )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_root = BACKUP_DIR / mod.id / stamp
    record = {
        "title": mod.title,
        "installed_at": datetime.now(timezone.utc).isoformat(),
        "destination": str(destination),
        "backup": str(backup_root),
        "files": [],
    }

    copied: list[Path] = []
    total_bytes = sum(path.stat().st_size for path in files)
    bytes_done = 0
    try:
        destination.mkdir(parents=True, exist_ok=True)
        for index, src in enumerate(files, 1):
            if cancel_event is not None and cancel_event.is_set():
                raise OperationCancelled("Операцію скасовано — усі незавершені зміни відновлено")
            relative = src.relative_to(source)
            target = (destination / relative).resolve()
            if not _is_inside(target, destination):
                raise ModError(f"Небезпечний шлях файлу: {relative}")
            existed = target.exists()
            if existed:
                backup_target = backup_root / relative
                backup_target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup_target)
            target.parent.mkdir(parents=True, exist_ok=True)
            temp_target = target.with_name(target.name + ".mtahub.tmp")
            copied_size = _copy_with_progress(
                src, temp_target, progress, index, len(files), relative.as_posix(),
                bytes_done, total_bytes, cancel_event,
            )
            os.replace(temp_target, target)
            bytes_done += copied_size
            copied.append(target)
            file_record = {"relative": relative.as_posix(), "existed": existed}
            if mod.hash_guard:
                source_stat = src.stat()
                file_record.update({
                    "managed_sha256": file_sha256(src),
                    "source_size": source_stat.st_size,
                    "source_mtime_ns": source_stat.st_mtime_ns,
                })
            record["files"].append(file_record)
            _notify_progress(progress, index, len(files), relative.as_posix(), bytes_done, total_bytes)
    except Exception:
        for target in reversed(copied):
            relative = target.relative_to(destination)
            backup_target = backup_root / relative
            if backup_target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup_target, target)
            else:
                target.unlink(missing_ok=True)
        raise

    state["installed"][mod.id] = record
    save_state(state)
    record_history("Мод оновлено" if previous else "Мод встановлено", mod)
    return record


def uninstall_mod(
    mod: Mod,
    game_root: str | Path,
    tolerate_missing: bool = False,
    record_action: bool = True,
) -> None:
    ensure_game_stopped()
    state = load_state()
    record = state.get("installed", {}).get(mod.id)
    if not record:
        if tolerate_missing:
            return
        raise ModError("Мод не позначено як встановлений")

    if record.get("kind") == "skin":
        _uninstall_skin_mod(mod, game_root, record, record_action)
        return
    if record.get("kind") == "accessory":
        _uninstall_accessory_mod(mod, game_root, record_action)
        return
    if record.get("kind") == "weapon":
        _uninstall_weapon_mod(mod, game_root, record_action)
        return

    root = resolve_game_root(game_root)
    destination = Path(record["destination"]).resolve()
    if not _is_inside(destination, root):
        raise ModError("Збережений шлях призначення розташований поза папкою гри")
    backup_root = Path(record["backup"])

    for item in reversed(record.get("files", [])):
        relative = Path(item["relative"])
        target = (destination / relative).resolve()
        if not _is_inside(target, destination):
            continue
        backup_target = backup_root / relative
        if item.get("existed") and backup_target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup_target, target)
        elif not item.get("existed"):
            target.unlink(missing_ok=True)

    state["installed"].pop(mod.id, None)
    save_state(state)
    shutil.rmtree(backup_root, ignore_errors=True)
    if record_action:
        record_history("Оригінальні файли відновлено", mod)


def restore_clean_game(
    game_root: str | Path,
    mods: Iterable[Mod] | None = None,
    progress=None,
    cancel_event=None,
) -> int:
    ok, message = validate_game_root(game_root)
    if not ok:
        raise ModError(message)
    ensure_game_stopped()
    catalog_mods = {mod.id: mod for mod in (mods or load_catalog()[1])}
    installed = list(load_state().get("installed", {}).items())
    restored = 0
    for index, (mod_id, record) in enumerate(reversed(installed), 1):
        if cancel_event is not None and cancel_event.is_set():
            raise OperationCancelled("Очищення зупинено. Уже відновлені моди залишилися чистими.")
        mod = catalog_mods.get(mod_id) or Mod(
            mod_id, record.get("title", mod_id), "Інше", "", "game"
        )
        uninstall_mod(mod, game_root, record_action=False)
        restored += 1
        _notify_progress(progress, index, len(installed), mod.title, index, len(installed))
    record_history("Відновлено чисту гру", details=f"Видалено модів: {restored}")
    return restored


def verify_catalog_manifest_signature(manifest: dict, public_key_b64: str) -> dict:
    """Verify RSA-SHA256 manifests while retaining Ed25519 compatibility."""
    try:
        payload = manifest["payload"]
        signature = base64.b64decode(manifest["signature"], validate=True)
        public_bytes = base64.b64decode(public_key_b64.strip(), validate=True)
        canonical = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        algorithm = str(manifest.get("algorithm", "ed25519")).strip().lower()
        if algorithm == "rsa-sha256":
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import padding, rsa

            public_key = serialization.load_pem_public_key(public_bytes)
            if not isinstance(public_key, rsa.RSAPublicKey):
                raise ValueError("публічний ключ не є RSA-ключем")
            public_key.verify(signature, canonical, padding.PKCS1v15(), hashes.SHA256())
        elif algorithm == "ed25519":
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

            Ed25519PublicKey.from_public_bytes(public_bytes).verify(signature, canonical)
        else:
            raise ValueError(f"непідтримуваний алгоритм: {algorithm}")
        return payload
    except ImportError as exc:
        raise ModError("Для перевірки цифрового підпису потрібен компонент cryptography") from exc
    except Exception as exc:
        raise ModError(f"Каталог не пройшов перевірку цифрового підпису: {exc}") from exc


def _version_tuple(value: str) -> tuple[int, ...]:
    match = re.fullmatch(r"\s*(\d+)\.(\d+)(?:\.(\d+))?\s*", value or "")
    if not match:
        raise ModError(f"Некоректна версія програми: {value}")
    return tuple(int(part or 0) for part in match.groups())


def check_application_update(manifest_url: str, public_key_b64: str, current_version: str) -> dict | None:
    manifest_url = manifest_url.strip()
    if not is_allowed_remote_url(manifest_url):
        raise ModError("Оновлення програми повинні завантажуватися через HTTPS")
    try:
        with urllib.request.urlopen(manifest_url, timeout=10) as response:
            manifest = json.loads(response.read().decode("utf-8"))
        payload = verify_catalog_manifest_signature(manifest, public_key_b64)
    except ModError:
        raise
    except Exception as exc:
        raise ModError(f"Не вдалося перевірити оновлення програми: {exc}") from exc
    if not payload.get("available"):
        return None
    if _version_tuple(str(payload.get("version", ""))) <= _version_tuple(current_version):
        return None
    download_url = urllib.parse.urljoin(manifest_url, str(payload.get("download_url", "")))
    if is_trusted_origin_url(manifest_url):
        download_url = origin_url_for_official(download_url)
    expected = str(payload.get("sha256", "")).lower()
    if not is_allowed_remote_url(download_url) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ModError("Маніфест оновлення містить небезпечні або неповні дані")
    result = dict(payload)
    result["download_url"] = download_url
    return result


def download_application_update(payload: dict, progress=None, cancel_event=None) -> Path:
    version = str(payload["version"])
    target_dir = DATA_DIR / "updates"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"UG-MOD-HUB-{version}.exe"
    temporary = target.with_suffix(".download")
    digest = hashlib.sha256()
    total = max(0, int(payload.get("size", 0)))
    done = 0
    try:
        with urllib.request.urlopen(str(payload["download_url"]), timeout=60) as response, temporary.open("wb") as writer:
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise OperationCancelled("Завантаження оновлення скасовано")
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                writer.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                _notify_progress(progress, 1, 1, target.name, done, total)
        if digest.hexdigest() != str(payload["sha256"]).lower():
            raise ModError("SHA-256 завантаженого оновлення не збігається")
        os.replace(temporary, target)
        return target
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def launch_application_updater(update_path: str | Path) -> None:
    if os.name != "nt" or not getattr(sys, "frozen", False):
        raise ModError("Автооновлення доступне лише у зібраній Windows-версії")
    source = Path(update_path).resolve()
    current = Path(sys.executable).resolve()
    if not source.is_file() or source.suffix.lower() != ".exe":
        raise ModError("Файл оновлення не знайдено")
    script = DATA_DIR / "apply-update.cmd"
    script.parent.mkdir(parents=True, exist_ok=True)
    content = (
        "@echo off\r\n"
        "setlocal\r\n"
        "set \"PYINSTALLER_RESET_ENVIRONMENT=1\"\r\n"
        "timeout /t 2 /nobreak >nul\r\n"
        ":retry\r\n"
        f'move /Y "{source}" "{current}" >nul 2>&1\r\n'
        "if errorlevel 1 (timeout /t 1 /nobreak >nul & goto retry)\r\n"
        f'start "" "{current}"\r\n'
        'del "%~f0"\r\n'
    )
    script.write_text(content, encoding="mbcs")
    updater_environment = os.environ.copy()
    # A new frozen application must not reuse the parent's temporary _MEI
    # runtime, which is removed while the previous version is shutting down.
    updater_environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    subprocess.Popen(
        ["cmd.exe", "/c", str(script)],
        cwd=str(current.parent),
        creationflags=0x08000000,
        env=updater_environment,
    )


def _online_file_index(mod_id: str) -> list[dict]:
    """Return the signed remote file metadata without downloading file bodies."""
    catalog = _read_json(ONLINE_CATALOG_PATH, {"mods": []})
    for raw in catalog.get("mods", []):
        if str(raw.get("id", "")) != mod_id:
            continue
        result: list[dict] = []
        for item in raw.get("file_index", []):
            relative = Path(str(item.get("path", "")).replace("\\", "/"))
            expected = str(item.get("sha256", "")).lower()
            url = str(item.get("url", "")).strip()
            if (
                not relative.parts or relative.is_absolute() or ".." in relative.parts
                or relative.parts[0] not in {"payload", "cover.png", "cover.jpg", "cover.jpeg"}
                and relative.name not in {"cover.png", "cover.jpg", "cover.jpeg"}
                or not re.fullmatch(r"[0-9a-f]{64}", expected)
                or not is_allowed_remote_url(url)
            ):
                continue
            result.append({
                "path": relative.as_posix(),
                "size": max(0, int(item.get("size", 0))),
                "sha256": expected,
                "url": url,
            })
        return result
    return []


def remote_payload_available(mod_id: str) -> bool:
    return any(item["path"].startswith("payload/") for item in _online_file_index(mod_id))


def mod_payload_available(mod_id: str) -> bool:
    return bool(payload_files(mod_id)) or remote_payload_available(mod_id)


def _normalize_template_id(value) -> str:
    """Accept web template IDs as names, filenames, or differently-cased labels."""
    raw = str(value or "").strip().casefold().replace("\\", "/")
    raw = raw.replace("\ufeff", "").replace("\u200b", "")
    name = raw.rsplit("/", 1)[-1]
    if name.endswith(".img"):
        name = name[:-4]
    compact = re.sub(r"[^a-z0-9]", "", name)
    return {
        "pedsimg": "peds",
        "acsimg": "acs",
        "gta3img": "gta3",
    }.get(compact, compact)


def _online_template_index() -> dict[str, dict]:
    catalog = _read_json(ONLINE_CATALOG_PATH, {"templates": []})
    result = {}
    for item in catalog.get("templates", []):
        template_id = _normalize_template_id(item.get("id"))
        if template_id not in {"peds", "acs", "gta3"}:
            template_id = _normalize_template_id(item.get("filename"))
        expected = str(item.get("sha256", "")).casefold()
        url = str(item.get("url", "")).strip()
        if (
            template_id not in {"peds", "acs", "gta3"}
            or not re.fullmatch(r"[0-9a-f]{64}", expected)
            or not is_allowed_remote_url(url)
        ):
            continue
        result[template_id] = {
            "id": template_id,
            "filename": {"peds": "PEDS.img", "acs": "acs.img", "gta3": "gta3.img"}[template_id],
            "size": max(0, int(item.get("size", 0))),
            "sha256": expected,
            "url": url,
        }
    return result


def ensure_online_template(template_id: str, progress=None, cancel_event=None) -> Path:
    template_id = _normalize_template_id(template_id)
    job = _online_template_index().get(template_id)
    if not job:
        label = {"peds": "PEDS.img", "acs": "acs.img", "gta3": "gta3.img"}.get(template_id, "IMG")
        raise ModError(f"На веб-сайті ще не опубліковано відкритий шаблон {label}")
    target = {
        "peds": SKIN_TEMPLATE_DIR / "PEDS.img",
        "acs": ACCESSORY_TEMPLATE_DIR / "acs.img",
        "gta3": WEAPON_TEMPLATE_DIR / "gta3.img",
    }[template_id]
    try:
        if (
            target.is_file()
            and target.stat().st_size == job["size"]
            and file_sha256(target) == job["sha256"]
        ):
            with target.open("rb") as handle:
                if handle.read(4) == b"VER2":
                    return target
    except OSError:
        pass

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".download")
    temporary.unlink(missing_ok=True)
    digest = hashlib.sha256()
    bytes_done = 0
    try:
        with urllib.request.urlopen(job["url"], timeout=120) as response, temporary.open("wb") as writer:
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise OperationCancelled("Завантаження IMG-шаблону скасовано")
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                writer.write(chunk)
                digest.update(chunk)
                bytes_done += len(chunk)
                _notify_progress(
                    progress, 1, 1, job["filename"], bytes_done,
                    job["size"] or max(bytes_done, 1),
                )
        if temporary.stat().st_size != job["size"] or digest.hexdigest() != job["sha256"]:
            raise ModError(f"SHA-256 або розмір не збігається: {job['filename']}")
        with temporary.open("rb") as handle:
            if handle.read(4) != b"VER2":
                raise ModError(f"{job['filename']} не є відкритим IMG формату VER2")
        os.replace(temporary, target)
        record_history("IMG-шаблон завантажено", details=f"{job['filename']}; {job['sha256']}")
        return target
    finally:
        temporary.unlink(missing_ok=True)


def ensure_online_payload(mod_id: str, progress=None, cancel_event=None) -> dict:
    """Download one selected mod on demand and atomically replace its local cache."""
    jobs = _online_file_index(mod_id)
    payload_jobs = [item for item in jobs if item["path"].startswith("payload/")]
    if not payload_jobs:
        if payload_files(mod_id):
            return {"files": len(payload_files(mod_id)), "downloaded_files": 0, "cached": True}
        raise ModError("Для цього мода в онлайн-каталозі немає файлів")

    all_cached = True
    for job in jobs:
        target = DATA_DIR / "mods" / mod_id / Path(job["path"])
        try:
            if (
                not target.is_file()
                or target.stat().st_size != job["size"]
                or file_sha256(target) != job["sha256"]
            ):
                all_cached = False
                break
        except OSError:
            all_cached = False
            break
    if all_cached:
        return {"files": len(payload_jobs), "downloaded_files": 0, "cached": True}

    staging_root = DATA_DIR / ".mod-download" / mod_id
    backup_root = DATA_DIR / ".mod-download-backup" / mod_id
    target_root = DATA_DIR / "mods" / mod_id
    shutil.rmtree(staging_root, ignore_errors=True)
    shutil.rmtree(backup_root, ignore_errors=True)
    staging_root.mkdir(parents=True, exist_ok=True)
    total_bytes = sum(item["size"] for item in jobs)
    bytes_done = 0
    downloaded_files = 0
    try:
        for index, job in enumerate(jobs, 1):
            if cancel_event is not None and cancel_event.is_set():
                raise OperationCancelled("Завантаження мода скасовано")
            relative = Path(job["path"])
            target = staging_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            reused = False
            for candidate in (
                DATA_DIR / "mods" / mod_id / relative,
                APP_DIR / "mods" / mod_id / relative,
            ):
                try:
                    if (
                        candidate.is_file()
                        and candidate.stat().st_size == job["size"]
                        and file_sha256(candidate) == job["sha256"]
                    ):
                        try:
                            os.link(candidate, target)
                        except OSError:
                            shutil.copy2(candidate, target)
                        bytes_done += job["size"]
                        _notify_progress(progress, index, len(jobs), relative.as_posix(), bytes_done, total_bytes or 1)
                        reused = True
                        break
                except OSError:
                    continue
            if reused:
                continue
            digest = hashlib.sha256()
            with urllib.request.urlopen(job["url"], timeout=60) as response, target.open("wb") as writer:
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        raise OperationCancelled("Завантаження мода скасовано")
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    writer.write(chunk)
                    digest.update(chunk)
                    bytes_done += len(chunk)
                    _notify_progress(progress, index, len(jobs), relative.as_posix(), bytes_done, total_bytes or max(bytes_done, 1))
            downloaded_files += 1
            if digest.hexdigest() != job["sha256"] or target.stat().st_size != job["size"]:
                raise ModError(f"SHA-256 або розмір не збігається: {relative.name}")

        if target_root.exists():
            backup_root.parent.mkdir(parents=True, exist_ok=True)
            target_root.replace(backup_root)
        target_root.parent.mkdir(parents=True, exist_ok=True)
        staging_root.replace(target_root)
        shutil.rmtree(backup_root, ignore_errors=True)
        record_history("Файли мода завантажено", details=f"{mod_id}; файлів: {len(payload_jobs)}")
        return {"files": len(payload_jobs), "downloaded_files": downloaded_files, "cached": False}
    except Exception:
        if not target_root.exists() and backup_root.exists():
            backup_root.replace(target_root)
        raise
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)
        shutil.rmtree(DATA_DIR / ".mod-download", ignore_errors=True)


_ONLINE_COVER_NAMES = {"cover.png", "cover.jpg", "cover.jpeg"}
_MAX_ONLINE_COVER_SIZE = 20 * 1024 * 1024


def _cover_has_expected_format(path: Path) -> bool:
    """Reject HTML/error pages and malformed files before the UI opens them."""
    try:
        with path.open("rb") as handle:
            header = handle.read(8)
    except OSError:
        return False
    suffix = path.suffix.casefold()
    if suffix == ".download":
        suffix = path.with_suffix("").suffix.casefold()
    if suffix == ".png":
        return header == b"\x89PNG\r\n\x1a\n"
    if suffix in {".jpg", ".jpeg"}:
        return header.startswith(b"\xff\xd8\xff")
    return False


def _sync_online_cover_previews(mods: list[dict], progress=None, cancel_event=None) -> dict:
    """Cache signed card covers while leaving every payload file remote."""
    preview_jobs: list[tuple[dict, dict]] = []
    for raw in mods:
        declared = Path(str(raw.get("cover") or "")).name.casefold()
        candidates = [
            item for item in raw.get("file_index", [])
            if not str(item.get("path", "")).startswith("payload/")
            and Path(str(item.get("path", ""))).name.casefold() in _ONLINE_COVER_NAMES
        ]
        selected = next(
            (item for item in candidates if Path(str(item["path"])).name.casefold() == declared),
            candidates[0] if candidates else None,
        )
        if selected is None:
            raw["cover"] = None
            root = DATA_DIR / "mods" / str(raw.get("id", ""))
            for stale in _ONLINE_COVER_NAMES:
                (root / stale).unlink(missing_ok=True)
            continue
        if int(selected.get("size", 0)) > _MAX_ONLINE_COVER_SIZE:
            raise ModError(f"Обкладинка мода завелика: {raw.get('title') or raw.get('id')}")
        preview_jobs.append((raw, selected))

    downloaded = 0
    cached = 0
    total_bytes = sum(max(0, int(job.get("size", 0))) for _, job in preview_jobs)
    completed_bytes = 0
    for index, (raw, job) in enumerate(preview_jobs, 1):
        if cancel_event is not None and cancel_event.is_set():
            raise OperationCancelled("Завантаження обкладинок скасовано")
        mod_id = str(raw["id"])
        filename = Path(str(job["path"])).name.casefold()
        target_root = DATA_DIR / "mods" / mod_id
        target = target_root / filename
        expected_size = max(0, int(job.get("size", 0)))
        expected_hash = str(job.get("sha256", "")).casefold()
        raw["cover"] = filename
        try:
            valid_cached = (
                target.is_file()
                and target.stat().st_size == expected_size
                and file_sha256(target) == expected_hash
                and _cover_has_expected_format(target)
            )
        except OSError:
            valid_cached = False
        if valid_cached:
            cached += 1
            completed_bytes += expected_size
            _notify_progress(
                progress, index, len(preview_jobs), f"preview/{mod_id}/{filename}",
                completed_bytes, total_bytes or 1,
            )
            continue

        target_root.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".download")
        temporary.unlink(missing_ok=True)
        digest = hashlib.sha256()
        current_bytes = 0
        try:
            with urllib.request.urlopen(str(job["url"]), timeout=30) as response, temporary.open("wb") as writer:
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        raise OperationCancelled("Завантаження обкладинок скасовано")
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    writer.write(chunk)
                    digest.update(chunk)
                    current_bytes += len(chunk)
                    if current_bytes > _MAX_ONLINE_COVER_SIZE:
                        raise ModError(f"Обкладинка мода завелика: {raw.get('title') or mod_id}")
                    _notify_progress(
                        progress, index, len(preview_jobs), f"preview/{mod_id}/{filename}",
                        completed_bytes + current_bytes,
                        total_bytes or max(1, completed_bytes + current_bytes),
                    )
            if (
                current_bytes != expected_size
                or digest.hexdigest() != expected_hash
                or not _cover_has_expected_format(temporary)
            ):
                raise ModError(f"Не вдалося перевірити обкладинку мода: {raw.get('title') or mod_id}")
            os.replace(temporary, target)
            for stale_name in _ONLINE_COVER_NAMES - {filename}:
                (target_root / stale_name).unlink(missing_ok=True)
            downloaded += 1
            completed_bytes += current_bytes
        finally:
            temporary.unlink(missing_ok=True)
    return {"downloaded": downloaded, "cached": cached, "total": len(preview_jobs)}


def _fetch_catalog_manifest(manifest_url: str, progress=None, cancel_event=None) -> dict:
    """Fetch a cold catalog without making the user repeat the operation."""
    attempts = (30, 45, 60)
    last_error: Exception | None = None
    for attempt, timeout in enumerate(attempts, 1):
        if cancel_event is not None and cancel_event.is_set():
            raise OperationCancelled("Оновлення каталогу скасовано")
        _notify_progress(
            progress, 0, 1,
            f"Підготовка каталогу · спроба {attempt}/{len(attempts)}",
            0, 1,
        )
        request = urllib.request.Request(
            manifest_url,
            headers={"Accept": "application/json", "User-Agent": f"UG-MOD-HUB/{EMBEDDED_APP_VERSION}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (TimeoutError, urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt == len(attempts):
                break
            if cancel_event is not None:
                if cancel_event.wait(1.0):
                    raise OperationCancelled("Оновлення каталогу скасовано")
            else:
                time.sleep(1.0)
    if last_error is not None:
        raise last_error
    raise TimeoutError("catalog request timed out")


def update_online_catalog(
    manifest_url: str,
    public_key_b64: str,
    progress=None,
    cancel_event=None,
) -> dict:
    """Download and verify only catalog metadata; mod files stay remote until install."""
    manifest_url = manifest_url.strip()
    public_key_b64 = public_key_b64.strip()
    if not is_allowed_remote_url(manifest_url):
        raise ModError("Вкажіть повну HTTP(S)-адресу каталогу")
    if not public_key_b64:
        raise ModError("Вкажіть публічний ключ цифрового підпису")
    try:
        manifest = _fetch_catalog_manifest(manifest_url, progress, cancel_event)
        payload = verify_catalog_manifest_signature(manifest, public_key_b64)
    except ModError:
        raise
    except Exception as exc:
        raise ModError(f"Не вдалося отримати онлайн-каталог: {exc}") from exc

    mods_raw = payload.get("mods", [])
    if not isinstance(mods_raw, list):
        raise ModError("Некоректний формат каталогу")
    cleaned: list[dict] = []
    cleaned_templates: list[dict] = []
    for item in payload.get("templates", []):
        template_id = _normalize_template_id(item.get("id"))
        if template_id not in {"peds", "acs", "gta3"}:
            template_id = _normalize_template_id(item.get("filename"))
        if template_id not in {"peds", "acs", "gta3"}:
            raise ModError(f"Невідомий IMG-шаблон у каталозі: {template_id}")
        expected = str(item.get("sha256", "")).casefold()
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ModError(f"Некоректний SHA-256 IMG-шаблону: {template_id}")
        url = urllib.parse.urljoin(manifest_url, str(item.get("url", "")))
        if is_trusted_origin_url(manifest_url):
            url = origin_url_for_official(url)
        if not is_allowed_remote_url(url):
            raise ModError(f"Недозволена адреса IMG-шаблону: {template_id}")
        cleaned_templates.append({
            "id": template_id,
            "filename": {"peds": "PEDS.img", "acs": "acs.img", "gta3": "gta3.img"}[template_id],
            "size": max(0, int(item.get("size", 0))),
            "sha256": expected,
            "url": url,
        })
    seen_ids: set[str] = set()
    file_count = 0
    for raw in mods_raw:
        mod = Mod.from_dict(raw)
        if not mod.id or mod.id in seen_ids:
            raise ModError(f"Онлайн-каталог містить порожній або повторний ID: {mod.id or 'без ID'}")
        seen_ids.add(mod.id)
        index: list[dict] = []
        for item in raw.get("files", []):
            relative = Path(str(item.get("path", "")).replace("\\", "/"))
            if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                raise ModError("Каталог містить небезпечний шлях до файла")
            if relative.parts[0] != "payload" and relative.name not in {"cover.png", "cover.jpg", "cover.jpeg"}:
                raise ModError(f"Недозволений файл у каталозі: {relative.as_posix()}")
            expected = str(item.get("sha256", "")).lower()
            if not re.fullmatch(r"[0-9a-f]{64}", expected):
                raise ModError(f"Некоректний SHA-256: {relative.name}")
            url = urllib.parse.urljoin(manifest_url, str(item.get("url", "")))
            if is_trusted_origin_url(manifest_url):
                url = origin_url_for_official(url)
            if not is_allowed_remote_url(url):
                raise ModError(f"Недозволена адреса файла каталогу: {relative.name}")
            index.append({
                "path": relative.as_posix(),
                "size": max(0, int(item.get("size", 0))),
                "sha256": expected,
                "url": url,
            })
            file_count += 1
        clean = {key: value for key, value in raw.items() if key != "files"}
        clean["user_defined"] = False
        clean["file_index"] = index
        cleaned.append(clean)

    preview_result = _sync_online_cover_previews(cleaned, progress, cancel_event)
    current = _read_json(ONLINE_CATALOG_PATH, {"version": "", "mods": []})
    remote_version = str(payload.get("version", ""))
    unchanged = str(current.get("version", "")) == remote_version
    _atomic_json(ONLINE_CATALOG_PATH, {
        "version": remote_version,
        "synced": True,
        "metadata_only": True,
        "mods": cleaned,
        "templates": cleaned_templates,
    })
    _notify_progress(progress, 1, 1, "catalog.json", 1, 1)
    if not unchanged:
        record_history(
            "Онлайн-каталог оновлено",
            details=(
                f"Версія {remote_version}; модів: {len(cleaned)}; "
                f"прев’ю: {preview_result['total']}; файли модів не завантажувались"
            ),
        )
    return {
        "version": remote_version,
        "mods": len(cleaned),
        "files": file_count,
        "downloaded_files": 0,
        "downloaded_previews": preview_result["downloaded"],
        "cached_previews": preview_result["cached"],
        "unchanged": unchanged,
        "metadata_only": True,
    }


def _update_online_catalog_eager_legacy(
    manifest_url: str,
    public_key_b64: str,
    progress=None,
    cancel_event=None,
) -> dict:
    """Download a signed catalog and individual files (never a ZIP archive)."""
    manifest_url = manifest_url.strip()
    public_key_b64 = public_key_b64.strip()
    if not is_allowed_remote_url(manifest_url):
        raise ModError("Вкажіть повну HTTP(S)-адресу каталогу")
    if not public_key_b64:
        raise ModError("Вкажіть публічний ключ цифрового підпису")
    try:
        with urllib.request.urlopen(manifest_url, timeout=20) as response:
            manifest = json.loads(response.read().decode("utf-8"))
        payload = verify_catalog_manifest_signature(manifest, public_key_b64)
    except ModError:
        raise
    except Exception as exc:
        raise ModError(f"Не вдалося отримати онлайн-каталог: {exc}") from exc

    mods_raw = payload.get("mods", [])
    if not isinstance(mods_raw, list):
        raise ModError("Некоректний формат каталогу")
    jobs: list[dict] = []
    seen_ids: set[str] = set()
    for raw in mods_raw:
        mod = Mod.from_dict(raw)
        if not mod.id or mod.id in seen_ids:
            raise ModError(f"Онлайн-каталог містить порожній або повторний ID: {mod.id or 'без ID'}")
        seen_ids.add(mod.id)
        for item in raw.get("files", []):
            relative = Path(str(item.get("path", "")).replace("\\", "/"))
            if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                raise ModError("Каталог містить небезпечний шлях до файла")
            if relative.parts[0] != "payload" and relative.name not in {"cover.png", "cover.jpg", "cover.jpeg"}:
                raise ModError(f"Недозволений файл у каталозі: {relative.as_posix()}")
            expected = str(item.get("sha256", "")).lower()
            if not re.fullmatch(r"[0-9a-f]{64}", expected):
                raise ModError(f"Некоректний SHA-256: {relative.name}")
            url = urllib.parse.urljoin(manifest_url, str(item.get("url", "")))
            if is_trusted_origin_url(manifest_url):
                url = origin_url_for_official(url)
            if not is_allowed_remote_url(url):
                raise ModError(f"Недозволена адреса файлу каталогу: {relative.name}")
            jobs.append({
                "id": mod.id,
                "relative": relative,
                "sha256": expected,
                "size": max(0, int(item.get("size", 0))),
                "url": url,
            })

    def clean_catalog_mods() -> list[dict]:
        cleaned = []
        for raw in mods_raw:
            clean = {key: value for key, value in raw.items() if key != "files"}
            clean["user_defined"] = False
            clean["file_index"] = [
                {
                    "path": str(item.get("path", "")),
                    "size": max(0, int(item.get("size", 0))),
                    "sha256": str(item.get("sha256", "")).lower(),
                }
                for item in raw.get("files", [])
            ]
            cleaned.append(clean)
        return cleaned

    current_catalog = _read_json(ONLINE_CATALOG_PATH, {"version": "", "mods": []})
    remote_version = str(payload.get("version", ""))
    if str(current_catalog.get("version", "")) == remote_version:
        complete = True
        for job in jobs:
            target = DATA_DIR / "mods" / job["id"] / job["relative"]
            try:
                if not target.is_file() or target.stat().st_size != job["size"]:
                    complete = False
                    break
            except OSError:
                complete = False
                break
        if complete:
            _atomic_json(ONLINE_CATALOG_PATH, {
                "version": remote_version,
                "synced": True,
                "mods": clean_catalog_mods(),
            })
            return {
                "version": remote_version,
                "mods": len(mods_raw),
                "files": len(jobs),
                "downloaded_files": 0,
                "unchanged": True,
            }

    staging = DATA_DIR / ".catalog-update"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    total_bytes = sum(job["size"] for job in jobs)
    bytes_done = 0
    downloaded_files = 0
    try:
        for index, job in enumerate(jobs, 1):
            if cancel_event is not None and cancel_event.is_set():
                raise OperationCancelled("Оновлення каталогу скасовано")
            relative = job["relative"]
            target = staging / job["id"] / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            reused = False
            for candidate in (
                DATA_DIR / "mods" / job["id"] / relative,
                APP_DIR / "mods" / job["id"] / relative,
            ):
                try:
                    if (
                        candidate.is_file()
                        and candidate.stat().st_size == job["size"]
                        and file_sha256(candidate) == job["sha256"]
                    ):
                        try:
                            os.link(candidate, target)
                        except OSError:
                            shutil.copy2(candidate, target)
                        bytes_done += job["size"]
                        _notify_progress(
                            progress, index, len(jobs), relative.as_posix(), bytes_done,
                            total_bytes or max(bytes_done, 1),
                        )
                        reused = True
                        break
                except OSError:
                    continue
            if reused:
                continue
            digest = hashlib.sha256()
            with urllib.request.urlopen(job["url"], timeout=30) as response, target.open("wb") as writer:
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        raise OperationCancelled("Оновлення каталогу скасовано")
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    writer.write(chunk)
                    digest.update(chunk)
                    bytes_done += len(chunk)
                    _notify_progress(
                        progress, index, len(jobs), relative.as_posix(), bytes_done,
                        total_bytes or max(bytes_done, 1),
                    )
            downloaded_files += 1
            if digest.hexdigest() != job["sha256"]:
                raise ModError(f"SHA-256 не збігається: {relative.name}")

        backups: list[tuple[Path, Path]] = []
        installed_targets: list[Path] = []
        try:
            old_ids = {str(item.get("id")) for item in current_catalog.get("mods", []) if item.get("id")}
            new_ids = {str(raw["id"]) for raw in mods_raw}
            for stale_id in sorted(old_ids - new_ids):
                target_root = DATA_DIR / "mods" / stale_id
                backup_root = DATA_DIR / ".catalog-backup" / stale_id
                if target_root.exists():
                    backup_root.parent.mkdir(parents=True, exist_ok=True)
                    shutil.rmtree(backup_root, ignore_errors=True)
                    target_root.replace(backup_root)
                    backups.append((backup_root, target_root))
            for raw in mods_raw:
                source_root = staging / raw["id"]
                target_root = DATA_DIR / "mods" / raw["id"]
                backup_root = DATA_DIR / ".catalog-backup" / raw["id"]
                if target_root.exists():
                    backup_root.parent.mkdir(parents=True, exist_ok=True)
                    shutil.rmtree(backup_root, ignore_errors=True)
                    target_root.replace(backup_root)
                    backups.append((backup_root, target_root))
                if source_root.exists():
                    source_root.replace(target_root)
                    installed_targets.append(target_root)
            _atomic_json(ONLINE_CATALOG_PATH, {
                "version": remote_version,
                "synced": True,
                "mods": clean_catalog_mods(),
            })
        except Exception:
            for target in installed_targets:
                shutil.rmtree(target, ignore_errors=True)
            for backup, target in reversed(backups):
                if backup.exists():
                    backup.replace(target)
            raise
        shutil.rmtree(DATA_DIR / ".catalog-backup", ignore_errors=True)
        record_history("Онлайн-каталог оновлено", details=f"Версія {remote_version}; модів: {len(mods_raw)}")
        return {
            "version": remote_version,
            "mods": len(mods_raw),
            "files": len(jobs),
            "downloaded_files": downloaded_files,
            "unchanged": False,
        }
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def detect_game_candidates() -> list[Path]:
    candidates: list[Path] = []
    roots = [
        Path("C:/Games/UKRAINEGTA"),
        Path("D:/Games/UKRAINEGTA"),
        Path("C:/UKRAINEGTA"),
        Path("D:/UKRAINEGTA"),
    ]
    for drive in "CDEFG":
        roots.extend([
            Path(f"{drive}:/Program Files/UKRAINEGTA"),
            Path(f"{drive}:/Program Files (x86)/UKRAINEGTA"),
        ])
    for root in roots:
        ok, _ = validate_game_root(root)
        if ok and root not in candidates:
            candidates.append(root)
    return candidates


def find_game_executable(game_root: str | Path, preferred: str = "") -> Path | None:
    root = resolve_game_root(game_root)
    if preferred:
        value = Path(preferred)
        if value.is_file() and _is_inside(value, root):
            return value
    game = root / "game"
    names = ["UKRAINEGTA.exe", "Multi Theft Auto.exe", "MTA.exe", "gta_sa.exe"]
    for name in names:
        matches = list(game.rglob(name))
        if matches:
            return matches[0]
    executables = list(game.glob("*.exe"))
    return executables[0] if executables else None


def load_mta_servers(game_root: str | Path) -> list[dict]:
    root = resolve_game_root(game_root)
    cache_path = root / "game/mta/config/servercache.xml"
    try:
        import xml.etree.ElementTree as ET
        tree = ET.parse(cache_path)
    except (FileNotFoundError, OSError, ET.ParseError):
        return []
    servers: dict[tuple[str, int], dict] = {}
    for element in tree.iter("server"):
        host = (element.get("ip") or (element.text or "")).strip()
        try:
            port = int(element.get("port", "22003"))
        except ValueError:
            continue
        if not host or not 1 <= port <= 65535:
            continue
        name = element.get("strName", "Сервер").strip()
        servers[(host, port)] = {"name": name, "host": host, "port": port}

    def number(item: dict) -> int:
        match = re.search(r"#\s*0*(\d+)", item["name"])
        return int(match.group(1)) if match else 999

    return sorted(servers.values(), key=lambda item: (number(item), item["name"]))


def configure_quick_connect(game_root: str | Path, host: str, port: int) -> None:
    if not re.fullmatch(r"[A-Za-z0-9.-]+", host or ""):
        raise ModError("Некоректна адреса сервера")
    if not 1 <= int(port) <= 65535:
        raise ModError("Некоректний порт сервера")
    root = resolve_game_root(game_root)
    config_path = root / "game/mta/config/coreconfig.xml"
    try:
        content = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ModError(f"Не вдалося прочитати налаштування MTA: {exc}") from exc
    updated, host_count = re.subn(r"<host>.*?</host>", f"<host>{host}</host>", content, count=1, flags=re.DOTALL)
    updated, port_count = re.subn(r"<port>.*?</port>", f"<port>{int(port)}</port>", updated, count=1, flags=re.DOTALL)
    if not host_count or not port_count:
        raise ModError("У coreconfig.xml не знайдено параметрів Quick Connect")
    fd, tmp_name = tempfile.mkstemp(prefix="coreconfig", suffix=".tmp", dir=config_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(updated)
        os.replace(tmp_name, config_path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def launch_game(
    game_root: str | Path,
    preferred: str = "",
    server_host: str = "",
    server_port: int = 22003,
) -> Path:
    executable = find_game_executable(game_root, preferred)
    if executable is None:
        raise ModError("Не знайдено .exe гри — виберіть його в налаштуваннях")
    arguments = [str(executable)]
    if server_host:
        configure_quick_connect(game_root, server_host, server_port)
        # MTA Launcher processes its own mtasa:// URL and still performs all
        # normal launcher initialization before the connection.
        arguments.append(f"mtasa://{server_host}:{int(server_port)}")
    if os.name == "nt":
        import ctypes
        parameters = subprocess.list2cmdline(arguments[1:])
        result = ctypes.windll.shell32.ShellExecuteW(
            None,
            "runas",
            str(executable),
            parameters,
            str(executable.parent),
            1,
        )
        if result <= 32:
            raise ModError("Запуск від адміністратора скасовано або заблоковано Windows")
    else:
        subprocess.Popen(arguments, cwd=str(executable.parent))
    return executable


def is_admin() -> bool:
    if os.name != "nt":
        return True
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def restart_as_admin() -> None:
    if os.name != "nt":
        raise ModError("Підвищення прав підтримується лише у Windows")
    import ctypes
    executable = sys.executable
    if getattr(sys, "frozen", False):
        arguments = ""
    else:
        arguments = subprocess.list2cmdline([str(Path(__file__).with_name("app.py"))])
    result = ctypes.windll.shell32.ShellExecuteW(None, "runas", executable, arguments, str(APP_DIR), 1)
    if result <= 32:
        raise ModError("Не вдалося перезапустити програму від адміністратора")
