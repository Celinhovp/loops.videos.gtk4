#!/usr/bin/env python3
"""Install pinned local-translation wheels into a Flatpak app prefix."""

import argparse
import hashlib
import sys
import urllib.request
import zipfile
from pathlib import Path


WHEELS = [
    (
        "ctranslate2-4.8.4-cp314-cp314-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl",
        "https://files.pythonhosted.org/packages/0d/f4/d14d4ad8d1eac0e49aafcc11ff9ec83ac5758ae23acc526f15b73c980c19/ctranslate2-4.8.4-cp314-cp314-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl",
        "e838fb78e8db9daee96187a08c3d0839bf45bd4853afda839f3b4b4979336ed6",
    ),
    (
        "sentencepiece-0.2.2-cp314-cp314-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl",
        "https://files.pythonhosted.org/packages/24/b3/718847349da7b25c8220ed86d85b89080af94740b2d87a59198104ae5c51/sentencepiece-0.2.2-cp314-cp314-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl",
        "8d44b20234905ff022b7d535f79d1f823ad7670c9851cc4f03cdc34787cdb3ab",
    ),
    (
        "langdetect-1.0.9-py2-none-any.whl",
        "https://files.pythonhosted.org/packages/d0/04/31f2f175c475c0274a7fb20552e18c4f5b1ef04b211dd4d7301ca6bf4534/langdetect-1.0.9-py2-none-any.whl",
        "7cbc0746252f19e76f77c0b1690aadf01963be835ef0cd4b56dddf2a8f1dfc2a",
    ),
    (
        "six-1.17.0-py2.py3-none-any.whl",
        "https://files.pythonhosted.org/packages/b7/ce/149a00dd41f10bc29e5921b496af8b574d8413afcd5e30dfa0ed46c2cc5e/six-1.17.0-py2.py3-none-any.whl",
        "4721f391ed90541fddacab5acf947aa0d3dc7d27b2e1e8eda2be8970586c3274",
    ),
    (
        "pyyaml-6.0.3-cp314-cp314-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl",
        "https://files.pythonhosted.org/packages/88/f9/16491d7ed2a919954993e48aa941b200f38040928474c9e85ea9e64222c3/pyyaml-6.0.3-cp314-cp314-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl",
        "c458b6d084f9b935061bc36216e8a69a7e293a2f1e68bf956dcd9e6cbcd143f5",
    ),
    (
        "numpy-2.5.4-cp314-cp314-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl",
        "https://files.pythonhosted.org/packages/16/d5/928078d2b28f26829b138b4a6c3980045022fb409f570657a224ae60ef4e/numpy-2.5.4-cp314-cp314-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl",
        "d549420b8858885cea8838a727842249218b9c1da24dd517e25c9c7a948310a3",
    ),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prefix", required=True, help="Flatpak build prefix or staging root")
    parser.add_argument("--download", action="store_true", help="Download pinned wheels (manual build)")
    parser.add_argument("--wheel-dir", help="Directory containing wheels already fetched by flatpak-builder")
    args = parser.parse_args()

    if sys.version_info[:2] != (3, 14):
        raise SystemExit(
            f"The pinned CTranslate2 wheels require Python 3.14; got {sys.version_info.major}.{sys.version_info.minor}."
        )
    prefix = Path(args.prefix)
    site = prefix / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    site.mkdir(parents=True, exist_ok=True)
    wheel_dir = Path(args.wheel_dir) if args.wheel_dir else Path.cwd()

    for filename, url, expected_hash in WHEELS:
        wheel = wheel_dir / filename
        if args.download:
            if not wheel.exists() or hashlib.sha256(wheel.read_bytes()).hexdigest() != expected_hash:
                wheel_dir.mkdir(parents=True, exist_ok=True)
                request = urllib.request.Request(url, headers={"User-Agent": "LoopsGTK4/1.0"})
                with urllib.request.urlopen(request, timeout=120) as response:
                    data = response.read()
                digest = hashlib.sha256(data).hexdigest()
                if digest != expected_hash:
                    raise SystemExit(f"SHA-256 mismatch for {filename}")
                wheel.write_bytes(data)
        if not wheel.is_file():
            raise SystemExit(f"Required wheel not found: {wheel}")
        digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
        if digest != expected_hash:
            raise SystemExit(f"SHA-256 mismatch for {filename}")
        with zipfile.ZipFile(wheel) as archive:
            archive.extractall(site)
    # The staged extension modules need their normal executable/readable bits.
    for path in site.rglob("*.so"):
        path.chmod(path.stat().st_mode | 0o555)
    print(f"Installed pinned local translation dependencies under {site}")


if __name__ == "__main__":
    main()
