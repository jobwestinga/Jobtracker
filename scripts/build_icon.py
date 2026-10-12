"""Build the existing macOS ICNS icon without changing source artwork."""

from __future__ import annotations

from pathlib import Path
import argparse
import struct
import subprocess
import tempfile

ICON_SIZES = (("icp4", 16), ("icp5", 32), ("icp6", 64), ("ic07", 128),
              ("ic08", 256), ("ic09", 512), ("ic10", 1024))


def encode_icns(images: dict[int, bytes]) -> bytes:
    chunks = []
    for kind, size in ICON_SIZES:
        data = images[size]
        chunks.append(kind.encode("ascii") + struct.pack(">I", 8 + len(data)) + data)
    body = b"".join(chunks)
    return b"icns" + struct.pack(">I", 8 + len(body)) + body


def build_icon(source: Path, destination: Path, *, run=subprocess.run) -> Path:
    from PySide6.QtGui import QColor, QImage, QPainter

    image = QImage(str(source))
    if image.isNull():
        raise ValueError(f"Could not read icon artwork: {source}")
    with tempfile.TemporaryDirectory(prefix="jobtracker-icon-") as temporary:
        folder = Path(temporary)
        base = QImage(image.size(), QImage.Format_ARGB32)
        base.fill(QColor("#050B18"))
        painter = QPainter(base)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.drawImage(0, 0, image)
        painter.end()
        png = folder / "base.png"
        if not base.convertToFormat(QImage.Format_RGB32).save(str(png), "PNG"):
            raise ValueError("Could not prepare icon artwork")
        images = {}
        for _, size in ICON_SIZES:
            output = folder / f"{size}.png"
            run(["sips", "-z", str(size), str(size), "-s", "format", "png",
                 str(png), "--out", str(output)], check=True, capture_output=True)
            images[size] = output.read_bytes()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(encode_icns(images))
    return destination


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args(argv)
    build_icon(args.source, args.destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
