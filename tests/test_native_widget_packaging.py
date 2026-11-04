"""Local widget packaging must keep the sandbox and production/pilot boundary."""
from scripts import build_native, install_macos
import plistlib
import shutil
import subprocess
import sys

import pytest


def test_widget_sandbox_grants_one_read_only_snapshot_per_identity():
    for channel in ("production", "pilot"):
        entitlements = build_native.widget_entitlements(channel)
        assert entitlements == {"com.apple.security.app-sandbox": True,
            "com.apple.security.temporary-exception.files.home-relative-path.read-only": [
                f"/Library/Application Support/JobTracker Widget/{channel}/goals.json"]}
        info = build_native.widget_info(channel, "2.0.0")
        assert info["CFBundleIdentifier"] == build_native.IDENTITIES[channel]["bundle_id"] + ".GoalsWidget"
        assert info["JTWidgetChannel"] == channel
        assert info["NSExtension"]["NSExtensionPointIdentifier"] == "com.apple.widgetkit-extension"


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS signing integration")
def test_installer_resigning_preserves_the_extension_entitlements(tmp_path):
    app = tmp_path / "JobTracker.app"
    extension = app / "Contents/PlugIns/Widget.appex"
    for target, ident in [(extension, "com.jobtracker.app.GoalsWidget"), (app, "com.jobtracker.app")]:
        executable = target / "Contents/MacOS/JobTracker"
        executable.parent.mkdir(parents=True)
        shutil.copyfile("/usr/bin/true", executable)
        executable.chmod(0o755)
        (target / "Contents/Info.plist").write_bytes(plistlib.dumps({
            "CFBundleIdentifier": ident, "CFBundleExecutable": "JobTracker",
            "CFBundlePackageType": "APPL" if target == app else "XPC!"}))
    entitlements = build_native.widget_entitlements("production")
    entitlement_file = tmp_path / "widget.entitlements"
    entitlement_file.write_bytes(plistlib.dumps(entitlements))
    subprocess.run(["codesign", "--force", "--sign", "-", "--entitlements", str(entitlement_file), str(extension)], check=True, capture_output=True)
    subprocess.run(["codesign", "--force", "--sign", "-", str(app)], check=True, capture_output=True)
    install_macos.sign_bundle(app)
    result = subprocess.run(["codesign", "--display", "--entitlements", ":-", str(extension)], check=True, capture_output=True)
    assert plistlib.loads(result.stdout) == entitlements


@pytest.fixture(scope="module")
def widget_binary():
    return build_native.build_widget()


@pytest.fixture
def widget_app(tmp_path, widget_binary):
    app = tmp_path / "JobTracker.app"
    extension = app / "Contents/PlugIns/JobTrackerGoalsWidget.appex"
    executable = extension / "Contents/MacOS/JobTrackerGoalsWidget"
    executable.parent.mkdir(parents=True)
    shutil.copyfile(widget_binary, executable)
    executable.chmod(0o755)
    (extension / "Contents/Info.plist").write_bytes(plistlib.dumps(build_native.widget_info("pilot", build_native.NATIVE_VERSION)))
    subprocess.run(["codesign", "--force", "--sign", "-", str(extension)], check=True, capture_output=True)
    return app


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS WidgetKit integration")
def test_built_widget_uses_xpc_bootstrap_and_retains_its_swift_entry(widget_app):
    # Exercise the real product's compiler/linker settings. A signed, registered
    # plain Swift executable previously crashed before gallery descriptors loaded.
    assert build_native.smoke_widget(widget_app) == {"xpc_bootstrap": True, "swift_entry": True}


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS symbol visibility integration")
def test_widget_smoke_accepts_a_retained_entry_without_an_exported_main(widget_app, tmp_path):
    extension = widget_app / "Contents/PlugIns/JobTrackerGoalsWidget.appex"
    executable = extension / "Contents/MacOS/JobTrackerGoalsWidget"
    exports = tmp_path / "exports.txt"
    exports.write_text("")
    # Localize real Swift definitions without altering the retained entry metadata
    # or its code. The entry need not be exposed as a global symbol named _main.
    subprocess.run(["nmedit", "-s", str(exports), str(executable)], check=True, capture_output=True)
    subprocess.run(["codesign", "--force", "--sign", "-", str(extension)], check=True, capture_output=True)
    symbols = subprocess.run(["nm", str(executable)], check=True, capture_output=True, text=True).stdout
    assert not any(line.endswith(" T _main") for line in symbols.splitlines())
    assert build_native.smoke_widget(widget_app) == {"xpc_bootstrap": True, "swift_entry": True}


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS extension bootstrap integration")
def test_widget_smoke_rejects_xpc_bootstrap_without_a_swift_entry(widget_app, tmp_path):
    extension = widget_app / "Contents/PlugIns/JobTrackerGoalsWidget.appex"
    executable = extension / "Contents/MacOS/JobTrackerGoalsWidget"
    source = tmp_path / "bootstrap.c"
    source.write_text("int main(void) { return 0; }\n")
    subprocess.run(["xcrun", "clang", str(source), "-framework", "Foundation", "-Wl,-e,_NSExtensionMain", "-o", str(executable)], check=True, capture_output=True)
    subprocess.run(["codesign", "--force", "--sign", "-", str(extension)], check=True, capture_output=True)
    with pytest.raises(RuntimeError, match="retained Swift entry"):
        build_native.smoke_widget(widget_app)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS executable integration")
def test_widget_smoke_rejects_an_ordinary_executable(tmp_path):
    app = tmp_path / "JobTracker.app"
    executable = app / "Contents/PlugIns/JobTrackerGoalsWidget.appex/Contents/MacOS/JobTrackerGoalsWidget"
    executable.parent.mkdir(parents=True)
    shutil.copyfile("/usr/bin/true", executable)
    with pytest.raises(RuntimeError, match="XPC bootstrap"):
        build_native.smoke_widget(app)
