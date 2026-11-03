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
