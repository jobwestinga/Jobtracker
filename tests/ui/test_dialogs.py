"""Offscreen native dialogs regressions on isolated databases."""
from PySide6.QtCore import (Qt)
from PySide6.QtTest import (QTest)
from PySide6.QtWidgets import (QColorDialog, QFileDialog, QFrame, QMessageBox)
from jobtracker.ui.widgets.dialog_utils import (information)
from jobtracker.ui.widgets.goal_dialog import (GoalDialog)
from jobtracker.ui.widgets.settings_dialog import (SettingsDialog)
from jobtracker.ui.widgets.subject_dialog import (SubjectDialog)
from tests.support.native import (_application)

def test_parented_editors_are_widgets_from_birth_and_accept_text():
    qt_app = _application()
    parent = QFrame()
    parent.show()
    dialogs = (GoalDialog(parent), SubjectDialog(parent))
    try:
        for dialog in dialogs:
            assert dialog.windowType() == Qt.Widget
            assert dialog.windowModality() == Qt.NonModal
            assert not dialog.isWindow()
            dialog.show()
            editor = (
                dialog.name_input
                if hasattr(dialog, "name_input")
                else dialog.title_input
            )
            editor.setFocus()
            qt_app.processEvents()
            QTest.keyClicks(editor, "Typed")
            assert editor.text().endswith("Typed")
            dialog.hide()
    finally:
        for dialog in dialogs:
            dialog.close()
        parent.close()


def test_message_box_helper_uses_parented_window_modal_dialog():
    qt_app = _application()
    parent = QFrame()
    box = information(parent, "Title", "Body")
    assert box._jt_original_parent is parent
    qt_app.processEvents()
    assert box.windowType() == Qt.Widget
    assert box.window() is parent
    assert not box.isWindow()
    box.done(QMessageBox.Ok)
    qt_app.processEvents()
    parent.close()


def test_framework_color_and_file_panels_remain_window_modal(service):
    qt_app = _application()
    parent = QFrame()
    parent.show()

    subject = SubjectDialog(parent)
    subject.show()
    subject._pick_custom()
    color = subject.findChild(QColorDialog)
    assert color is not None and not color.isVisible()
    qt_app.processEvents()
    assert color.isVisible()
    assert color.testOption(QColorDialog.DontUseNativeDialog)
    assert color.windowType() == Qt.Dialog
    assert color.windowModality() == Qt.WindowModal
    assert color.isWindow()
    color.reject()
    qt_app.processEvents()
    subject.close()

    settings = SettingsDialog(parent, service=service)
    settings.show()
    settings._export()
    file_dialog = settings.findChild(QFileDialog)
    assert file_dialog is not None and not file_dialog.isVisible()
    qt_app.processEvents()
    assert file_dialog.isVisible()
    assert file_dialog.testOption(QFileDialog.DontUseNativeDialog)
    assert file_dialog.windowType() == Qt.Dialog
    assert file_dialog.windowModality() == Qt.WindowModal
    assert file_dialog.isWindow()
    file_dialog.reject()
    qt_app.processEvents()
    settings.close()
    parent.close()


def test_theme_picker_selects_one_complete_theme_and_keeps_legacy_preferences(service):
    qt_app = _application()
    service.set_setting("theme_fx", "Clean")
    service.set_setting("theme_palette", "Light")
    dialog = SettingsDialog(service=service)
    dialog.show()
    qt_app.processEvents()
    assert dialog.theme_combo.currentData() == ("Clean", "Light")
    dialog._select_preset("OLED Midnight")
    assert dialog.theme_combo.currentText() == "OLED Midnight"
    assert (dialog._fx, dialog._palette) == ("Clean", "Obsidian")
    dialog._select_preset("Carbon Dark")
    assert dialog.theme_combo.currentText() == "Carbon Dark"
    assert (dialog._fx, dialog._palette) == ("Clean", "Dark")
    assert service.get_setting("theme_palette") == "Light"
    dialog.close()

