"""Read and safely rebuild encrypted Fastman92 GTA SA IMG v4 archives.

Unchanged payload ciphertext is copied byte-for-byte. Replacement files and
the directory are encrypted again before the verified output is published.
The source archive is never modified by this module.
"""

from __future__ import annotations

import math
import os
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable, Mapping

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


SECTOR_SIZE = 2048
HEADER_SIZE = 0x24
RECORD_SIZE = 0x40
FASTMAN_SIGNATURE = b"VERF"
FASTMAN_MARKER = b"fastman92"


class FastmanImgError(RuntimeError):
    pass


class FastmanOperationCancelled(FastmanImgError):
    pass


@dataclass(frozen=True)
class FastmanEntry:
    name: str
    block_offset: int
    uncompressed_size: int
    stored_size: int
    compression_type: int
    encryption_type: int


@dataclass(frozen=True)
class FastmanArchive:
    path: Path
    prefix: bytes
    metadata_encryption_type: int
    entries: tuple[FastmanEntry, ...]


def _align_up(value: int, alignment: int = SECTOR_SIZE) -> int:
    if value < 0:
        raise FastmanImgError("Некоректний від’ємний розмір IMG")
    return ((value + alignment - 1) // alignment) * alignment if value else 0


def _load_key(value: object, name: str) -> bytes:
    if isinstance(value, str):
        try:
            raw = bytes.fromhex("".join(value.split()))
        except ValueError as exc:
            raise FastmanImgError(f"Некоректний IMG-ключ {name}") from exc
    elif isinstance(value, list) and all(isinstance(item, int) and 0 <= item <= 255 for item in value):
        raw = bytes(value)
    else:
        raise FastmanImgError(f"Некоректний формат IMG-ключа {name}")
    if len(raw) != 32:
        raise FastmanImgError(f"IMG-ключ {name} повинен містити рівно 32 байти")
    return raw


def normalize_keys(payload: Mapping[str, object]) -> dict[int, bytes]:
    """Validate a private keys.json-shaped payload without persisting it."""
    return {
        1: _load_key(payload.get("fastman92_gtasa_var1"), "fastman92_gtasa_var1"),
        2: _load_key(payload.get("fastman92_gtasa_var2"), "fastman92_gtasa_var2"),
    }


def _aes_ecb(data: bytes, key: bytes, *, encrypt: bool) -> bytes:
    full = len(data) // 16 * 16
    if not full:
        return data
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    transform = cipher.encryptor() if encrypt else cipher.decryptor()
    return transform.update(data[:full]) + transform.finalize() + data[full:]


def _crypt(data: bytes, encryption_type: int, keys: Mapping[int, bytes], *, encrypt: bool) -> bytes:
    if encryption_type == 0:
        return data
    key = keys.get(encryption_type)
    if key is None:
        raise FastmanImgError(f"Немає IMG-ключа типу {encryption_type}")
    return _aes_ecb(data, key, encrypt=encrypt)


def _read_exact(handle: BinaryIO, size: int) -> bytes:
    data = handle.read(size)
    if len(data) != size:
        raise FastmanImgError("Неочікуваний кінець IMG-архіву")
    return data


def parse_fastman_archive(path: str | Path, keys: Mapping[int, bytes]) -> FastmanArchive:
    path = Path(path).expanduser().resolve()
    try:
        archive_size = path.stat().st_size
        handle = path.open("rb")
    except OSError as exc:
        raise FastmanImgError(f"Не вдалося відкрити {path.name}: {exc}") from exc
    with handle:
        header = _read_exact(handle, HEADER_SIZE)
        if header[:4] != FASTMAN_SIGNATURE or not header[8:20].startswith(FASTMAN_MARKER):
            raise FastmanImgError(f"{path.name} не є зашифрованим Fastman92 IMG v4")
        flags = struct.unpack_from("<I", header, 4)[0]
        if flags & 0xF != 1:
            raise FastmanImgError("Непідтримувані прапорці Fastman92 IMG")
        metadata_type = (flags >> 4) & 0xF
        marker, count, reserved1, reserved2 = struct.unpack(
            "<IIII", _crypt(header[0x14:0x24], metadata_type, keys, encrypt=False)
        )
        if marker != 1 or reserved1 != 0 or reserved2 != 0:
            raise FastmanImgError("Неправильний ключ або пошкоджений заголовок IMG")
        if count > 10_000_000 or HEADER_SIZE + count * RECORD_SIZE > archive_size:
            raise FastmanImgError("Некоректна кількість записів IMG")

        entries: list[FastmanEntry] = []
        names: set[str] = set()
        for _ in range(count):
            record = _crypt(_read_exact(handle, RECORD_SIZE), metadata_type, keys, encrypt=False)
            block, sectors1, pad1, sectors2, pad2, props = struct.unpack_from("<IHHHHI", record, 0)
            raw_name = record[16:56].split(b"\0", 1)[0]
            try:
                name = raw_name.decode("ascii")
            except UnicodeDecodeError as exc:
                raise FastmanImgError("Некоректна назва запису IMG") from exc
            folded = name.casefold()
            if not name or folded in names:
                raise FastmanImgError(f"Порожній або повторний запис IMG: {name!r}")
            names.add(folded)
            if struct.unpack_from("<II", record, 0x38) != (0, 0) or pad1 > 2047 or pad2 > 2047:
                raise FastmanImgError(f"Пошкоджений запис IMG: {name}")
            uncompressed = sectors1 * SECTOR_SIZE - pad1 if sectors1 else 0
            stored = sectors2 * SECTOR_SIZE - pad2 if sectors2 else 0
            compression_type = props & 0xF
            encryption_type = (props >> 4) & 0xF
            if compression_type > 2 or encryption_type > 2:
                raise FastmanImgError(f"Непідтримуваний тип запису IMG: {name}")
            if block * SECTOR_SIZE + stored > archive_size:
                raise FastmanImgError(f"Запис виходить за межі IMG: {name}")
            entries.append(FastmanEntry(name, block, uncompressed, stored, compression_type, encryption_type))
    return FastmanArchive(path, header[:0x14], metadata_type, tuple(entries))


def archive_contains(archive: FastmanArchive, *entry_names: str) -> bool:
    available = {entry.name.casefold() for entry in archive.entries}
    return all(str(name).casefold() in available for name in entry_names)


def _encode_size(size: int) -> tuple[int, int]:
    sectors = math.ceil(size / SECTOR_SIZE) if size else 0
    if sectors > 0xFFFF:
        raise FastmanImgError(f"Один файл завеликий для IMG: {size} байтів")
    return sectors, sectors * SECTOR_SIZE - size if sectors else 0


def _make_record(entry: FastmanEntry) -> bytes:
    sectors1, pad1 = _encode_size(entry.uncompressed_size)
    sectors2, pad2 = _encode_size(entry.stored_size)
    try:
        name = entry.name.encode("ascii")
    except UnicodeEncodeError as exc:
        raise FastmanImgError(f"Назва IMG не є ASCII: {entry.name}") from exc
    if not name or len(name) > 39:
        raise FastmanImgError(f"Назва запису IMG задовга: {entry.name}")
    record = bytearray(RECORD_SIZE)
    struct.pack_into(
        "<IHHHHI", record, 0, entry.block_offset, sectors1, pad1, sectors2, pad2,
        entry.compression_type | (entry.encryption_type << 4),
    )
    record[16:16 + len(name)] = name
    return bytes(record)


def _copy_exact(
    source: BinaryIO,
    output: BinaryIO,
    offset: int,
    size: int,
    on_bytes: Callable[[int], None],
    cancel_event=None,
) -> None:
    source.seek(offset)
    remaining = size
    while remaining:
        if cancel_event is not None and cancel_event.is_set():
            raise FastmanOperationCancelled("Операцію із IMG скасовано")
        chunk = source.read(min(8 * 1024 * 1024, remaining))
        if not chunk:
            raise FastmanImgError("Неочікуваний кінець IMG під час копіювання")
        output.write(chunk)
        remaining -= len(chunk)
        on_bytes(len(chunk))


def repack_fastman_archive(
    source_path: str | Path,
    output_path: str | Path,
    replacements: Mapping[str, str | Path],
    keys: Mapping[int, bytes],
    progress: Callable[[int, int, str], None] | None = None,
    cancel_event=None,
    allow_additions: bool = False,
) -> FastmanArchive:
    """Create and verify a new encrypted archive containing replacements."""
    archive = parse_fastman_archive(source_path, keys)
    source_path = archive.path
    output_path = Path(output_path).expanduser().resolve()
    if source_path == output_path:
        raise FastmanImgError("IMG не можна перебудовувати поверх джерела")

    prepared_replacements: dict[str, tuple[str, Path]] = {}
    for name, raw_path in replacements.items():
        path = Path(raw_path).expanduser().resolve()
        if not path.is_file():
            raise FastmanImgError(f"Не знайдено файл заміни: {path}")
        entry_name = str(name)
        try:
            encoded_name = entry_name.encode("ascii")
        except UnicodeEncodeError as exc:
            raise FastmanImgError(f"Назва IMG не є ASCII: {entry_name}") from exc
        if not encoded_name or len(encoded_name) > 39:
            raise FastmanImgError(f"Некоректна назва запису IMG: {entry_name}")
        prepared_replacements[entry_name.casefold()] = (entry_name, path)
    known = {entry.name.casefold() for entry in archive.entries}
    missing = sorted(name for name in prepared_replacements if name not in known)
    if missing and not allow_additions:
        raise FastmanImgError("У зашифрованому IMG немає: " + ", ".join(missing))

    metadata_size = HEADER_SIZE + (len(archive.entries) + len(missing)) * RECORD_SIZE
    data_start = _align_up(metadata_size)
    prepared: list[tuple[FastmanEntry, Path | None]] = []
    current_block = data_start // SECTOR_SIZE
    for original in archive.entries:
        replacement_item = prepared_replacements.get(original.name.casefold())
        replacement = replacement_item[1] if replacement_item is not None else None
        if replacement is None:
            rewritten = FastmanEntry(
                original.name, current_block, original.uncompressed_size, original.stored_size,
                original.compression_type, original.encryption_type,
            )
        else:
            size = replacement.stat().st_size
            rewritten = FastmanEntry(original.name, current_block, size, size, 0, original.encryption_type)
        prepared.append((rewritten, replacement))
        current_block += math.ceil(rewritten.stored_size / SECTOR_SIZE) if rewritten.stored_size else 0
    for folded_name in missing:
        original_name, replacement = prepared_replacements[folded_name]
        size = replacement.stat().st_size
        rewritten = FastmanEntry(
            original_name, current_block, size, size, 0, archive.metadata_encryption_type
        )
        prepared.append((rewritten, replacement))
        current_block += math.ceil(size / SECTOR_SIZE) if size else 0

    total_bytes = sum(item.stored_size for item, _ in prepared)
    done = 0

    def report(delta: int, name: str = "Перебудова зашифрованого IMG") -> None:
        nonlocal done
        done += delta
        if progress is not None:
            progress(done, max(total_bytes, 1), name)

    partial = output_path.with_name(output_path.name + ".partial")
    output_path.unlink(missing_ok=True)
    partial.unlink(missing_ok=True)
    try:
        with source_path.open("rb") as source, partial.open("xb") as output:
            output.write(archive.prefix)
            output.write(_crypt(
                struct.pack("<IIII", 1, len(prepared), 0, 0),
                archive.metadata_encryption_type, keys, encrypt=True,
            ))
            for entry, _ in prepared:
                output.write(_crypt(_make_record(entry), archive.metadata_encryption_type, keys, encrypt=True))
            output.write(b"\0" * (data_start - output.tell()))

            originals = {entry.name.casefold(): entry for entry in archive.entries}
            for entry, replacement in prepared:
                if cancel_event is not None and cancel_event.is_set():
                    raise FastmanOperationCancelled("Операцію із IMG скасовано")
                if replacement is None:
                    original = originals[entry.name.casefold()]
                    _copy_exact(
                        source, output, original.block_offset * SECTOR_SIZE, original.stored_size,
                        lambda amount, item=entry.name: report(amount, item), cancel_event,
                    )
                else:
                    raw = replacement.read_bytes()
                    output.write(_crypt(raw, entry.encryption_type, keys, encrypt=True))
                    report(len(raw), entry.name)
                padding = _align_up(entry.stored_size) - entry.stored_size
                if padding:
                    output.write(b"\0" * padding)
            output.flush()
            os.fsync(output.fileno())

        verified = parse_fastman_archive(partial, keys)
        if tuple(item[0] for item in prepared) != verified.entries:
            raise FastmanImgError("Перевірка перебудованого IMG не пройдена")
        os.replace(partial, output_path)
        return parse_fastman_archive(output_path, keys)
    except Exception:
        partial.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)
        raise
