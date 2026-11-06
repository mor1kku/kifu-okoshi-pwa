"""配布前のアプリ構成を検証する。Appleによる署名・実機試験の代わりではない。"""
import plistlib
import sys
from pathlib import Path

appPath = Path(sys.argv[1])
entitlementsPath = Path(sys.argv[2])
with (appPath / "Info.plist").open("rb") as stream:
    info = plistlib.load(stream)
with entitlementsPath.open("rb") as stream:
    entitlements = plistlib.load(stream)
assert info["CFBundleIdentifier"] == "io.github.mor1kku.KifuCaptureProbe"
assert info["UIBackgroundModes"] == ["screen-capture"]
assert float(info["MinimumOSVersion"]) >= 27
assert (appPath / info["CFBundleExecutable"]).stat().st_size > 0
assert entitlements == {"com.apple.developer.screen-recording": True}
assert not (appPath / "embedded.mobileprovision").exists()
assert not any("UsageDescription" in key for key in info)
print("Bundle and requested entitlement verified. Personal re-signing and a device test are still required.")
