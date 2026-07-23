"""Headless tests for installed-app paths and Windows release definitions."""
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "overlay")]

import app_runtime  # noqa: E402
from tools import join_party  # noqa: E402


tmp = tempfile.mkdtemp(prefix="poe_installer_test_")
try:
    root = os.path.join(tmp, "app")
    data_dir = os.path.join(tmp, "user-data")
    for rel in (
        "routes/act1.json",
        "data/pob_leveling_adapters.json",
        "builds/allflame/party_bundle.json",
    ):
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("{}")

    paths = app_runtime.app_paths(root, data_dir)
    assert paths["config"] == os.path.join(data_dir, "config.json")
    assert paths["ui_state"] == os.path.join(data_dir, "ui_state.json")
    assert paths["runs"] == os.path.join(data_dir, "runs")
    assert paths["bundle"].endswith(
        os.path.join("builds", "allflame", "party_bundle.json"))
    assert app_runtime.user_data_dir({
        app_runtime.DATA_DIR_ENV: data_dir}) == data_dir

    cfg = app_runtime.prepare_config(
        paths["config"], join_party.DEFAULT_CONFIG, paths)
    assert cfg["routes_dir"] == paths["routes"]
    assert cfg["runs_dir"] == paths["runs"]
    assert cfg["layouts"]["dir"] == paths["layouts"]
    assert os.path.isdir(paths["runs"])
    assert os.path.isdir(paths["logs"])
    assert app_runtime.needs_first_run(cfg, paths["config"])

    notes = os.path.join(root, "builds", "allflame", "Carry_notes.json")
    with open(notes, "w", encoding="utf-8") as f:
        f.write("[]")
    cfg["selected_build"] = {"id": "Carry"}
    cfg["build_notes"] = os.path.relpath(
        notes, os.path.dirname(paths["config"]))
    from tools import setup_profiles
    setup_profiles.write_config(cfg, paths["config"])
    assert not app_runtime.needs_first_run(cfg, paths["config"])

    # Existing user preferences and valid custom paths survive normalization.
    custom_routes = os.path.join(tmp, "custom-routes")
    custom_runs = os.path.join(tmp, "custom-runs")
    custom_layouts = os.path.join(tmp, "custom-layouts")
    for path in (custom_routes, custom_runs, custom_layouts):
        os.makedirs(path)
    cfg["opacity"] = 0.77
    cfg["routes_dir"] = custom_routes
    cfg["runs_dir"] = custom_runs
    cfg["layouts"]["dir"] = custom_layouts
    setup_profiles.write_config(cfg, paths["config"])
    preserved = app_runtime.prepare_config(
        paths["config"], join_party.DEFAULT_CONFIG, paths)
    assert preserved["opacity"] == 0.77
    assert preserved["routes_dir"] == custom_routes
    assert preserved["runs_dir"] == custom_runs
    assert preserved["layouts"]["dir"] == custom_layouts

    assert app_runtime.self_test(paths) == []
    os.unlink(os.path.join(root, "routes", "act1.json"))
    missing = app_runtime.self_test(paths)
    assert len(missing) == 1 and missing[0].endswith("act1.json")
finally:
    shutil.rmtree(tmp, ignore_errors=True)


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


spec = read("packaging/poe_league_tools.spec")
assert 'name="PoE League Tools"' in spec
assert "console=False" in spec
assert "COLLECT(" in spec, "release must use one-folder mode"
assert "builds" in spec and "overlay" in spec and "routes" in spec

iss = read("packaging/poe_league_tools.iss")
assert "PrivilegesRequired=lowest" in iss
assert r"DefaultDirName={localappdata}\Programs\{#AppName}" in iss
assert "PoE-League-Tools-Setup" in iss
assert "[Icons]" in iss and "[Run]" in iss
assert 'Parameters: "--setup"' in iss

workflow = read(".github/workflows/build-windows-installer.yml")
assert "windows-latest" in workflow
assert "PyInstaller" in workflow
assert "ISCC.exe" in workflow
assert "--self-test" in workflow
assert "upload-artifact@v4" in workflow

print("ALL TESTS PASSED")
