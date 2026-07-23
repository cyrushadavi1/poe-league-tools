#!/usr/bin/env python3
"""Windows desktop entry point for PoE League Tools.

The installed executable opens first-run setup when needed and then starts
the overlay.  It also remains runnable from a source checkout for local
verification.
"""
from __future__ import annotations

import os
import sys
import traceback

import app_runtime


def _add_import_paths(root: str) -> None:
    for rel in ("", "overlay", "buildgen", "market", "advisor"):
        path = os.path.join(root, rel) if rel else root
        if path not in sys.path:
            sys.path.insert(0, path)


def _show_fatal(message: str, log_path: str) -> None:
    try:
        from PyQt6.QtWidgets import QApplication, QMessageBox
        app = QApplication.instance() or QApplication(sys.argv)
        QMessageBox.critical(
            None, "PoE League Tools could not start",
            f"{message}\n\nDiagnostic details were saved to:\n{log_path}")
    except Exception:
        pass


def _write_crash(paths: dict) -> str:
    os.makedirs(paths["logs"], exist_ok=True)
    path = os.path.join(paths["logs"], "last-crash.log")
    with open(path, "w", encoding="utf-8") as f:
        traceback.print_exc(file=f)
    return path


def run(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    paths = app_runtime.app_paths()
    _add_import_paths(paths["root"])

    if "--self-test" in argv:
        missing = app_runtime.self_test(paths)
        if missing:
            return 1
        # Exercise imports that the normal GUI reaches only after startup.
        # This catches missing PyInstaller hidden imports without opening a
        # window or requiring Path of Exile on the build machine.
        import main as overlay_main  # noqa: F401
        import party  # noqa: F401
        from tools import setup_gui, setup_profiles  # noqa: F401
        bundle_path, bundle = setup_profiles.find_bundle(paths["root"])
        return 0 if bundle_path and bundle else 1
    force_setup = "--setup" in argv

    from tools import join_party, setup_gui, setup_profiles

    cfg = app_runtime.prepare_config(
        paths["config"], join_party.DEFAULT_CONFIG, paths)

    if force_setup or app_runtime.needs_first_run(cfg, paths["config"]):
        from PyQt6.QtWidgets import QApplication, QMessageBox
        app = QApplication.instance() or QApplication(sys.argv)
        bundle_path, bundle = setup_profiles.find_bundle(paths["root"])
        if not bundle:
            try:
                bundle_path, bundle = setup_gui.prepare_allflame_bundle()
            except Exception as exc:
                QMessageBox.critical(
                    None, "Prepared builds are unavailable",
                    "The installer is missing its prepared build data.\n\n"
                    f"{exc}")
                return 2
        saved = setup_gui.choose_and_save(
            None, paths["config"], bundle_path, bundle, include_client=True)
        if saved is None:
            return 0

    import main as overlay_main
    overlay_main.main([
        "--config", paths["config"],
        "--state", paths["ui_state"],
    ])
    return 0


def main() -> int:
    paths = app_runtime.app_paths()
    try:
        return run()
    except SystemExit as exc:
        return int(exc.code or 0)
    except BaseException as exc:  # noqa: BLE001 -- GUI must surface failures
        log_path = _write_crash(paths)
        _show_fatal(str(exc) or type(exc).__name__, log_path)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
