import tempfile
import unittest
import json
import base64
import hashlib
import io
import os
import ctypes
import threading
import struct
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import MagicMock, patch

import core
import fastman_img


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.process_patch = patch.object(core, "running_game_processes", return_value=[])
        self.process_patch.start()
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "UKRAINEGTA"
        (self.root / "game" / "bin" / "data").mkdir(parents=True)
        self.data = Path(self.temp.name) / "state"
        self.payload = Path(self.temp.name) / "payload"
        self.payload.mkdir()
        self.mod = core.Mod("sky", "Sky", "Графика", "test", "game/bin/data")
        self.fastman_key_payload = {
            "fastman92_gtasa_var1": "11" * 32,
            "fastman92_gtasa_var2": "22" * 32,
        }
        core.set_fastman_test_keys(self.fastman_key_payload)

    def tearDown(self):
        core.set_fastman_test_keys(None)
        self.temp.cleanup()
        self.process_patch.stop()

    def test_install_and_restore_existing_file(self):
        target = self.root / "game" / "bin" / "data" / "timecyc.dat"
        target.write_text("original", encoding="utf-8")
        (self.payload / "timecyc.dat").write_text("modded", encoding="utf-8")
        with patch.object(core, "DATA_DIR", self.data), \
             patch.object(core, "STATE_PATH", self.data / "installed.json"), \
             patch.object(core, "BACKUP_DIR", self.data / "backups"), \
             patch.object(core, "payload_dir", return_value=self.payload):
            core.install_mod(self.mod, self.root)
            self.assertEqual(target.read_text(encoding="utf-8"), "modded")
            core.uninstall_mod(self.mod, self.root)
            self.assertEqual(target.read_text(encoding="utf-8"), "original")

    def test_install_and_remove_new_file(self):
        target = self.root / "game" / "bin" / "data" / "new.dat"
        (self.payload / "new.dat").write_text("modded", encoding="utf-8")
        with patch.object(core, "DATA_DIR", self.data), \
             patch.object(core, "STATE_PATH", self.data / "installed.json"), \
             patch.object(core, "BACKUP_DIR", self.data / "backups"), \
             patch.object(core, "payload_dir", return_value=self.payload):
            core.install_mod(self.mod, self.root)
            self.assertTrue(target.exists())
            core.uninstall_mod(self.mod, self.root)
            self.assertFalse(target.exists())

    def test_accepts_game_subfolder(self):
        ok, resolved = core.validate_game_root(self.root / "game")
        self.assertTrue(ok)
        self.assertEqual(Path(resolved), self.root.resolve())

    def test_account_login_uses_json_and_returns_opaque_token(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        response.read.return_value = json.dumps({
            "ok": True,
            "token": "a" * 64,
            "user": {"display_name": "Tester", "email": "test@example.com", "email_verified": True},
        }).encode("utf-8")
        with patch.object(core.urllib.request, "urlopen", return_value=response) as urlopen:
            result = core.login_account(core.DEFAULT_ACCOUNT_URL, "test@example.com", "secret-pass")
        self.assertEqual(result["token"], "a" * 64)
        request = urlopen.call_args.args[0]
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["action"], "login")
        self.assertEqual(body["email"], "test@example.com")

    def test_account_token_is_protected_for_local_storage(self):
        protected = core.protect_local_secret("b" * 64)
        self.assertNotIn("b" * 64, protected)
        self.assertEqual(core.unprotect_local_secret(protected), "b" * 64)

    @staticmethod
    def _write_test_img(path, entries):
        """Create a tiny encrypted Fastman92 v4 archive for replacement tests."""
        path.parent.mkdir(parents=True, exist_ok=True)
        keys = fastman_img.normalize_keys({
            "fastman92_gtasa_var1": "11" * 32,
            "fastman92_gtasa_var2": "22" * 32,
        })
        data_start = fastman_img._align_up(
            fastman_img.HEADER_SIZE + len(entries) * fastman_img.RECORD_SIZE
        )
        sector = data_start // fastman_img.SECTOR_SIZE
        prepared = []
        for name, payload in entries:
            entry = fastman_img.FastmanEntry(name, sector, len(payload), len(payload), 0, 2)
            prepared.append((entry, payload))
            sectors = max(1, (len(payload) + fastman_img.SECTOR_SIZE - 1) // fastman_img.SECTOR_SIZE)
            sector += sectors
        prefix = b"VERF" + struct.pack("<I", 0x21) + b"fastman92\0\0\0"
        content = bytearray(prefix)
        content.extend(fastman_img._crypt(struct.pack("<IIII", 1, len(prepared), 0, 0), 2, keys, encrypt=True))
        for entry, _ in prepared:
            content.extend(fastman_img._crypt(fastman_img._make_record(entry), 2, keys, encrypt=True))
        content.extend(b"\0" * (data_start - len(content)))
        for entry, payload in prepared:
            encrypted = fastman_img._crypt(payload, entry.encryption_type, keys, encrypt=True)
            content.extend(encrypted)
            content.extend(b"\0" * (fastman_img._align_up(len(payload)) - len(payload)))
        path.write_bytes(content)

    def _read_fastman_payload(self, path, entry_name):
        keys = fastman_img.normalize_keys(self.fastman_key_payload)
        archive = fastman_img.parse_fastman_archive(path, keys)
        entry = next(item for item in archive.entries if item.name.casefold() == entry_name.casefold())
        with Path(path).open("rb") as handle:
            handle.seek(entry.block_offset * fastman_img.SECTOR_SIZE)
            raw = handle.read(entry.stored_size)
        return fastman_img._crypt(raw, entry.encryption_type, keys, encrypt=False)

    def test_skin_install_rebuilds_open_img_and_uninstall_restores_original(self):
        template_dir = self.data / "img_templates"
        template = template_dir / "gta3.img"
        self._write_test_img(template, [
            ("bmybe.dff", b"ORIGINAL_DFF"),
            ("bmybe.txd", b"ORIGINAL_TXD"),
        ])
        game_archive = self.root / "game" / "bin" / "models" / "gta3.img"
        game_archive.parent.mkdir(parents=True)
        game_archive.write_bytes(template.read_bytes())
        encrypted_original = game_archive.read_bytes()
        (self.payload / "tractor.dff").write_bytes(b"NEW_DFF_MODEL")
        (self.payload / "tractor.txd").write_bytes(b"NEW_TXD_TEXTURE")
        mod = core.Mod(
            "tractor_skin", "Тракторист", "Скіни", "test",
            "game/bin/models", mod_type="skin",
        )
        target = core.SkinTarget(18, "Тракторист", "bmybe", "bmybe")
        with patch.object(core, "DATA_DIR", self.data), \
             patch.object(core, "STATE_PATH", self.data / "installed.json"), \
             patch.object(core, "BACKUP_DIR", self.data / "backups"), \
             patch.object(core, "SKIN_TEMPLATE_DIR", template_dir), \
             patch.object(core, "payload_dir", return_value=self.payload):
            core.install_mod(
                mod, self.root, skin_target=target,
                skin_archive="gta3.img", skin_template=template,
            )
            self.assertEqual(self._read_fastman_payload(game_archive, "bmybe.dff"), b"NEW_DFF_MODEL")
            self.assertEqual(self._read_fastman_payload(game_archive, "bmybe.txd"), b"NEW_TXD_TEXTURE")
            core.uninstall_mod(mod, self.root)
            self.assertEqual(game_archive.read_bytes(), encrypted_original)

    def test_multiple_skin_replacements_accumulate_in_one_archive(self):
        template_dir = self.data / "img_templates"
        template = template_dir / "PEDS.img"
        self._write_test_img(template, [
            ("skin18.dff", b"A_DFF"), ("skin18.txd", b"A_TXD"),
            ("skin19.dff", b"B_DFF"), ("skin19.txd", b"B_TXD"),
        ])
        game_archive = self.root / "game" / "bin" / "models" / "PEDS.img"
        game_archive.parent.mkdir(parents=True)
        game_archive.write_bytes(template.read_bytes())
        payloads = {}
        for mod_id, marker in (("skin_a", b"FIRST"), ("skin_b", b"SECOND")):
            folder = self.data / "payloads" / mod_id
            folder.mkdir(parents=True)
            (folder / "model.dff").write_bytes(marker + b"_DFF")
            (folder / "texture.txd").write_bytes(marker + b"_TXD")
            payloads[mod_id] = folder
        first = core.Mod("skin_a", "Skin A", "Скіни", "", "game/bin/models", mod_type="skin")
        second = core.Mod("skin_b", "Skin B", "Скіни", "", "game/bin/models", mod_type="skin")
        first_target = core.SkinTarget(18, "A", "skin18", "skin18")
        second_target = core.SkinTarget(19, "B", "skin19", "skin19")

        def payload_for(mod_id):
            return payloads[mod_id]

        with patch.object(core, "DATA_DIR", self.data), \
             patch.object(core, "STATE_PATH", self.data / "installed.json"), \
             patch.object(core, "BACKUP_DIR", self.data / "backups"), \
             patch.object(core, "SKIN_TEMPLATE_DIR", template_dir), \
             patch.object(core, "payload_dir", side_effect=payload_for):
            core.install_mod(first, self.root, skin_target=first_target, skin_archive="PEDS.img", skin_template=template)
            core.install_mod(second, self.root, skin_target=second_target, skin_archive="PEDS.img", skin_template=template)
            core.uninstall_mod(first, self.root)
            self.assertEqual(self._read_fastman_payload(game_archive, "skin18.dff"), b"A_DFF")
            self.assertEqual(self._read_fastman_payload(game_archive, "skin19.dff"), b"SECOND_DFF")

    def test_accessory_install_uses_acs_archive_and_restores_encrypted_original(self):
        template_dir = self.data / "accessory_img_templates"
        template = template_dir / "acs.img"
        self._write_test_img(template, [
            ("m_acs53.dff", b"OLD_MODEL"),
        ])
        game_archive = self.root / "game/bin/data/maps/ACS/acs.img"
        game_archive.parent.mkdir(parents=True)
        game_archive.write_bytes(template.read_bytes())
        encrypted_original = game_archive.read_bytes()
        (self.payload / "new_accessory.dff").write_bytes(b"NEW_ACCESSORY_MODEL")
        (self.payload / "new_accessory.txd").write_bytes(b"NEW_ACCESSORY_TEXTURE")
        mod = core.Mod(
            "red_tie", "Червона краватка", "Аксесуари", "test",
            "game/bin/data/maps/ACS", mod_type="accessory",
        )
        target = core.AccessoryTarget(637, "Червона краватка", "m_acs53", "akses", "tie_red", "neck")
        with patch.object(core, "DATA_DIR", self.data), \
             patch.object(core, "STATE_PATH", self.data / "installed.json"), \
             patch.object(core, "BACKUP_DIR", self.data / "backups"), \
             patch.object(core, "ACCESSORY_TEMPLATE_DIR", template_dir), \
             patch.object(core, "payload_dir", return_value=self.payload):
            core.install_mod(
                mod, self.root, accessory_target=target,
            )
            self.assertEqual(self._read_fastman_payload(game_archive, "m_acs53.dff"), b"NEW_ACCESSORY_MODEL")
            self.assertEqual(self._read_fastman_payload(game_archive, "akses.txd"), b"NEW_ACCESSORY_TEXTURE")
            core.uninstall_mod(mod, self.root)
            self.assertEqual(game_archive.read_bytes(), encrypted_original)

    def test_weapon_install_uses_gta3_archive_and_restores_encrypted_original(self):
        template_dir = self.data / "weapon_img_templates"
        template = template_dir / "gta3.img"
        self._write_test_img(template, [
            ("ak47.dff", b"OLD_WEAPON_MODEL"),
            ("ak47.txd", b"OLD_WEAPON_TEXTURE"),
        ])
        game_archive = self.root / "game/bin/models/gta3.img"
        game_archive.parent.mkdir(parents=True)
        game_archive.write_bytes(template.read_bytes())
        encrypted_original = game_archive.read_bytes()
        (self.payload / "custom.dff").write_bytes(b"NEW_WEAPON_MODEL")
        (self.payload / "custom.txd").write_bytes(b"NEW_WEAPON_TEXTURE")
        mod = core.Mod(
            "ak_custom", "AK Custom", "Заміна зброї", "test",
            "game/bin/models", mod_type="weapon",
        )
        target = core.WeaponTarget(355, "AK-47", "ak47", "ak47")
        with patch.object(core, "DATA_DIR", self.data), \
             patch.object(core, "STATE_PATH", self.data / "installed.json"), \
             patch.object(core, "BACKUP_DIR", self.data / "backups"), \
             patch.object(core, "WEAPON_TEMPLATE_DIR", template_dir), \
             patch.object(core, "payload_dir", return_value=self.payload):
            core.install_mod(mod, self.root, weapon_target=target)
            self.assertEqual(self._read_fastman_payload(game_archive, "ak47.dff"), b"NEW_WEAPON_MODEL")
            self.assertEqual(self._read_fastman_payload(game_archive, "ak47.txd"), b"NEW_WEAPON_TEXTURE")
            core.uninstall_mod(mod, self.root)
            self.assertEqual(game_archive.read_bytes(), encrypted_original)

    def test_accessories_with_shared_txd_conflict(self):
        template_dir = self.data / "accessory_img_templates"
        template = template_dir / "acs.img"
        self._write_test_img(template, [
            ("a.dff", b"A"), ("b.dff", b"B"), ("shared.txd", b"T"),
        ])
        game_archive = self.root / "game/bin/data/maps/ACS/acs.img"
        game_archive.parent.mkdir(parents=True)
        game_archive.write_bytes(template.read_bytes())
        payloads = {}
        for mod_id in ("accessory_a", "accessory_b"):
            folder = self.data / "payloads" / mod_id
            folder.mkdir(parents=True)
            (folder / "item.dff").write_bytes(mod_id.encode())
            (folder / "item.txd").write_bytes(mod_id.encode())
            payloads[mod_id] = folder
        first = core.Mod("accessory_a", "A", "Аксесуари", "", "game/bin/data/maps/ACS", mod_type="accessory")
        second = core.Mod("accessory_b", "B", "Аксесуари", "", "game/bin/data/maps/ACS", mod_type="accessory")
        target_a = core.AccessoryTarget(1, "A", "a", "shared", "a", "head")
        target_b = core.AccessoryTarget(2, "B", "b", "shared", "b", "face")
        with patch.object(core, "DATA_DIR", self.data), \
             patch.object(core, "STATE_PATH", self.data / "installed.json"), \
             patch.object(core, "BACKUP_DIR", self.data / "backups"), \
             patch.object(core, "ACCESSORY_TEMPLATE_DIR", template_dir), \
             patch.object(core, "payload_dir", side_effect=lambda mod_id: payloads[mod_id]):
            core.install_mod(first, self.root, accessory_target=target_a, accessory_template=template)
            with self.assertRaises(core.ModError):
                core.install_mod(second, self.root, accessory_target=target_b, accessory_template=template)

    def test_online_img_template_download_is_lazy_and_sha_verified(self):
        content = b"VER2" + b"web template"
        online = self.data / "online_catalog.json"
        online.parent.mkdir(parents=True, exist_ok=True)
        online.write_text(json.dumps({
            "templates": [{
                "id": "peds",
                "filename": "PEDS.img",
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "url": "https://mods.example.com/uploads/templates/PEDS.img",
            }],
        }), encoding="utf-8")
        template_dir = self.data / "img_templates"
        with patch.object(core, "DATA_DIR", self.data), \
             patch.object(core, "ONLINE_CATALOG_PATH", online), \
             patch.object(core, "SKIN_TEMPLATE_DIR", template_dir), \
             patch.object(core.urllib.request, "urlopen", return_value=io.BytesIO(content)):
            result = core.ensure_online_template("peds")
            self.assertEqual(result, template_dir / "PEDS.img")
            self.assertEqual(result.read_bytes(), content)

    def test_verifies_rsa_sha256_catalog_signature(self):
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa

        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        payload = {"mods": [], "version": "20260806.1", "generated_at": "2026-08-06T00:00:00Z"}
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        signature = private_key.sign(canonical, padding.PKCS1v15(), hashes.SHA256())
        public_pem = private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        manifest = {
            "algorithm": "rsa-sha256",
            "payload": payload,
            "signature": base64.b64encode(signature).decode("ascii"),
        }
        verified = core.verify_catalog_manifest_signature(
            manifest, base64.b64encode(public_pem).decode("ascii")
        )
        self.assertEqual(verified, payload)

    def test_rejects_tampered_rsa_catalog(self):
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa

        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        original = {"mods": [], "version": "1"}
        canonical = json.dumps(original, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        signature = private_key.sign(canonical, padding.PKCS1v15(), hashes.SHA256())
        public_pem = private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        manifest = {
            "algorithm": "rsa-sha256",
            "payload": {"mods": [], "version": "2"},
            "signature": base64.b64encode(signature).decode("ascii"),
        }
        with self.assertRaises(core.ModError):
            core.verify_catalog_manifest_signature(
                manifest, base64.b64encode(public_pem).decode("ascii")
            )

    def test_version_comparison_for_application_updates(self):
        self.assertLess(core._version_tuple("3.1"), core._version_tuple("3.2"))
        self.assertEqual(core._version_tuple("3.1"), core._version_tuple("3.1.0"))
        self.assertLess(core._version_tuple("0.1"), core._version_tuple("0.1.1"))
        with self.assertRaises(core.ModError):
            core._version_tuple("latest")

    def test_web_hosting_health_check_accepts_configured_service(self):
        response = MagicMock()
        response.status = 200
        response.read.return_value = json.dumps({
            "ok": True,
            "service": "UG MOD HUB",
            "database": "mysql",
        }).encode("utf-8")
        response.__enter__.return_value = response
        with patch.object(core.urllib.request, "urlopen", return_value=response) as request:
            result = core.check_web_hosting("https://mods.example.com/api/health.php", timeout=3)
        self.assertTrue(result["ok"])
        self.assertEqual(request.call_args.kwargs["timeout"], 3.0)

    def test_web_hosting_health_check_rejects_unready_service(self):
        response = MagicMock()
        response.status = 200
        response.read.return_value = json.dumps({
            "ok": False,
            "service": "UG MOD HUB",
        }).encode("utf-8")
        response.__enter__.return_value = response
        with patch.object(core.urllib.request, "urlopen", return_value=response):
            with self.assertRaises(core.ModError):
                core.check_web_hosting("https://mods.example.com/api/health.php")

    def test_web_hosting_health_check_requires_https(self):
        with self.assertRaises(core.ModError):
            core.check_web_hosting("http://mods.example.com/api/health.php")

    def test_web_hosting_health_check_accepts_pinned_origin(self):
        public_key = "trusted-public-key"
        response = MagicMock()
        response.status = 200
        response.read.return_value = json.dumps({
            "ok": True,
            "service": "UG MOD HUB",
            "public_key": public_key,
        }).encode("utf-8")
        response.__enter__.return_value = response
        fingerprint = hashlib.sha256(public_key.encode("utf-8")).hexdigest()
        with patch.object(core, "OFFICIAL_PUBLIC_KEY_SHA256", fingerprint), \
             patch.object(core.urllib.request, "urlopen", return_value=response):
            result = core.check_web_hosting(core.DEFAULT_WEB_HEALTH_URL)
        self.assertTrue(result["ok"])

    def test_web_hosting_health_check_rejects_changed_origin_key(self):
        response = MagicMock()
        response.status = 200
        response.read.return_value = json.dumps({
            "ok": True,
            "service": "UG MOD HUB",
            "public_key": "substituted-key",
        }).encode("utf-8")
        response.__enter__.return_value = response
        with patch.object(core.urllib.request, "urlopen", return_value=response):
            with self.assertRaises(core.ModError):
                core.check_web_hosting(core.DEFAULT_WEB_HEALTH_URL)

    def test_official_download_url_is_routed_to_origin(self):
        self.assertEqual(
            core.origin_url_for_official("https://ug-mods-hub.qniks.me/uploads/app/UG.exe"),
            "http://149.50.111.56:20069/uploads/app/UG.exe",
        )
        self.assertFalse(core.is_allowed_remote_url("http://149.50.111.56:20070/api/health.php"))

    def test_settings_migrate_official_domain_api_urls_to_origin(self):
        settings_path = self.data / "settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(json.dumps({
            "health_url": "https://ug-mods-hub.qniks.me/main/api/health.php",
            "catalog_url": "https://ug-mods-hub.qniks.me/main/api/catalog.php",
            "update_url": "https://ug-mods-hub.qniks.me/main/api/latest.php",
        }), encoding="utf-8")
        with patch.object(core, "SETTINGS_PATH", settings_path):
            settings = core.load_settings()
        self.assertEqual(settings["health_url"], core.DEFAULT_WEB_HEALTH_URL)
        self.assertEqual(settings["catalog_url"], core.DEFAULT_CATALOG_URL)
        self.assertEqual(settings["update_url"], core.DEFAULT_UPDATE_URL)

    def test_release_catalog_uses_online_catalog_as_authoritative_source(self):
        catalog_path = Path(self.temp.name) / "catalog.json"
        online_path = self.data / "online_catalog.json"
        catalog_path.write_text(json.dumps({
            "app_name": "UG MOD HUB", "version": "3.5",
            "mods": [{"id": "old_mod", "title": "Old", "category": "Інше", "description": "old", "destination": "game"}],
        }), encoding="utf-8")
        online_path.parent.mkdir(parents=True, exist_ok=True)
        online_path.write_text(json.dumps({
            "version": "2", "mods": [
                {"id": "site_mod", "title": "Site", "category": "Інше", "description": "site", "destination": "game"},
            ],
        }), encoding="utf-8")
        with patch.object(core, "APP_DIR", Path(self.temp.name)), \
             patch.object(core, "CATALOG_PATH", catalog_path), \
             patch.object(core, "ONLINE_CATALOG_PATH", online_path), \
             patch.object(core, "is_dev_mode", return_value=False):
            _, mods = core.load_catalog()
        self.assertEqual([mod.id for mod in mods], ["site_mod"])

    def test_unchanged_online_catalog_does_not_redownload_or_conflict_with_dev(self):
        online_path = self.data / "online_catalog.json"
        target = self.data / "mods" / "same_id" / "payload" / "file.dat"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"payload")
        digest = hashlib.sha256(b"payload").hexdigest()
        online_path.write_text(json.dumps({"version": "2", "mods": [{"id": "same_id"}]}), encoding="utf-8")
        (self.data / "custom_mods.json").write_text(json.dumps({"mods": [{"id": "same_id"}]}), encoding="utf-8")
        payload = {
            "version": "2",
            "mods": [{
                "id": "same_id", "title": "Site", "category": "Інше",
                "description": "site", "destination": "game",
                "files": [{
                    "path": "payload/file.dat", "url": "file.dat",
                    "size": 7, "sha256": digest,
                }],
            }],
        }
        response = MagicMock()
        response.read.return_value = b"{}"
        response.__enter__.return_value = response
        with patch.object(core, "DATA_DIR", self.data), \
             patch.object(core, "ONLINE_CATALOG_PATH", online_path), \
             patch.object(core.urllib.request, "urlopen", return_value=response) as urlopen, \
             patch.object(core, "verify_catalog_manifest_signature", return_value=payload):
            result = core.update_online_catalog("https://mods.example.com/api/catalog.php", "key")
        self.assertTrue(result["unchanged"])
        self.assertEqual(result["downloaded_files"], 0)
        self.assertEqual(urlopen.call_count, 1)

    def test_catalog_refresh_downloads_metadata_only(self):
        online_path = self.data / "online_catalog.json"
        content = b"remote mod data"
        payload = {
            "version": "3",
            "templates": [{
                "id": "peds", "filename": "PEDS.img",
                "url": "https://mods.example.com/uploads/templates/PEDS.img",
                "size": 123, "sha256": "a" * 64,
            }, {
                "id": " \ufeffGTA3.img\u200b ", "filename": "gta3.img",
                "url": "https://mods.example.com/uploads/templates/gta3.img",
                "size": 456, "sha256": "b" * 64,
            }],
            "mods": [{
                "id": "remote_sky", "title": "Remote sky", "category": "Небо",
                "description": "site", "destination": "game/bin/data",
                "files": [{
                    "path": "payload/timecyc.dat",
                    "url": "https://mods.example.com/files/timecyc.dat",
                    "size": len(content), "sha256": hashlib.sha256(content).hexdigest(),
                }],
            }],
        }
        response = MagicMock()
        response.read.return_value = b"{}"
        response.__enter__.return_value = response
        with patch.object(core, "DATA_DIR", self.data), \
             patch.object(core, "ONLINE_CATALOG_PATH", online_path), \
             patch.object(core.urllib.request, "urlopen", return_value=response) as urlopen, \
             patch.object(core, "verify_catalog_manifest_signature", return_value=payload):
            result = core.update_online_catalog("https://mods.example.com/api/catalog.php", "key")
        self.assertTrue(result["metadata_only"])
        self.assertEqual(result["downloaded_files"], 0)
        self.assertEqual(urlopen.call_count, 1)
        self.assertFalse((self.data / "mods" / "remote_sky" / "payload" / "timecyc.dat").exists())
        saved = json.loads(online_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["mods"][0]["file_index"][0]["url"], "https://mods.example.com/files/timecyc.dat")
        self.assertEqual(saved["templates"][0]["id"], "peds")
        self.assertEqual(saved["templates"][1]["id"], "gta3")
        self.assertFalse((self.data / "img_templates" / "PEDS.img").exists())

    def test_catalog_refresh_retries_a_temporary_timeout_automatically(self):
        online_path = self.data / "online_catalog.json"
        payload = {"version": "retry-ok", "mods": []}
        response = MagicMock()
        response.read.return_value = b"{}"
        response.__enter__.return_value = response
        with patch.object(core, "ONLINE_CATALOG_PATH", online_path), \
             patch.object(core.urllib.request, "urlopen", side_effect=[TimeoutError("timed out"), response]) as urlopen, \
             patch.object(core, "verify_catalog_manifest_signature", return_value=payload), \
             patch.object(core.time, "sleep") as sleep:
            result = core.update_online_catalog("https://mods.example.com/api/catalog.php", "key")
        self.assertEqual(result["version"], "retry-ok")
        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once()

    def test_catalog_refresh_downloads_cover_but_not_mod_payload(self):
        online_path = self.data / "online_catalog.json"
        cover = b"\x89PNG\r\n\x1a\npreview"
        payload_file = b"large mod payload"
        signed_payload = {
            "version": "4",
            "mods": [{
                "id": "skin_preview", "title": "Skin preview", "category": "Скіни",
                "description": "site", "destination": "game/bin/models", "cover": "cover.png",
                "files": [{
                    "path": "cover.png", "url": "https://mods.example.com/files/cover.png",
                    "size": len(cover), "sha256": hashlib.sha256(cover).hexdigest(),
                }, {
                    "path": "payload/skin.dff", "url": "https://mods.example.com/files/skin.dff",
                    "size": len(payload_file), "sha256": hashlib.sha256(payload_file).hexdigest(),
                }],
            }],
        }
        manifest_response = MagicMock()
        manifest_response.read.return_value = b"{}"
        manifest_response.__enter__.return_value = manifest_response
        cover_response = MagicMock()
        cover_response.read.side_effect = [cover, b""]
        cover_response.__enter__.return_value = cover_response
        with patch.object(core, "DATA_DIR", self.data), \
             patch.object(core, "ONLINE_CATALOG_PATH", online_path), \
             patch.object(core.urllib.request, "urlopen", side_effect=[manifest_response, cover_response]) as urlopen, \
             patch.object(core, "verify_catalog_manifest_signature", return_value=signed_payload):
            result = core.update_online_catalog("https://mods.example.com/api/catalog.php", "key")
        self.assertEqual(result["downloaded_files"], 0)
        self.assertEqual(result["downloaded_previews"], 1)
        self.assertEqual(urlopen.call_count, 2)
        self.assertEqual((self.data / "mods" / "skin_preview" / "cover.png").read_bytes(), cover)
        self.assertFalse((self.data / "mods" / "skin_preview" / "payload" / "skin.dff").exists())

    def test_remote_mod_downloads_only_when_installing(self):
        online_path = self.data / "online_catalog.json"
        content = b"downloaded on click"
        digest = hashlib.sha256(content).hexdigest()
        online_path.parent.mkdir(parents=True, exist_ok=True)
        online_path.write_text(json.dumps({
            "version": "3", "metadata_only": True,
            "mods": [{
                "id": "remote_sky", "title": "Remote sky", "category": "Небо",
                "description": "site", "destination": "game/bin/data",
                "file_index": [{
                    "path": "payload/timecyc.dat",
                    "url": "https://mods.example.com/files/timecyc.dat",
                    "size": len(content), "sha256": digest,
                }],
            }],
        }), encoding="utf-8")
        response = MagicMock()
        response.read.side_effect = [content, b""]
        response.__enter__.return_value = response
        mod = core.Mod("remote_sky", "Remote sky", "Небо", "site", "game/bin/data")
        with patch.object(core, "DATA_DIR", self.data), \
             patch.object(core, "ONLINE_CATALOG_PATH", online_path), \
             patch.object(core, "STATE_PATH", self.data / "installed.json"), \
             patch.object(core, "BACKUP_DIR", self.data / "backups"), \
             patch.object(core, "APP_DIR", Path(self.temp.name) / "app"), \
             patch.object(core.urllib.request, "urlopen", return_value=response) as urlopen:
            self.assertTrue(core.mod_payload_available(mod.id))
            self.assertFalse(core.payload_files(mod.id))
            core.install_mod(mod, self.root)
        self.assertEqual(urlopen.call_count, 1)
        self.assertEqual((self.root / "game/bin/data/timecyc.dat").read_bytes(), content)

    def test_blocks_collision_with_another_mod(self):
        (self.payload / "same.dat").write_text("first", encoding="utf-8")
        second_payload = Path(self.temp.name) / "payload2"
        second_payload.mkdir()
        (second_payload / "same.dat").write_text("second", encoding="utf-8")
        second_mod = core.Mod("sky2", "Sky 2", "Графика", "test", "game/bin/data")

        def select_payload(mod_id):
            return self.payload if mod_id == "sky" else second_payload

        with patch.object(core, "DATA_DIR", self.data), \
             patch.object(core, "STATE_PATH", self.data / "installed.json"), \
             patch.object(core, "BACKUP_DIR", self.data / "backups"), \
             patch.object(core, "payload_dir", side_effect=select_payload):
            core.install_mod(self.mod, self.root)
            with self.assertRaises(core.ModError):
                core.install_mod(second_mod, self.root)

    def test_add_and_delete_custom_mod(self):
        source = Path(self.temp.name) / "weapon effects"
        source.mkdir()
        (source / "effects.fxp").write_text("effect", encoding="utf-8")
        (source / "preview.mp4").write_text("video", encoding="utf-8")
        with patch.object(core, "DATA_DIR", self.data), \
             patch.object(core, "STATE_PATH", self.data / "installed.json"):
            mod = core.add_custom_mod(
                title="Мои эффекты",
                category="Бой",
                destination="game/bin/models",
                source_folder=source,
            )
            self.assertTrue(mod.user_defined)
            self.assertEqual(len(core.payload_files(mod.id)), 1)
            self.assertFalse((core.payload_dir(mod.id) / "preview.mp4").exists())
            self.assertIn(mod.id, {item.id for item in core.load_catalog()[1]})
            core.delete_custom_mod(mod.id)
            self.assertNotIn(mod.id, {item.id for item in core.load_catalog()[1]})

    def test_rejects_destination_outside_game(self):
        with self.assertRaises(core.ModError):
            core.validate_destination("../Windows")

    def test_resource_guard_restores_changed_file_by_hash(self):
        resources = self.root / "game" / "mods" / "deathmatch" / "resources"
        resources.mkdir(parents=True)
        guarded_mod = core.Mod("hud", "HUD", "HUD", "test", "game/mods/deathmatch/resources")
        (self.payload / "hud.dat").write_text("expected", encoding="utf-8")
        with patch.object(core, "DATA_DIR", self.data), \
             patch.object(core, "STATE_PATH", self.data / "installed.json"), \
             patch.object(core, "BACKUP_DIR", self.data / "backups"), \
             patch.object(core, "payload_dir", return_value=self.payload):
            core.install_mod(guarded_mod, self.root)
            target = resources / "hud.dat"
            target.write_text("server!!", encoding="utf-8")  # same byte length
            result = core.sync_managed_resources(self.root, {})
            self.assertEqual(result["repaired"], 1)
            self.assertEqual(target.read_text(encoding="utf-8"), "expected")
            stored = core.load_state()["installed"]["hud"]["files"][0]
            self.assertEqual(stored["managed_sha256"], core.file_sha256(self.payload / "hud.dat"))

    def test_resource_guard_keeps_working_while_game_is_running(self):
        resources = self.root / "game" / "mods" / "deathmatch" / "resources"
        resources.mkdir(parents=True)
        guarded_mod = core.Mod(
            "hud", "HUD", "HUD", "test", "game/mods/deathmatch/resources", hash_guard=True
        )
        (self.payload / "hud.dat").write_text("managed", encoding="utf-8")
        with patch.object(core, "DATA_DIR", self.data), \
             patch.object(core, "STATE_PATH", self.data / "installed.json"), \
             patch.object(core, "BACKUP_DIR", self.data / "backups"), \
             patch.object(core, "payload_dir", return_value=self.payload):
            core.install_mod(guarded_mod, self.root)
            target = resources / "hud.dat"
            target.write_text("server!", encoding="utf-8")
            with patch.object(core, "running_game_processes", return_value=["gta_sa.exe"]):
                result = core.sync_managed_resources(self.root, {})
            self.assertEqual(result["repaired"], 1)
            self.assertEqual(target.read_text(encoding="utf-8"), "managed")

    def test_resource_guard_skips_sections_without_hash_guard(self):
        resources = self.root / "game" / "mods" / "deathmatch" / "resources"
        resources.mkdir(parents=True)
        unguarded_mod = core.Mod("sky", "Sky", "Небо", "test", "game/mods/deathmatch/resources")
        (self.payload / "sky.dat").write_text("expected", encoding="utf-8")
        with patch.object(core, "DATA_DIR", self.data), \
             patch.object(core, "STATE_PATH", self.data / "installed.json"), \
             patch.object(core, "BACKUP_DIR", self.data / "backups"), \
             patch.object(core, "payload_dir", return_value=self.payload):
            core.install_mod(unguarded_mod, self.root)
            target = resources / "sky.dat"
            target.write_text("changed!", encoding="utf-8")
            result = core.sync_managed_resources(self.root, {})
            self.assertEqual(result["checked"], 0)
            self.assertEqual(target.read_text(encoding="utf-8"), "changed!")

    def test_prelaunch_verification_repairs_every_installed_category(self):
        (self.payload / "timecyc.dat").write_text("managed", encoding="utf-8")
        target = self.root / "game" / "bin" / "data" / "timecyc.dat"
        target.write_text("original", encoding="utf-8")
        with patch.object(core, "DATA_DIR", self.data), \
             patch.object(core, "STATE_PATH", self.data / "installed.json"), \
             patch.object(core, "BACKUP_DIR", self.data / "backups"), \
             patch.object(core, "payload_dir", return_value=self.payload):
            core.install_mod(self.mod, self.root)
            target.write_text("server!!", encoding="utf-8")
            result = core.verify_installed_files(self.root, [self.mod])
            self.assertEqual(result["checked"], 1)
            self.assertEqual(result["repaired"], 1)
            self.assertEqual(target.read_text(encoding="utf-8"), "managed")
            item = core.load_state()["installed"][self.mod.id]["files"][0]
            self.assertEqual(item["managed_sha256"], core.file_sha256(self.payload / "timecyc.dat"))

    def test_generates_reversible_optimization_payloads(self):
        bin_root = self.root / "game" / "bin"
        (bin_root / "data" / "plants.dat").write_text(
            "; comment\nGRASS 1 2 3\n", encoding="utf-8"
        )
        (bin_root / "data" / "gta_low.dat").write_text(
            "IMG DATA\\MAPS\\update_low.IMG\n", encoding="utf-8"
        )
        (bin_root / "stream.ini").write_text(
            "vehicles 12\npe_bRadiosity 1\n", encoding="utf-8"
        )
        with patch.object(core, "DATA_DIR", self.data):
            generated = core.ensure_optimization_payloads(self.root)
            self.assertEqual(set(generated), core.BUILTIN_OPTIMIZATION_IDS)
            grass = (self.data / "mods/optimization_no_grass/payload/plants.dat").read_text(encoding="utf-8")
            effects = (self.data / "mods/optimization_low_effects/payload/stream.ini").read_text(encoding="utf-8")
            low_map = (self.data / "mods/optimization_low_map/payload/gta.dat").read_text(encoding="utf-8")
            self.assertIn("; UG MOD HUB: GRASS", grass)
            self.assertIn("vehicles\t4", effects)
            self.assertIn("pe_bRadiosity   0", effects)
            self.assertIn("update_low.IMG", low_map)

    def test_quick_connect_config_and_server_list(self):
        config = self.root / "game" / "mta" / "config"
        config.mkdir(parents=True)
        coreconfig = config / "coreconfig.xml"
        coreconfig.write_text(
            "<mainconfig><settings><host>old.host</host><port>22003</port></settings></mainconfig>",
            encoding="utf-8",
        )
        (config / "servercache.xml").write_text(
            '<root><server ip="10.0.0.2" port="22004" strName="Server [ #02 ]" />'
            '<server ip="10.0.0.1" port="22003" strName="Server [ #01 ]" /></root>',
            encoding="utf-8",
        )
        servers = core.load_mta_servers(self.root)
        self.assertEqual([item["host"] for item in servers], ["10.0.0.1", "10.0.0.2"])
        core.configure_quick_connect(self.root, "s5.example.com", 22005)
        updated = coreconfig.read_text(encoding="utf-8")
        self.assertIn("<host>s5.example.com</host>", updated)
        self.assertIn("<port>22005</port>", updated)

    def test_locked_release_ignores_dev_custom_catalog(self):
        app_dir = Path(self.temp.name) / "release"
        data_dir = Path(self.temp.name) / "release-data"
        app_dir.mkdir()
        data_dir.mkdir()
        catalog_path = app_dir / "catalog.json"
        config_path = app_dir / "build_config.json"
        catalog_path.write_text(json.dumps({
            "app_name": "UG MOD HUB",
            "version": "1.2",
            "mods": [{
                "id": "built_in",
                "title": "Built in",
                "category": "HUD",
                "description": "test",
                "destination": "game/mods/deathmatch/resources",
            }],
        }), encoding="utf-8")
        config_path.write_text('{"dev_mode": false}', encoding="utf-8")
        (data_dir / "custom_mods.json").write_text(json.dumps({
            "mods": [{
                "id": "dev_only",
                "title": "DEV only",
                "category": "HUD",
                "description": "test",
                "destination": "game/mods/deathmatch/resources",
            }],
        }), encoding="utf-8")

        with patch.object(core, "APP_DIR", app_dir), \
             patch.object(core, "DATA_DIR", data_dir), \
             patch.object(core, "ONLINE_CATALOG_PATH", data_dir / "online_catalog.json"), \
             patch.object(core, "CATALOG_PATH", catalog_path), \
             patch.object(core, "BUILD_CONFIG_PATH", config_path):
            self.assertFalse(core.is_dev_mode())
            self.assertEqual([mod.id for mod in core.load_catalog()[1]], ["built_in"])

    def test_frozen_app_uses_embedded_version_when_external_catalog_is_stale(self):
        app_dir = Path(self.temp.name) / "old-install"
        app_dir.mkdir()
        catalog_path = app_dir / "catalog.json"
        config_path = app_dir / "build_config.json"
        catalog_path.write_text(json.dumps({
            "app_name": "UG MOD HUB", "version": "3.4", "mods": [],
        }), encoding="utf-8")
        config_path.write_text('{"dev_mode": false}', encoding="utf-8")

        with patch.object(core, "APP_DIR", app_dir), \
             patch.object(core, "CATALOG_PATH", catalog_path), \
             patch.object(core, "BUILD_CONFIG_PATH", config_path), \
             patch.object(core.sys, "frozen", True, create=True), \
             patch.object(core, "EMBEDDED_APP_VERSION", "3.9"):
            catalog, _mods = core.load_catalog()

        self.assertEqual(catalog["version"], "3.9")

    def test_dev_can_edit_built_in_mod_and_replace_payload(self):
        app_dir = Path(self.temp.name) / "dev-app"
        data_dir = Path(self.temp.name) / "dev-data"
        packaged_payload = app_dir / "mods" / "effects" / "payload"
        packaged_payload.mkdir(parents=True)
        (packaged_payload / "old.fxp").write_text("old", encoding="utf-8")
        data_dir.mkdir()
        catalog_path = app_dir / "catalog.json"
        config_path = app_dir / "build_config.json"
        catalog_path.write_text(json.dumps({
            "app_name": "UG MOD HUB",
            "version": "1.3",
            "mods": [{
                "id": "effects",
                "title": "Effects",
                "category": "Ефекти",
                "description": "old",
                "destination": "game/bin/models",
            }],
        }), encoding="utf-8")
        config_path.write_text('{"dev_mode": true}', encoding="utf-8")
        replacement = Path(self.temp.name) / "replacement"
        replacement.mkdir()
        (replacement / "new.fxp").write_text("new", encoding="utf-8")

        with patch.object(core, "APP_DIR", app_dir), \
             patch.object(core, "DATA_DIR", data_dir), \
             patch.object(core, "CATALOG_PATH", catalog_path), \
             patch.object(core, "BUILD_CONFIG_PATH", config_path):
            editable = core.ensure_editable_payload("effects")
            self.assertEqual((editable / "old.fxp").read_text(encoding="utf-8"), "old")
            updated = core.update_dev_mod(
                "effects",
                title="Effects edited",
                category="Ефекти",
                destination="game/bin/models",
                description="new",
                source_folder=replacement,
            )
            self.assertEqual(updated.title, "Effects edited")
            self.assertFalse((core.payload_dir("effects") / "old.fxp").exists())
            self.assertEqual((core.payload_dir("effects") / "new.fxp").read_text(encoding="utf-8"), "new")
            self.assertEqual(core.load_catalog()[1][0].title, "Effects edited")

    def test_release_keeps_cached_payload_for_installed_mod_removed_from_catalog(self):
        app_dir = Path(self.temp.name) / "release-app"
        data_dir = Path(self.temp.name) / "release-data"
        cached_payload = data_dir / "mods" / "old_sky_id" / "payload"
        packaged_payload = app_dir / "mods" / "old_sky_id" / "payload"
        cached_payload.mkdir(parents=True)
        packaged_payload.mkdir(parents=True)
        (cached_payload / "timecyc.dat").write_text("cached reference", encoding="utf-8")
        (packaged_payload / "timecyc.dat").write_text("stale package", encoding="utf-8")
        (app_dir / "build_config.json").write_text('{"dev_mode": false}', encoding="utf-8")
        (data_dir / "online_catalog.json").write_text(
            json.dumps({"mods": [{"id": "new_sky_id"}]}), encoding="utf-8"
        )

        with patch.object(core, "APP_DIR", app_dir), \
             patch.object(core, "DATA_DIR", data_dir), \
             patch.object(core, "BUILD_CONFIG_PATH", app_dir / "build_config.json"):
            selected = core.payload_dir("old_sky_id")

        self.assertEqual(selected, cached_payload)
        self.assertEqual((selected / "timecyc.dat").read_text(encoding="utf-8"), "cached reference")

    def test_installed_entries_include_mod_removed_from_online_catalog(self):
        self.data.mkdir(parents=True)
        state_path = self.data / "installed.json"
        state_path.write_text(json.dumps({"installed": {
            "old_sky": {
                "title": "Небо №1",
                "destination": str(self.root / "game/bin/data"),
                "backup": str(self.data / "backups/old_sky"),
                "files": [{"relative": "timecyc.dat", "existed": True}],
            },
        }}), encoding="utf-8")

        with patch.object(core, "STATE_PATH", state_path):
            entries = core.installed_mod_entries([])

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].id, "old_sky")
        self.assertEqual(entries[0].title, "Небо №1")
        self.assertEqual(entries[0].category, "Інше")

    @unittest.skipUnless(os.name == "nt", "Windows mutex")
    def test_single_instance_detects_existing_mutex(self):
        core.release_single_instance()
        handle = ctypes.windll.kernel32.CreateMutexW(
            None, False, "Local\\MTA_MOD_HUB_SINGLE_INSTANCE"
        )
        try:
            self.assertFalse(core.acquire_single_instance())
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
            core.release_single_instance()

    def test_autostart_uses_elevated_logon_task(self):
        completed = SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        with patch.object(core.subprocess, "run", return_value=completed) as run, \
             patch.object(core.sys, "frozen", True, create=True), \
             patch.object(core.sys, "executable", r"D:\\Apps\\UG MOD HUB.exe"):
            core.set_autostart(True)
        command = run.call_args.args[0]
        self.assertIn("/SC", command)
        self.assertIn("ONLOGON", command)
        self.assertIn("/RL", command)
        self.assertIn("HIGHEST", command)
        self.assertIn('"D:\\Apps\\UG MOD HUB.exe"', command)

    def test_custom_cover_and_favorites_are_persisted(self):
        source = Path(self.temp.name) / "mod"
        source.mkdir()
        (source / "effect.dat").write_text("data", encoding="utf-8")
        cover = Path(self.temp.name) / "preview.png"
        cover.write_bytes(b"png-placeholder")
        with patch.object(core, "DATA_DIR", self.data), \
             patch.object(core, "STATE_PATH", self.data / "installed.json"):
            mod = core.add_custom_mod(
                "Preview", "Інше", "game/bin/data", source, cover_file=cover
            )
            self.assertEqual(core.cover_path(mod), self.data / "mods" / mod.id / "cover.png")
            core.set_favorite(mod.id, True)
            self.assertIn(mod.id, core.load_favorites())
            core.set_favorite(mod.id, False)
            self.assertNotIn(mod.id, core.load_favorites())

    def test_cancelled_install_restores_original_file(self):
        target = self.root / "game" / "bin" / "data" / "large.dat"
        target.write_bytes(b"original")
        (self.payload / "large.dat").write_bytes(b"x" * (2 * 1024 * 1024))
        cancel = threading.Event()

        def progress(*_args):
            cancel.set()

        with patch.object(core, "DATA_DIR", self.data), \
             patch.object(core, "STATE_PATH", self.data / "installed.json"), \
             patch.object(core, "BACKUP_DIR", self.data / "backups"), \
             patch.object(core, "payload_dir", return_value=self.payload):
            with self.assertRaises(core.OperationCancelled):
                core.install_mod(self.mod, self.root, progress, cancel)
            self.assertEqual(target.read_bytes(), b"original")
            self.assertNotIn(self.mod.id, core.load_state().get("installed", {}))

    def test_restore_clean_game_uninstalls_all_mods(self):
        (self.payload / "sky.dat").write_text("modded", encoding="utf-8")
        target = self.root / "game" / "bin" / "data" / "sky.dat"
        target.write_text("original", encoding="utf-8")
        with patch.object(core, "DATA_DIR", self.data), \
             patch.object(core, "STATE_PATH", self.data / "installed.json"), \
             patch.object(core, "BACKUP_DIR", self.data / "backups"), \
             patch.object(core, "payload_dir", return_value=self.payload):
            core.install_mod(self.mod, self.root)
            restored = core.restore_clean_game(self.root, [self.mod])
            self.assertEqual(restored, 1)
            self.assertEqual(target.read_text(encoding="utf-8"), "original")
            self.assertFalse(core.load_state().get("installed"))

    def test_preflight_detects_selected_mod_conflict(self):
        first = core.Mod("one", "One", "HUD", "", "game/bin/data")
        second = core.Mod("two", "Two", "HUD", "", "game/bin/data")
        payload_one = Path(self.temp.name) / "one"
        payload_two = Path(self.temp.name) / "two"
        payload_one.mkdir()
        payload_two.mkdir()
        (payload_one / "same.dat").write_text("1", encoding="utf-8")
        (payload_two / "same.dat").write_text("2", encoding="utf-8")

        def choose(mod_id):
            return payload_one if mod_id == "one" else payload_two

        with patch.object(core, "payload_dir", side_effect=choose):
            issues = core.preflight_check(self.root, [first, second])
        self.assertTrue(any(item["title"] == "Конфлікти модів" for item in issues))

    def test_installed_file_conflicts_finds_legacy_mod_by_target_path(self):
        old_payload = Path(self.temp.name) / "old-payload"
        new_payload = Path(self.temp.name) / "new-payload"
        old_payload.mkdir()
        new_payload.mkdir()
        (old_payload / "timecyc.dat").write_text("old", encoding="utf-8")
        (new_payload / "timecyc.dat").write_text("new", encoding="utf-8")
        old_mod = core.Mod("old_sky", "Небо №1", "Небо", "", "game/bin/data")
        new_mod = core.Mod("new_sky", "Небо 1", "Небо", "", "game/bin/data")

        def choose(mod_id):
            return old_payload if mod_id == old_mod.id else new_payload

        with patch.object(core, "DATA_DIR", self.data), \
             patch.object(core, "STATE_PATH", self.data / "installed.json"), \
             patch.object(core, "BACKUP_DIR", self.data / "backups"), \
             patch.object(core, "payload_dir", side_effect=choose):
            core.install_mod(old_mod, self.root)
            conflicts = core.installed_file_conflicts(new_mod, self.root)

        self.assertEqual(conflicts[0]["id"], "old_sky")
        self.assertEqual(conflicts[0]["files"], ["timecyc.dat"])

    def test_application_updater_resets_pyinstaller_runtime(self):
        source = Path(self.temp.name) / "UG-MOD-HUB-5.2.exe"
        current = Path(self.temp.name) / "UG MOD HUB.exe"
        source.write_bytes(b"update")
        popen = MagicMock()

        with patch.object(core, "DATA_DIR", self.data), \
             patch.object(core.os, "name", "nt"), \
             patch.object(core.sys, "frozen", True, create=True), \
             patch.object(core.sys, "executable", str(current)), \
             patch.object(core.subprocess, "Popen", popen):
            core.launch_application_updater(source)

        script = (self.data / "apply-update.cmd").read_text(encoding="mbcs")
        self.assertIn('set "PYINSTALLER_RESET_ENVIRONMENT=1"', script)
        self.assertEqual(
            popen.call_args.kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"],
            "1",
        )


if __name__ == "__main__":
    unittest.main()
