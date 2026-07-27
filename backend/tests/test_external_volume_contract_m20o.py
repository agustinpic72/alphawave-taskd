import importlib.util
import json
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[2] / "scripts" / "admin" / "runtime_migration.py"
SPEC = importlib.util.spec_from_file_location("runtime_migration_m20o", SCRIPT)
runtime_migration = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = runtime_migration
SPEC.loader.exec_module(runtime_migration)


@pytest.mark.parametrize("name", ["alphawave_data", "AlphaWave-1.2", "a_b-c.d"])
def test_volume_names_and_local_driver_contract(name):
    spec = runtime_migration.VolumeSpec(name)

    assert spec.expected_options == {}
    assert spec.docker_create_args() == ["volume", "create", "--driver", "local", name]


@pytest.mark.parametrize("name", ["", "a", "_data", "bad/name", "bad name", "../data"])
def test_invalid_volume_names_fail_closed(name):
    with pytest.raises(runtime_migration.VolumeContractError):
        runtime_migration.VolumeSpec(name)


def test_external_bind_contract_builds_exact_create_arguments(tmp_path):
    device = tmp_path / "data"
    spec = runtime_migration.VolumeSpec(
        "alphawave_data_soak_v1",
        runtime_migration.EXTERNAL_BIND_BACKED,
        device,
    )

    assert spec.expected_options == {
        "type": "none",
        "o": "bind",
        "device": str(device),
    }
    assert spec.docker_create_args() == [
        "volume",
        "create",
        "--driver",
        "local",
        "--opt",
        "type=none",
        "--opt",
        "o=bind",
        "--opt",
        f"device={device}",
        "alphawave_data_soak_v1",
    ]


def test_modes_reject_incompatible_or_missing_devices(tmp_path):
    with pytest.raises(runtime_migration.VolumeContractError):
        runtime_migration.VolumeSpec("managed_volume", device=tmp_path)
    with pytest.raises(runtime_migration.VolumeContractError):
        runtime_migration.VolumeSpec("bind_volume", runtime_migration.EXTERNAL_BIND_BACKED)
    with pytest.raises(runtime_migration.VolumeContractError):
        runtime_migration.VolumeSpec("unknown_volume", "unknown")


def test_device_must_be_absolute_and_cannot_traverse_symlinks(tmp_path):
    with pytest.raises(runtime_migration.VolumeContractError):
        runtime_migration.validate_device_path("relative/data")
    with pytest.raises(runtime_migration.VolumeContractError):
        runtime_migration.validate_device_path("/")
    with pytest.raises(runtime_migration.VolumeContractError):
        runtime_migration.validate_device_path(tmp_path / ".." / "data")

    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    with pytest.raises(runtime_migration.VolumeContractError, match="symlink"):
        runtime_migration.validate_device_path(linked / "data")


def test_prepare_and_probe_device_uses_only_target_directory(tmp_path):
    device = tmp_path / "runtime" / "data"
    prepared = runtime_migration.prepare_device_directory(device, create=True)

    assert prepared == device
    assert runtime_migration.device_is_empty(device)
    result = runtime_migration.probe_device_filesystem(device, require_empty=True)
    assert result["write"] is True
    assert result["flock"] is True
    assert result["atomic_rename"] is True
    assert result["available_bytes"] > 0
    assert runtime_migration.device_is_empty(device)


def test_prepare_rejects_symlink_even_when_creation_is_enabled(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)

    with pytest.raises(runtime_migration.VolumeContractError):
        runtime_migration.prepare_device_directory(linked / "new", create=True)
    assert not (real / "new").exists()


def test_empty_device_requirement_fails_before_writing(tmp_path):
    (tmp_path / "existing").write_text("preserve", encoding="utf-8")

    with pytest.raises(runtime_migration.VolumeContractError, match="must be empty"):
        runtime_migration.probe_device_filesystem(tmp_path, require_empty=True)
    assert (tmp_path / "existing").read_text(encoding="utf-8") == "preserve"


@pytest.mark.parametrize(
    "mode,options",
    [
        (runtime_migration.LOCAL_DRIVER_MANAGED, None),
        (runtime_migration.LOCAL_DRIVER_MANAGED, {}),
        (
            runtime_migration.EXTERNAL_BIND_BACKED,
            {"type": "none", "o": "bind", "device": "DEVICE"},
        ),
    ],
)
def test_inspect_accepts_only_exact_contract(tmp_path, mode, options):
    device = tmp_path / "data" if mode == runtime_migration.EXTERNAL_BIND_BACKED else None
    spec = runtime_migration.VolumeSpec("contract_volume", mode, device)
    if options and options.get("device") == "DEVICE":
        options["device"] = str(device)
    payload = [{"Name": spec.name, "Driver": "local", "Options": options}]

    inspected = runtime_migration.parse_volume_inspection(spec, json.dumps(payload))

    assert inspected["Name"] == spec.name


@pytest.mark.parametrize(
    "mutation",
    [
        {"Name": "other", "Driver": "local", "Options": {}},
        {"Name": "contract_volume", "Driver": "overlay", "Options": {}},
        {"Name": "contract_volume", "Driver": "local", "Options": {"o": "bind"}},
        {"Name": "contract_volume", "Driver": "local"},
        {"Name": "contract_volume", "Driver": "local", "Options": []},
    ],
)
def test_external_inspect_mismatch_fails_closed(tmp_path, mutation):
    spec = runtime_migration.VolumeSpec(
        "contract_volume", runtime_migration.EXTERNAL_BIND_BACKED, tmp_path / "data"
    )

    with pytest.raises(runtime_migration.VolumeContractError):
        runtime_migration.verify_volume_inspection(spec, [mutation])


@pytest.mark.parametrize("payload", ["", "not-json", "[]", "[{}, {}]", "null"])
def test_malformed_inspect_output_fails_closed(payload):
    spec = runtime_migration.VolumeSpec("managed_volume")

    with pytest.raises(runtime_migration.VolumeContractError):
        runtime_migration.parse_volume_inspection(spec, payload)


def test_data_and_backup_devices_must_be_distinct(tmp_path):
    device = tmp_path / "shared"
    device.mkdir()

    with pytest.raises(runtime_migration.VolumeContractError, match="distinct"):
        runtime_migration.verify_distinct_devices(device, device)

    alias = tmp_path / "alias"
    alias.symlink_to(device, target_is_directory=True)
    with pytest.raises(runtime_migration.VolumeContractError):
        runtime_migration.verify_distinct_devices(device, alias)
