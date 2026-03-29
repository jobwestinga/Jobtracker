from pathlib import Path

import PyInstaller.__main__


def main() -> None:
    assets_dir = Path("assets")
    assets_dir.mkdir(exist_ok=True)

    spec_path = Path("JobTracker.spec")
    icon_path = assets_dir / "icon.icns"

    if not icon_path.exists():
        print("Warning: assets/icon.icns is missing. The bundled app may use a default icon.")

    if spec_path.exists():
        args = [str(spec_path), "--noconfirm", "--clean"]
    else:
        args = [
            "main.py",
            "--name=JobTracker",
            "--windowed",
            "--noconfirm",
            "--clean",
        ]
        if icon_path.exists():
            args.append(f"--icon={icon_path}")

    PyInstaller.__main__.run(args)


if __name__ == "__main__":
    main()
