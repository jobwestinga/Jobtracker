"""Broken examples prove navigation and dependency checks fail usefully."""
import json
from pathlib import Path

import pytest

from scripts import check_boundaries, check_references, check_web_assets, dev


def put(root, name, text):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.mark.parametrize("reference", [
    {"path": "missing.py"},
    {"path": "owner.py", "symbol": "Owner.missing"},
    {"path": "owner.py", "symbol": "Missing"},
    {"path": "../outside.py"},
    {"path": "owner.js", "symbol": "missingExport"},
])
def test_broken_feature_reference_is_rejected(tmp_path, reference):
    put(tmp_path, "owner.py", "class Owner:\n    def save(self): pass\n")
    put(tmp_path, "owner.js", "export function save() {}\n")
    with pytest.raises(ValueError):
        check_references.validate_ref(tmp_path, reference)


def test_qualified_owner_and_reexport_remain_findable(tmp_path):
    put(tmp_path, "owner.py", "class Owner:\n    def save(self): pass\n")
    put(tmp_path, "impl.js", "export function save() {}\n")
    put(tmp_path, "entry.js", "export { save as persist } from './impl.js';\n")
    check_references.validate_ref(tmp_path, {"path": "owner.py", "symbol": "Owner.save"})
    check_references.validate_ref(tmp_path, {"path": "entry.js", "symbol": "persist"})
    with pytest.raises(ValueError):
        check_references.validate_ref(tmp_path, {"path": "entry.js", "symbol": "save"})


def test_comments_strings_and_missing_bindings_do_not_define_exports(tmp_path):
    put(tmp_path, "entry.js", '// export function removed() {}\nconst text = "export function imaginary() {}";\nexport { missing };\nfunction outer() { const nested = 1; }\nexport { nested };\nconst value = 1; export { value as present };\n')
    check_references.validate_ref(tmp_path, {"path": "entry.js", "symbol": "present"})
    for symbol in ("removed", "imaginary", "missing", "nested"):
        with pytest.raises(ValueError):
            check_references.validate_ref(tmp_path, {"path": "entry.js", "symbol": symbol})


def test_named_import_reexport_must_exist_in_implementation(tmp_path):
    put(tmp_path, "impl.js", "export function actual() {}\n")
    put(tmp_path, "entry.js", "import { removed, actual as alias } from './impl.js'; export { removed as missing, alias as present };\n")
    check_references.validate_ref(tmp_path, {"path": "entry.js", "symbol": "present"})
    with pytest.raises(ValueError):
        check_references.validate_ref(tmp_path, {"path": "entry.js", "symbol": "missing"})


def test_unknown_feature_contract_and_operation_fail(tmp_path):
    put(tmp_path, "owner.py", "class Owner: pass\n")
    put(tmp_path, "test_owner.py", "")
    feature = {"id": "feature.owner", "title": "Owner", "entrypoints": [{"path": "owner.py"}],
               "rule_owner": {"path": "owner.py", "symbol": "Owner"}, "persistence": [],
               "operations": ["unregistered"], "tables": [], "contracts": ["missing.contract"],
               "routes": ["GET /missing"], "tests": ["test_owner.py"], "related": ["missing.feature"]}
    errors = check_references.validate_metadata(tmp_path, {"schema_version": 1, "features": [feature, feature]},
                                                {"schema_version": 1, "contracts": []}, {}, {})
    assert "duplicate feature ID" in errors
    assert any("unknown contracts" in error for error in errors)
    assert any("unknown related" in error for error in errors)
    assert any("unknown operations" in error for error in errors)
    assert any("unknown routes" in error for error in errors)


def test_markdown_moved_file_or_heading_fails(tmp_path):
    source = put(tmp_path, "docs/index.md", "# Index\n")
    put(tmp_path, "docs/owner.md", "# Owner\n\n## Save data\n")
    check_references.document_target(tmp_path, source, "owner.md#save-data")
    for target in ("missing.md", "owner.md#renamed-heading", "../../outside.md"):
        with pytest.raises(ValueError):
            check_references.document_target(tmp_path, source, target)


@pytest.mark.parametrize("link", ['[Owner](absent.md "Owner reference")', '[Owner][owner]\n\n[owner]: absent.md'])
def test_standard_markdown_links_are_checked(tmp_path, link):
    put(tmp_path, "docs/index.md", link)
    for name in ("README.md", "AGENTS.md", "CLAUDE.md"):
        put(tmp_path, name, "")
    assert any("absent.md" in error for error in check_references.markdown_errors(tmp_path))


def test_fenced_headings_are_not_anchors_and_underscores_are_preserved(tmp_path):
    path = put(tmp_path, "docs/owner.md", "## `save_data`\n\n```text\n## Missing heading\n```\n")
    check_references.document_target(tmp_path, path, "#save_data")
    with pytest.raises(ValueError):
        check_references.document_target(tmp_path, path, "#missing-heading")


def test_core_cannot_import_ui_even_lazily(tmp_path):
    put(tmp_path, "jobtracker/core/rules.py", "def use_ui():\n    from jobtracker.ui import app\n")
    put(tmp_path, "jobtracker/ui/app.py", "")
    errors = check_boundaries.check(tmp_path)
    assert any("forbidden import jobtracker.ui.app" in error for error in errors)


def test_python_cycle_check_distinguishes_lazy_and_type_only_imports(tmp_path):
    put(tmp_path, "jobtracker/ui/a.py", "from . import b\n")
    put(tmp_path, "jobtracker/ui/b.py", "from . import a\n")
    assert any("Python eager import cycle" in error for error in check_boundaries.check(tmp_path))
    put(tmp_path, "jobtracker/ui/b.py", "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from . import a\nclass Dialog:\n    def open(self):\n        from . import a\n")
    assert not check_boundaries.check(tmp_path)


def test_package_initializer_cycle_is_detected(tmp_path):
    put(tmp_path, "jobtracker/ui/a.py", "from .widgets import view\nvalue = 1\n")
    put(tmp_path, "jobtracker/ui/widgets/__init__.py", "from ..a import value\n")
    put(tmp_path, "jobtracker/ui/widgets/view.py", "")
    assert any("Python eager import cycle" in error for error in check_boundaries.check(tmp_path))


def test_phone_feature_cannot_import_shell_or_create_cycle(tmp_path):
    put(tmp_path, "web/app.js", "import './features/owner.js';\n")
    put(tmp_path, "web/features/owner.js", "import '../app.js';\n")
    errors = check_boundaries.check(tmp_path)
    assert any("feature imports composition shell" in error for error in errors)
    assert any("JavaScript import cycle" in error for error in errors)


def test_dynamic_literal_imports_follow_asset_and_boundary_rules(tmp_path):
    put(tmp_path, "web/app.js", "import './features/owner.js'; // import './removed.js';\n")
    put(tmp_path, "web/features/owner.js", "async function load() { await import('../app.js'); }\n")
    errors = check_boundaries.check(tmp_path)
    assert any("feature imports composition shell" in error for error in errors)
    assert not any("removed.js" in error for error in errors)


@pytest.mark.parametrize("statement", ["from tests.test_api import api", "import tests.test_api"])
def test_collected_tests_cannot_be_helper_modules(tmp_path, statement):
    put(tmp_path, "tests/test_owner.py", statement + "\n")
    assert any("tests.support" in error for error in check_boundaries.check(tmp_path))


@pytest.mark.parametrize("statement", ["import './feature.js';", "async function load() { await import('./feature.js'); }"])
def test_transitive_web_asset_requires_cache_entry(tmp_path, statement):
    put(tmp_path, "web/index.html", '<script type="module" src="/app.js"></script>')
    put(tmp_path, "web/app.js", statement + "\n")
    put(tmp_path, "web/feature.js", "export function render() {}\n")
    put(tmp_path, "web/manifest.webmanifest", "{}")
    put(tmp_path, "web/icon.png", "placeholder")
    inventory = ["/", "/index.html", "/app.js", "/manifest.webmanifest", "/icon.png"]
    put(tmp_path, "web/sw.js", "const SHELL = " + json.dumps(inventory) + ";")
    assert "sw.js: uncached dependency feature.js" in check_web_assets.check(tmp_path)
    inventory.append("/feature.js")
    put(tmp_path, "web/sw.js", "const SHELL = " + json.dumps(inventory) + ";")
    assert not check_web_assets.check(tmp_path)
    put(tmp_path, "web/app.js", "import './missing.js';\n")
    assert any("missing dependency" in error for error in check_web_assets.check(tmp_path))


def test_unknown_context_and_interpreter_fail_without_app_import():
    with pytest.raises(ValueError, match="Unknown feature"):
        dev.feature_context("unknown.feature")
    with pytest.raises(ValueError, match="Cannot use Python"):
        dev.validate_python("/this/interpreter/does/not/exist", [])


def test_generated_index_drift_is_detected_and_reproducible(tmp_path):
    documents = {"feature-index.md": "# Feature index\n"}
    assert check_references.update_generated(tmp_path, documents)
    assert not check_references.update_generated(tmp_path, documents, write=True)
    path = tmp_path / "docs/generated/feature-index.md"
    original = path.read_bytes()
    assert not check_references.update_generated(tmp_path, documents)
    check_references.update_generated(tmp_path, documents, write=True)
    assert path.read_bytes() == original
    path.write_text("stale owner")
    assert "generated reference drift" in check_references.update_generated(tmp_path, documents)[0]
