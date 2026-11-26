"""Target-selected process input tests with generated non-proprietary fixtures."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from project_paths import PROJECT_ROOT
from src.a6400.targets import get_profile
from tools.research import probe_interpreter
from tools.research.probe_interpreter import kernel_symbol_crosscheck
from tools.research.process_target_inputs import (
    ILCE_7C_MANIFEST,
    load_target_manifest,
    require_guest_filesystem_manifest,
    resolve_firmware_file,
    select_a7c_images,
)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def a7c_fixture(root: Path) -> dict:
    records = {}
    fixture_inputs = (("app.elf", b"synthetic A7C application"),
                      ("ld.elf", b"synthetic A7C linker"),
                      ("vmlinux.bin", b"synthetic A7C kernel"),
                      ("init", b"synthetic A7C init"),
                      ("rootfs.img", b"synthetic A7C ext2 image"))
    if len({name for name, _ in fixture_inputs}) != len(fixture_inputs):
        raise AssertionError("synthetic target fixture contains duplicate input names")
    for name, content in fixture_inputs:
        relative = f"firmware/ilce_7c_203/{name}"
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        records[name] = {"firmware_path": relative, "sha256": sha(content)}
    return {
        "schema_version": 1,
        "target_id": "ilce_7c",
        "firmware_namespace": "firmware/ilce_7c_203/",
        "application": {"guest_path": "/usr/bin/im.elf", **records["app.elf"]},
        "interpreter_candidates": [{
            "id": "nflasha7-ld", **records["ld.elf"],
            "declared_pt_interp": "//lib/ld-linux.so.3",
            "resolution_status": "unverified_package_candidate",
        }],
        "kernel": records["vmlinux.bin"],
        "init": records["init"],
        "rootfs_image": {**records["rootfs.img"], "format": "ext2",
                         "planned_mount_target": "/rootfs"},
        "guest_filesystem_manifest": {
            "path": "artifacts/inventory/ilce_7c_203/guest-filesystem.json",
            "status": "not_supplied", "completeness": "unknown",
            "reason": "fixture intentionally omits runtime filesystem evidence",
        },
        "syscall_contracts": {"supported": ["stop"]},
        "deployment_status": "reference_only_not_verified_execution_or_installation_target",
    }


class ProcessTargetInputTests(unittest.TestCase):
    def test_a6400_stop_policy_keeps_legacy_inputs_without_a_baseline_fixture(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image = root / "firmware/a6400/root.img"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"synthetic A6400 root image")
            inventory_dir = root / "artifacts/inventory"
            inventory_dir.mkdir(parents=True)
            (inventory_dir / "guest-filesystem.json").write_text(json.dumps({"images": [{
                "path": "firmware/a6400/root.img", "sha256": sha(image.read_bytes()),
            }], "nodes": {}}), encoding="utf-8")
            (inventory_dir / "elf-inventory.json").write_text(json.dumps({"entries": []}), encoding="utf-8")
            self.assertFalse((inventory_dir / "baseline.json").exists())
            args = SimpleNamespace(
                interpreter_candidate=None, input_manifest=None, entry=None, argument=[],
                syscalls="stop", respect_runtime_mounts=False, instructions=100,
                timeout_us=1000,
            )
            application = {"interpreter": "/lib/ld.so", "entry": 0x1000}
            interpreter = {"entry": 0x2000}
            fake_probe = Mock()
            fake_probe.run.return_value = {"status": "syscall_boundary", "instructions_executed": 3}
            with patch.object(probe_interpreter, "PROJECT_ROOT", root), \
                 patch.object(probe_interpreter, "_load_guest_image",
                              side_effect=[application, interpreter]), \
                 patch.object(probe_interpreter, "InterpreterProbe", return_value=fake_probe):
                result = probe_interpreter._run_a6400(args, get_profile("ilce_6400"))
            self.assertEqual(result["target_input_selection"]["target_id"], "ilce_6400")
            self.assertEqual(result["target_input_selection"]["selection_mode"], "legacy_a6400_inventory_bundle")
            self.assertFalse(result["target_input_selection"]["baseline_used"])
            fake_probe.prepare.assert_called_once_with(application, interpreter, ["/usr/bin/im.elf"])
            fake_probe.run.assert_called_once()

    def test_a6400_load_guest_image_public_helper_keeps_its_call_signature(self):
        expected = {"entry": 0x1000}
        with patch.object(probe_interpreter, "_load_guest_image", return_value=expected) as loader:
            actual = probe_interpreter.load_guest_image({"nodes": {}}, [], "/usr/bin/im.elf")
        self.assertIs(actual, expected)
        loader.assert_called_once_with({"nodes": {}}, [], "/usr/bin/im.elf")

    def test_manifest_and_firmware_paths_cannot_escape_their_roots(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaisesRegex(ValueError, "manifest escapes"):
                load_target_manifest(root, Path("../outside-manifest.json"), "ilce_7c")
            with self.assertRaisesRegex(ValueError, "must be a nonempty relative path"):
                resolve_firmware_file(root, str(root / "outside.bin"), sha(b"outside"))
            with self.assertRaisesRegex(ValueError, "lowercase SHA-256 pin"):
                resolve_firmware_file(root, "firmware/ilce_7c_203/missing.bin", None)
            with self.assertRaisesRegex(ValueError, "escapes the target firmware directory"):
                resolve_firmware_file(root, "firmware/../outside.bin", sha(b"outside"))

            manifest = {
                "guest_filesystem_manifest": {
                    "status": "available", "path": "../outside-filesystem.json"
                }
            }
            with self.assertRaisesRegex(ValueError, "filesystem manifest escapes"):
                require_guest_filesystem_manifest(root, manifest, "ilce_7c")

    def test_a7c_selection_loads_only_declared_a7c_inputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = a7c_fixture(root)
            app, linker, kernel, init, rootfs = select_a7c_images(
                root, manifest, "/usr/bin/im.elf", "nflasha7-ld"
            )
            expected_root = (root / "firmware/ilce_7c_203").resolve()
            for item in (app, linker, kernel, init, rootfs):
                self.assertTrue(item["local_path"].is_relative_to(expected_root))
            self.assertEqual(app["sha256"], manifest["application"]["sha256"])
            self.assertEqual(kernel["sha256"], manifest["kernel"]["sha256"])
            with self.assertRaisesRegex(ValueError, "select a named interpreter candidate explicitly"):
                select_a7c_images(root, manifest, "/usr/bin/im.elf", None)

    def test_a7c_refuses_a6400_manifest_and_cross_namespace_kernel(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest_path = root / "a6400-inputs.json"
            manifest_path.write_text(json.dumps({"schema_version": 1, "target_id": "ilce_6400"}),
                                     encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "manifest mismatch"):
                load_target_manifest(root, Path("a6400-inputs.json"), "ilce_7c")

            a7c = a7c_fixture(root)
            a7c["kernel"]["firmware_path"] = "firmware/extracted_a6400/vmlinux.bin"
            with self.assertRaisesRegex(ValueError, "crosses target namespace"):
                select_a7c_images(root, a7c, "/usr/bin/im.elf", "nflasha7-ld")

            a7c = a7c_fixture(root)
            escaped_kernel = root / "firmware/other/vmlinux.bin"
            escaped_kernel.parent.mkdir(parents=True, exist_ok=True)
            escaped_kernel.write_bytes((root / a7c["kernel"]["firmware_path"]).read_bytes())
            a7c["kernel"]["firmware_path"] = "firmware/ilce_7c_203/../other/vmlinux.bin"
            with self.assertRaisesRegex(ValueError, "escapes target namespace"):
                select_a7c_images(root, a7c, "/usr/bin/im.elf", "nflasha7-ld")

            pinned = json.loads((PROJECT_ROOT / ILCE_7C_MANIFEST).read_text(encoding="utf-8"))
            pinned_path = root / "pinned-a7c.json"
            pinned["firmware_namespace"] = "firmware/"
            pinned_path.write_text(json.dumps(pinned), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "namespace does not match"):
                load_target_manifest(root, Path("pinned-a7c.json"), "ilce_7c")

            pinned["firmware_namespace"] = "firmware/ilce_7c_203/"
            pinned["kernel"]["firmware_path"] = "firmware/ilce_7c_203/nflasha3/other-kernel.bin"
            pinned_path.write_text(json.dumps(pinned), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "input paths do not match"):
                load_target_manifest(root, Path("pinned-a7c.json"), "ilce_7c")

            pinned = json.loads((PROJECT_ROOT / ILCE_7C_MANIFEST).read_text(encoding="utf-8"))
            pinned["interpreter_candidates"][0]["resolution_status"] = "verified"
            pinned_path.write_text(json.dumps(pinned), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unresolved static-package status"):
                load_target_manifest(root, Path("pinned-a7c.json"), "ilce_7c")

            pinned = json.loads((PROJECT_ROOT / ILCE_7C_MANIFEST).read_text(encoding="utf-8"))
            pinned["syscall_contracts"]["supported"].append("brk")
            pinned_path.write_text(json.dumps(pinned), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "limited to the verified stop policy"):
                load_target_manifest(root, Path("pinned-a7c.json"), "ilce_7c")

            pinned = json.loads((PROJECT_ROOT / ILCE_7C_MANIFEST).read_text(encoding="utf-8"))
            pinned["application"]["guest_path_evidence"]["runtime_mount_success_verified"] = True
            pinned_path.write_text(json.dumps(pinned), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "must remain static and runtime-unverified"):
                load_target_manifest(root, Path("pinned-a7c.json"), "ilce_7c")

    def test_each_a7c_process_and_kernel_input_is_hash_checked(self):
        for selected_name in ("app.elf", "ld.elf", "vmlinux.bin", "init", "rootfs.img"):
            with self.subTest(input=selected_name), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                manifest = a7c_fixture(root)
                (root / manifest["application"]["firmware_path"]).write_bytes(b"mutated content")
                # Restore the application for non-application cases, then mutate the selected entry.
                app_path = root / manifest["application"]["firmware_path"]
                if selected_name != "app.elf":
                    app_path.write_bytes(b"synthetic A7C application")
                record = {
                    "app.elf": manifest["application"],
                    "ld.elf": manifest["interpreter_candidates"][0],
                    "vmlinux.bin": manifest["kernel"],
                    "init": manifest["init"],
                    "rootfs.img": manifest["rootfs_image"],
                }[selected_name]
                (root / record["firmware_path"]).write_bytes(b"mutated selected input")
                with self.assertRaisesRegex(ValueError, "hash mismatch"):
                    select_a7c_images(root, manifest, "/usr/bin/im.elf", "nflasha7-ld")

    def test_hash_mismatch_fails_before_probe_construction(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = json.loads((PROJECT_ROOT / ILCE_7C_MANIFEST).read_text(encoding="utf-8"))
            manifest_path = root / ILCE_7C_MANIFEST
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            wrong_app = root / manifest["application"]["firmware_path"]
            wrong_app.parent.mkdir(parents=True, exist_ok=True)
            wrong_app.write_bytes(b"not the hash-pinned A7C im.elf")
            args = SimpleNamespace(
                input_manifest=ILCE_7C_MANIFEST, entry=None,
                interpreter_candidate="nflasha7-ld-2.11.2",
                syscalls="stop", respect_runtime_mounts=False, argument=[], instructions=10, timeout_us=1000,
            )
            with patch.object(probe_interpreter, "PROJECT_ROOT", root), \
                 patch.object(probe_interpreter, "InterpreterProbe") as probe:
                with self.assertRaisesRegex(ValueError, "hash mismatch"):
                    probe_interpreter._run_a7c(args, get_profile("ilce_7c"))
                probe.assert_not_called()

    def test_missing_runtime_contract_remains_unknown_and_blocks_filesystem_policy(self):
        with tempfile.TemporaryDirectory() as temp:
            manifest = a7c_fixture(Path(temp))
            self.assertEqual(manifest["guest_filesystem_manifest"]["completeness"], "unknown")
            with self.assertRaisesRegex(ValueError, "guest filesystem contract is not_supplied"):
                require_guest_filesystem_manifest(Path(temp), manifest, "ilce_7c")

    def test_a7c_stays_reference_only_and_does_not_claim_installation(self):
        manifest = json.loads((PROJECT_ROOT / ILCE_7C_MANIFEST).read_text(encoding="utf-8"))
        profile = get_profile("ilce_7c")
        self.assertEqual(profile.status, "reference")
        self.assertEqual(manifest["deployment_status"],
                         "reference_only_not_verified_execution_or_installation_target")
        self.assertEqual(manifest["syscall_contracts"]["supported"], ["stop"])

    @unittest.skipUnless(
        (PROJECT_ROOT / "firmware/ilce_7c_203/nflasha15/bin/im.elf").is_file(),
        "local A7C firmware inputs are not staged; test remains binary-free in clean clones",
    )
    def test_hash_pinned_a7c_linker_prefix_stops_at_the_same_brk_boundary(self):
        args = SimpleNamespace(
            input_manifest=None, entry=None, interpreter_candidate="nflasha7-ld-2.11.2",
            syscalls="stop", respect_runtime_mounts=False, argument=["a"],
            instructions=5000, timeout_us=1_000_000,
        )
        report = probe_interpreter._run_a7c(args, get_profile("ilce_7c"))
        self.assertEqual(report["status"], "syscall_boundary")
        self.assertEqual(report["instructions_executed"], 1183)
        self.assertEqual(report["boundary"]["r7"], 45)  # brk syscall number
        self.assertEqual(report["boundary"]["instruction_address"], 0x40010B14)
        self.assertEqual(report["syscall_policy"], "stop")
        self.assertEqual(report["syscall_responses_supplied"], 0)
        self.assertEqual(report["target_input_selection"]["target_profile_status"], "reference")
        app_mounts = report["target_input_selection"]["runtime_mount_evidence"][
            "application_mount_candidates"]
        self.assertTrue(app_mounts)
        self.assertTrue(all(row["target"] == "/usr" for row in app_mounts))
        self.assertTrue(any(row["source"] == "/dev/nflasha15" for row in app_mounts))
        self.assertFalse(report["target_input_selection"]["runtime_mount_evidence"][
            "application_mount_success_verified"])
        mapping = report["target_input_selection"]["pt_interp_package_mapping"]
        self.assertEqual(mapping["status"], "package_static_mapping_verified")
        self.assertEqual(mapping["symlink_target"], "ld-2.11.2.so")
        self.assertFalse(mapping["runtime_mount_or_pivot_success_verified"])
        self.assertFalse(report["target_input_selection"]["runtime_mount_evidence"][
            "runtime_mount_success_or_boot_mode_verified"])
        mounts = report["target_input_selection"]["runtime_mount_evidence"]["static_mounts"]
        sequence = report["target_input_selection"]["runtime_mount_evidence"]["init_main_mount_sequence"]
        self.assertGreaterEqual(len(mounts), len(app_mounts))
        self.assertGreaterEqual(len(sequence), len(report["target_input_selection"][
            "runtime_mount_evidence"]["root_transition_instructions"]))

    def test_missing_kernel_symbol_is_not_a_crosscheck_match(self):
        missing = kernel_symbol_crosscheck({}, {}, ["usb_gadgetcore_ioctl"])
        self.assertFalse(missing["usb_gadgetcore_ioctl"]["match"])
        matched = kernel_symbol_crosscheck(
            {"usb_gadgetcore_ioctl": 0x10}, {"usb_gadgetcore_ioctl": 0x10},
            ["usb_gadgetcore_ioctl"],
        )
        self.assertTrue(matched["usb_gadgetcore_ioctl"]["match"])

    def test_other_registered_bodies_do_not_reuse_a6400_or_a7c_inputs(self):
        for target_id in ("zv_e10", "ilce_6400a", "ilce_6100", "ilce_7m3", "ilce_7rm4a"):
            with self.subTest(target=target_id):
                profile = get_profile(target_id)
                args = SimpleNamespace(target=target_id)
                with patch.object(probe_interpreter, "InterpreterProbe") as probe:
                    with self.assertRaisesRegex(ValueError, "not reused"):
                        probe_interpreter._run_selected_target(args, profile)
                    probe.assert_not_called()
        with self.assertRaisesRegex(ValueError, "not reused"):
            probe_interpreter._run_a6400(
                SimpleNamespace(target="ilce_6400a"), get_profile("ilce_6400a")
            )
        with self.assertRaisesRegex(ValueError, "pinned to target ilce_7c"):
            probe_interpreter._run_a7c(
                SimpleNamespace(target="zv_e10", syscalls="stop"), get_profile("zv_e10")
            )

    def test_post_run_bookkeeping_failure_keeps_the_probe_receipt(self):
        class Boom(dict):
            def __setitem__(self, key, value):
                if key == "target_input_selection":
                    raise ValueError("bookkeeping failed")
                super().__setitem__(key, value)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image = root / "firmware/a6400/root.img"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"synthetic A6400 root image")
            inventory_dir = root / "artifacts/inventory"
            inventory_dir.mkdir(parents=True)
            (inventory_dir / "guest-filesystem.json").write_text(json.dumps({"images": [{
                "path": "firmware/a6400/root.img", "sha256": sha(image.read_bytes()),
            }], "nodes": {}}), encoding="utf-8")
            (inventory_dir / "elf-inventory.json").write_text(json.dumps({"entries": []}), encoding="utf-8")
            args = SimpleNamespace(
                interpreter_candidate=None, input_manifest=None, entry=None, argument=[],
                syscalls="stop", respect_runtime_mounts=False, instructions=100,
                timeout_us=1000,
            )
            receipt = Boom(status="syscall_boundary", instructions_executed=3, boundary={"r7": 45})
            fake_probe = Mock()
            fake_probe.run.return_value = receipt
            with patch.object(probe_interpreter, "PROJECT_ROOT", root), \
                 patch.object(probe_interpreter, "_load_guest_image",
                              side_effect=[{"interpreter": "/lib/ld.so", "entry": 0x1000},
                                           {"entry": 0x2000}]), \
                 patch.object(probe_interpreter, "InterpreterProbe", return_value=fake_probe):
                result = probe_interpreter._run_a6400(args, get_profile("ilce_6400"))
        self.assertIs(result, receipt)
        self.assertEqual(result["probe_status"], "syscall_boundary")
        self.assertEqual(result["instructions_executed"], 3)
        self.assertEqual(result["boundary"]["r7"], 45)
        self.assertEqual(result["status"], "configuration_error")
        self.assertIn("bookkeeping failed", result["error"])
        self.assertEqual(result["guest_paths"]["interpreter"], "/lib/ld.so")


if __name__ == "__main__":
    unittest.main()
