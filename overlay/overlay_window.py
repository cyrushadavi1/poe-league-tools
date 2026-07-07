"""Always-on-top overlay card (PyQt6). Purely visual — never sends input."""
import html

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

KIND_COLORS = {"kill": "#e46a6a", "town": "#6ab0e4",
               "trial": "#c9a44a", "travel": "#9aa4b2"}

# item-evaluator verdict colors: TAKE green-ish / SKIP grey / CHECK amber
VERDICT_COLORS = {"TAKE": "#7ec27d", "SKIP": "#8a93a0", "CHECK": "#c9a44a"}


class OverlayWindow(QWidget):
    def __init__(self, cfg):
        super().__init__()
        self._drag = None
        self.level = 1
        self.notes = {}                      # act -> gem note text
        self._party_text = ""
        self._flashing = False
        self._meta_bits = []                 # current step's meta lines
        self._status_text = ""               # run-tracker timer/XP bit

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowOpacity(cfg.get("opacity", 0.92))
        self.setFixedWidth(cfg.get("width", 360))

        card = QFrame(self)
        card.setObjectName("card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)
        inner = QVBoxLayout(card)
        inner.setContentsMargins(14, 10, 14, 12)
        inner.setSpacing(6)

        self.header = QLabel(objectName="hdr")
        self.body = QLabel(wordWrap=True)
        self.meta = QLabel(wordWrap=True, objectName="meta")
        self.party = QLabel(wordWrap=True, objectName="party")
        self.item = QLabel(wordWrap=True, objectName="item")
        self.nxt = QLabel(wordWrap=True, objectName="next")
        for w in (self.header, self.body, self.meta, self.party,
                  self.item, self.nxt):
            inner.addWidget(w)
        self.party.setVisible(False)
        self.item.setVisible(False)          # transient item verdict line
        self._item_timer = QTimer(self)
        self._item_timer.setSingleShot(True)
        self._item_timer.timeout.connect(self._hide_item)
        # single restartable timer (same pattern as _item_timer): a new
        # flash cancels the pending restore, so overlapping flashes can
        # never cut each other short
        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(self._end_flash)

        pt = cfg.get("font_pt", 11)
        self.setStyleSheet(f"""
            #card {{ background: rgba(14, 16, 21, 235);
                     border: 1px solid #2a2f3a; border-radius: 10px; }}
            QLabel {{ color: #dfe5ee; font-size: {pt}pt;
                      font-family: 'Segoe UI', 'Helvetica Neue', sans-serif; }}
            #hdr  {{ color: #8fa3bf; font-size: {pt - 2}pt; letter-spacing: 1px; }}
            #meta {{ color: #9aa4b2; font-size: {pt - 2}pt; }}
            #party {{ color: #b7c0cc; font-size: {pt - 2}pt; }}
            #item {{ color: #dfe5ee; font-size: {pt - 1}pt; }}
            #next {{ color: #6d7683; font-size: {pt - 2}pt; }}
        """)

    # -- content ----------------------------------------------------------
    def show_step(self, step, progress, peek):
        n, total, act = progress
        color = KIND_COLORS.get(step.get("kind", "travel"), "#9aa4b2")
        self.header.setText(
            f"ACT {act} · {n}/{total} · "
            f"<span style='color:{color}'><b>{step.get('zone', '')}</b></span>"
            f" · lvl {self.level}")
        self.body.setText("<br>".join(f"• {d}" for d in step.get("do", [])))

        bits = []
        if step.get("layout"):
            bits.append(f"◆ {step['layout']}")
        if step.get("tip"):
            bits.append(f"✦ {step['tip']}")
        if act in self.notes:
            bits.append(f"⚙ {self.notes[act]}")
        self._meta_bits = bits
        self._render_meta()
        self.nxt.setText(f"next: {peek['zone']}" if peek else "— end of route —")

    def set_level(self, lvl):
        self.level = lvl

    def set_notes(self, notes):
        self.notes = notes

    # -- run tracker status (timer splits + XP warning) ----------------------
    def set_status(self, text):
        """Run-tracker bit appended to the meta row, e.g.
        'A3 41:22 (-2:10 PB)  ⚠ XP -38%'. Empty string hides it."""
        if text == self._status_text:
            return
        self._status_text = text
        self._render_meta()

    def _render_meta(self):
        bits = list(self._meta_bits)
        if self._status_text:
            bits.append(f"⏱ {self._status_text}")
        self.meta.setText("<br>".join(bits))
        self.meta.setVisible(bool(bits))

    # -- clipboard item verdict (transient, separate from the death flash) ---
    def show_item(self, verdict, name, reason, ms=6000):
        """Show a color-coded item verdict for ~6 s on its own line.

        Uses a dedicated label + timer so it can never clobber a death
        flash on the party row; a new verdict simply replaces the old.
        """
        color = VERDICT_COLORS.get(verdict, "#9aa4b2")
        self.item.setText(
            f"<span style='color:{color}'><b>{html.escape(str(verdict))}"
            f"</b></span> {html.escape(str(name))} "
            f"<span style='color:#9aa4b2'>— {html.escape(str(reason))}</span>")
        self.item.setVisible(True)
        self._item_timer.start(ms)

    def _hide_item(self):
        self.item.setVisible(False)

    # -- party row ----------------------------------------------------------
    def set_party(self, text):
        """Persistent party status line; hidden when empty."""
        self._party_text = text
        if not self._flashing:
            self.party.setText(text)
            self.party.setVisible(bool(text))

    def flash(self, text, ms=6000):
        """Show an urgent message on the party row, then restore it.
        A newer flash restarts the timer (full display time) instead of
        being wiped early by the previous flash's timeout."""
        self._flashing = True
        self.party.setText(f"<span style='color:#e46a6a'>{text}</span>")
        self.party.setVisible(True)
        self._flash_timer.start(ms)

    def _end_flash(self):
        self._flashing = False
        self.set_party(self._party_text)

    # -- window behaviour ---------------------------------------------------
    def toggle_visible(self):
        self.setVisible(not self.isVisible())

    def toggle_clickthrough(self):
        # setWindowFlags hides the window as a side effect; only re-show
        # it if it was visible, so a global hotkey pressed while the
        # overlay is hidden (F4) does not force it back onto the screen.
        was_visible = self.isVisible()
        self.setWindowFlags(self.windowFlags()
                            ^ Qt.WindowType.WindowTransparentForInput)
        if was_visible:
            self.show()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        self._drag = None
