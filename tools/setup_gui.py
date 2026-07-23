#!/usr/bin/env python3
"""Graphical first-run setup and in-app build selector.

Launched by ``setup_pc.bat``.  Players choose one of the prepared party
PoBs from a dropdown, enter their actual in-game character name, and save.
No terminal questions, JSON editing, or build-generation command is needed.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OVERLAY = os.path.join(ROOT, "overlay")
BUILDGEN = os.path.join(ROOT, "buildgen")
for path in (ROOT, OVERLAY, BUILDGEN):
    if path not in sys.path:
        sys.path.insert(0, path)

import find_client  # noqa: E402
from tools import join_party, setup_profiles  # noqa: E402


def prepare_allflame_bundle(progress=None):
    """Generate the reviewed hardcoded builds when they were not shipped.

    Portable releases include these files already.  A source checkout may
    not because ``builds/`` is intentionally gitignored, so graphical setup
    transparently performs the same generation work in that case.
    """
    bundle_path, bundle = setup_profiles.find_bundle(ROOT)
    if bundle:
        return bundle_path, bundle

    manifest_path = os.path.join(BUILDGEN, "party.allflame.json")
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)
    out_dir = os.path.join(ROOT, "builds", "allflame")
    os.makedirs(out_dir, exist_ok=True)

    import party as party_builder  # noqa: E402

    built = []
    members = manifest.get("members") or []
    for i, member in enumerate(members):
        if progress is not None:
            from PyQt6.QtWidgets import QApplication
            progress.setLabelText(
                f"Preparing {member.get('player', 'build')}…")
            progress.setValue(i)
            QApplication.instance().processEvents()
        built.append(party_builder.build_member(member, out_dir))

    with open(os.path.join(out_dir, "party_summary.md"),
              "w", encoding="utf-8") as f:
        f.write(party_builder.summary_md(built))
    bundle_path = os.path.join(out_dir, "party_bundle.json")
    bundle = party_builder.write_bundle(
        built, bundle_path, manifest.get("league", "3.29"))
    if progress is not None:
        progress.setValue(len(members))
    return bundle_path, bundle


def _current_character(cfg, bundle):
    party = cfg.get("party") or {}
    current = str(party.get("me") or "").strip()
    role_ids = {str(row.get("player") or "") for row in bundle["members"]}
    return "" if current in role_ids else current


def choose_and_save(parent, config_path, bundle_path, bundle,
                    include_client=True):
    """Show the picker, persist on acceptance, and return the new config."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import (
        QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
        QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
        QVBoxLayout, QWidget,
    )

    cfg = join_party.load_config(config_path, say=lambda _text: None)
    dialog = QDialog(parent)
    dialog.setWindowTitle("PoE League Tools — choose your build")
    dialog.setMinimumWidth(560)

    outer = QVBoxLayout(dialog)
    title = QLabel("<b>Choose your 3.29 party build</b>")
    title.setStyleSheet("font-size: 16px;")
    subtitle = QLabel(
        "This selects the leveling steps, exact gem checklist, and item "
        "pickup rules used by the overlay.")
    subtitle.setWordWrap(True)
    outer.addWidget(title)
    outer.addWidget(subtitle)

    form = QFormLayout()
    builds = QComboBox()
    for member in bundle["members"]:
        builds.addItem(setup_profiles.member_label(member))
    builds.setCurrentIndex(setup_profiles.selected_member_index(
        cfg, bundle, bundle_path, config_path))
    form.addRow("Build / PoB:", builds)

    details = QLabel()
    details.setWordWrap(True)
    details.setOpenExternalLinks(True)
    details.setTextInteractionFlags(
        Qt.TextInteractionFlag.TextBrowserInteraction)
    form.addRow("", details)

    character = QLineEdit(_current_character(cfg, bundle))
    character.setPlaceholderText(
        "Exact character name from Path of Exile (can be added later)")
    form.addRow("Your character:", character)

    teammates = QLineEdit(", ".join(
        (cfg.get("party") or {}).get("members") or []))
    teammates.setPlaceholderText(
        "Optional: exact character names, separated by commas")
    form.addRow("Teammates:", teammates)

    client_row = QWidget()
    client_layout = QHBoxLayout(client_row)
    client_layout.setContentsMargins(0, 0, 0, 0)
    configured_client = cfg.get("client_txt") or ""
    if include_client:
        detected, _how = find_client.discover(configured_client)
    else:
        detected = configured_client
    client = QLineEdit(detected or configured_client)
    client.setPlaceholderText(r"Path of Exile\logs\Client.txt")
    browse = QPushButton("Browse…")
    client_layout.addWidget(client, 1)
    client_layout.addWidget(browse)
    if include_client:
        form.addRow("Client.txt:", client_row)
    outer.addLayout(form)

    note = QLabel(
        "You can change this later by running <b>choose_build.bat</b>. "
        "The PoB itself is already prepared; nothing is imported into "
        "Path of Building on this PC.")
    note.setWordWrap(True)
    note.setStyleSheet("color: #667085;")
    outer.addWidget(note)

    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Save
        | QDialogButtonBox.StandardButton.Cancel)
    buttons.button(QDialogButtonBox.StandardButton.Save).setText(
        "Use this build")
    outer.addWidget(buttons)

    def refresh_details(index):
        member = bundle["members"][index]
        role = html.escape(str(member.get("role") or "Party role"))
        cls = html.escape(str(member.get("class") or ""))
        asc = html.escape(str(member.get("ascendancy") or ""))
        pob = str(member.get("pob") or "")
        build = f"{cls} ({asc})" if asc else cls
        text = f"<b>{role}</b>"
        if build:
            text += f" · {build}"
        if pob.startswith(("https://", "http://")):
            safe_url = html.escape(pob, quote=True)
            text += f' · <a href="{safe_url}">open source PoB</a>'
        details.setText(text)

    def browse_client():
        path, _ = QFileDialog.getOpenFileName(
            dialog, "Choose Path of Exile Client.txt",
            os.path.dirname(client.text()) if client.text() else "",
            "Client log (Client.txt);;Text files (*.txt);;All files (*)")
        if path:
            client.setText(path)

    def save():
        names = [row.strip() for row in teammates.text().split(",")
                 if row.strip()]
        if names and not character.text().strip():
            QMessageBox.warning(
                dialog, "Character name needed",
                "Enter your exact in-game character name to distinguish "
                "your level and deaths from your teammates.")
            return
        try:
            new_cfg = setup_profiles.apply_profile(
                cfg, config_path, bundle_path, bundle,
                builds.currentIndex(), character.text(), names)
            if include_client and client.text().strip():
                new_cfg["client_txt"] = client.text().strip()
            setup_profiles.write_config(new_cfg, config_path)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(dialog, "Could not save setup", str(exc))
            return
        dialog._saved_config = new_cfg
        dialog.accept()

    builds.currentIndexChanged.connect(refresh_details)
    browse.clicked.connect(browse_client)
    buttons.accepted.connect(save)
    buttons.rejected.connect(dialog.reject)
    refresh_details(builds.currentIndex())

    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return dialog._saved_config


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default=os.path.join(OVERLAY, "config.json"))
    ap.add_argument("--bundle", default=None,
                    help="optional party_bundle.json override")
    ap.add_argument("--build-only", action="store_true",
                    help="hide Client.txt selection when changing roles")
    args = ap.parse_args(argv)

    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication, QMessageBox, QProgressDialog

    app = QApplication.instance() or QApplication(sys.argv)
    bundle_path, bundle = setup_profiles.find_bundle(ROOT, args.bundle)
    if not bundle:
        progress = QProgressDialog(
            "Preparing the four reviewed PoBs…", "", 0, 4)
        progress.setWindowTitle("PoE League Tools")
        progress.setCancelButton(None)
        progress.setWindowModality(Qt.WindowModality.ApplicationModal)
        progress.show()
        app.processEvents()
        try:
            bundle_path, bundle = prepare_allflame_bundle(progress)
        except Exception as exc:  # noqa: BLE001 -- present actionable GUI
            progress.close()
            QMessageBox.critical(
                None, "Could not prepare builds",
                f"The prepared build files are missing and could not be "
                f"generated:\n\n{exc}\n\nCheck your internet connection "
                "or use a portable release that includes builds/allflame.")
            return 2
        progress.close()

    result = choose_and_save(
        None, args.config, bundle_path, bundle,
        include_client=not args.build_only)
    if result is None:
        return 1
    selected = result.get("selected_build") or {}
    QMessageBox.information(
        None, "Setup saved",
        f"Selected: {selected.get('id', 'build')}\n\n"
        "Start the overlay with overlay\\run_overlay.bat.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
