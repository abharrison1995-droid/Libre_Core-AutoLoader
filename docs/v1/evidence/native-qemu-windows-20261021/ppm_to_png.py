import pathlib
import struct
import sys
import zlib


def convert(source: pathlib.Path, target: pathlib.Path) -> None:
    data = source.read_bytes()
    offset = 0

    def token() -> bytes:
        nonlocal offset
        while offset < len(data):
            if data[offset : offset + 1] == b"#":
                while offset < len(data) and data[offset : offset + 1] not in (b"\n", b"\r"):
                    offset += 1
            elif data[offset : offset + 1].isspace():
                offset += 1
            else:
                break
        start = offset
        while offset < len(data) and not data[offset : offset + 1].isspace() and data[offset : offset + 1] != b"#":
            offset += 1
        return data[start:offset]

    magic = token()
    width = int(token())
    height = int(token())
    maximum = int(token())
    if offset >= len(data) or not data[offset : offset + 1].isspace():
        raise ValueError("PPM header has no pixel-data separator")
    offset += 1
    pixels = data[offset:]
    if magic != b"P6" or maximum != 255 or len(pixels) != width * height * 3:
        raise ValueError("Expected an 8-bit binary PPM (P6) capture")

    def chunk(kind: bytes, contents: bytes) -> bytes:
        return (
            struct.pack(">I", len(contents))
            + kind
            + contents
            + struct.pack(">I", zlib.crc32(kind + contents) & 0xFFFFFFFF)
        )

    scanlines = b"".join(
        b"\0" + pixels[row * width * 3 : (row + 1) * width * 3]
        for row in range(height)
    )
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(scanlines))
        + chunk(b"IEND", b"")
    )
    target.write_bytes(png)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: ppm_to_png.py SOURCE.ppm TARGET.png")
    convert(pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]))
