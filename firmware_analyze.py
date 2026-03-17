#!/usr/bin/env python3
"""
WakeBand Firmware Analyzer

Analyzes the db_update_data.bin firmware file from the Homedics WakeBand.
Performs structural analysis, entropy measurement, encryption detection,
and attempts decryption with known/derived keys.

The firmware is likely for a Telink TLSR82xx BLE SoC (common in wristbands).
Telink firmware has "KNLT" magic at offset 8 in plaintext.

Usage:
    python3 firmware_analyze.py db_update_data.bin
    python3 firmware_analyze.py db_update_data.bin --key <hex_key>
    python3 firmware_analyze.py db_update_data.bin --key-file keys.txt
"""

import argparse
import math
import struct
import sys
from collections import Counter
from pathlib import Path

try:
    from Crypto.Cipher import AES
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False


def calc_entropy(data: bytes) -> float:
    """Calculate Shannon entropy in bits per byte."""
    if not data:
        return 0.0
    counter = Counter(data)
    length = len(data)
    return -sum((c / length) * math.log2(c / length) for c in counter.values())


def find_strings(data: bytes, min_length: int = 6) -> list[tuple[int, str]]:
    """Extract printable ASCII strings from binary data."""
    results = []
    current = []
    start = 0
    for i, b in enumerate(data):
        if 32 <= b < 127:
            if not current:
                start = i
            current.append(chr(b))
        else:
            if len(current) >= min_length:
                results.append((start, "".join(current)))
            current = []
    if len(current) >= min_length:
        results.append((start, "".join(current)))
    return results


def autocorrelation_analysis(data: bytes, max_period: int = 256, sample_size: int = 20000) -> dict[int, int]:
    """Detect repeating XOR key length via autocorrelation."""
    results = {}
    n = min(sample_size, len(data) // 2)
    for period in range(1, max_period + 1):
        if period >= len(data) - n:
            break
        matches = sum(1 for i in range(n) if data[i] == data[i + period])
        results[period] = matches
    return results


def try_xor_decrypt(data: bytes, key: bytes) -> bytes:
    """Decrypt data with repeating XOR key."""
    key_len = len(key)
    return bytes(data[i] ^ key[i % key_len] for i in range(len(data)))


def try_aes_decrypt(data: bytes, key: bytes, iv: bytes, mode: str = "CBC") -> bytes:
    """Attempt AES decryption."""
    if not HAS_CRYPTO:
        raise ImportError("pycryptodome required: pip install pycryptodome")
    if mode == "CBC":
        cipher = AES.new(key, AES.MODE_CBC, iv)
    elif mode == "ECB":
        cipher = AES.new(key, AES.MODE_ECB)
    elif mode == "CTR":
        ctr_nonce = iv[:8]
        cipher = AES.new(key, AES.MODE_CTR, nonce=ctr_nonce)
    else:
        raise ValueError(f"Unknown mode: {mode}")
    return cipher.decrypt(data)


def check_telink_signature(data: bytes) -> bool:
    """Check if decrypted data has Telink 'KNLT' signature at offset 8."""
    if len(data) < 12:
        return False
    return data[8:12] == b"KNLT" or data[8:12] == b"TLNK"


def analyze_firmware(filepath: str, key_hex: str = None, key_file: str = None):
    """Main firmware analysis routine."""
    data = Path(filepath).read_bytes()
    size = len(data)

    print(f"{'=' * 70}")
    print(f" WakeBand Firmware Analysis: {filepath}")
    print(f"{'=' * 70}")

    # --- Basic info ---
    print(f"\n[1] BASIC INFO")
    print(f"  File size: {size} bytes ({size / 1024:.1f} KB)")
    print(f"  SHA256: ", end="")
    import hashlib
    print(hashlib.sha256(data).hexdigest())

    # --- Structure detection ---
    print(f"\n[2] STRUCTURE DETECTION")
    # Check alignment - the file is 180242 bytes, and 180242 - 18 = 180224 = 0x2C000
    for header_size in [0, 2, 4, 8, 16, 18, 32, 64]:
        payload_size = size - header_size
        if payload_size > 0 and payload_size % 16 == 0:
            blocks = payload_size // 16
            print(f"  Header={header_size}B + Payload={payload_size}B ({payload_size // 1024}KB, {blocks} AES blocks)")

    # Best candidate: 18-byte header (size-18 = 180224 = 0x2C000 exactly)
    header = data[:18]
    payload = data[18:]
    print(f"\n  Best match: 18-byte header + {len(payload)}-byte payload")
    print(f"  Header hex: {header.hex()}")
    print(f"  Possible structure:")
    print(f"    Bytes 0-1:  {header[:2].hex()} (type/flags/checksum?)")
    print(f"    Bytes 2-17: {header[2:18].hex()} (AES IV / nonce?)")

    # --- Entropy analysis ---
    print(f"\n[3] ENTROPY ANALYSIS")
    overall = calc_entropy(data)
    print(f"  Overall entropy: {overall:.4f} bits/byte")
    print(f"  Interpretation: {'Encrypted/compressed (near maximum)' if overall > 7.9 else 'Contains structure' if overall < 7.5 else 'High entropy'}")

    # Entropy by region
    regions = [
        ("Header (0-17)", data[:18]),
        ("First 1KB", data[:1024]),
        ("Middle 1KB", data[size // 2 : size // 2 + 1024]),
        ("Last 1KB", data[-1024:]),
    ]
    for name, region in regions:
        print(f"  {name}: {calc_entropy(region):.4f}")

    # --- Autocorrelation (XOR key detection) ---
    print(f"\n[4] AUTOCORRELATION ANALYSIS (XOR key detection)")
    autocorr = autocorrelation_analysis(data)
    expected = len(data) / 256  # Expected matches for random data
    print(f"  Expected matches for random data: ~{expected:.0f}")
    significant = {p: m for p, m in autocorr.items() if m > expected * 2}
    if significant:
        print(f"  Significant periods (>2x expected):")
        for period in sorted(significant.keys()):
            ratio = significant[period] / expected
            print(f"    Period {period:4d}: {significant[period]:5d} matches ({ratio:.1f}x expected)")
    else:
        print(f"  No significant XOR key period detected.")

    # --- ECB block analysis ---
    print(f"\n[5] BLOCK REPETITION ANALYSIS")
    for block_size in [16, 32]:
        blocks = [payload[i : i + block_size] for i in range(0, len(payload), block_size)]
        unique = len(set(tuple(b) for b in blocks if len(b) == block_size))
        total = len([b for b in blocks if len(b) == block_size])
        print(f"  {block_size}-byte blocks: {total} total, {unique} unique ({100 * unique / total:.1f}% unique)")
        if unique < total:
            print(f"    ** {total - unique} repeated blocks detected — possible ECB mode **")

    # --- Strings ---
    print(f"\n[6] EMBEDDED STRINGS")
    strings = find_strings(data)
    if strings:
        for offset, s in strings[:20]:
            print(f"  0x{offset:06x}: \"{s}\"")
        if len(strings) > 20:
            print(f"  ... and {len(strings) - 20} more")
    else:
        print(f"  No readable strings found (confirms encryption)")

    # --- Decryption attempts ---
    print(f"\n[7] DECRYPTION ATTEMPTS")

    keys_to_try = []

    if key_hex:
        keys_to_try.append(("User-provided", bytes.fromhex(key_hex)))

    if key_file:
        with open(key_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    try:
                        keys_to_try.append((f"File: {line[:16]}...", bytes.fromhex(line)))
                    except ValueError:
                        # Try as ASCII key
                        key_bytes = line.encode()[:16].ljust(16, b"\x00")
                        keys_to_try.append((f"File(ASCII): {line[:16]}", key_bytes))

    # Always try some common keys
    builtin_keys = [
        ("All zeros", b"\x00" * 16),
        ("All 0xFF", b"\xff" * 16),
        ("Sequential", bytes(range(16))),
        ("WakeBand", b"WakeBand\x00\x00\x00\x00\x00\x00\x00\x00"),
        ("homedics", b"homedics\x00\x00\x00\x00\x00\x00\x00\x00"),
        ("greatpower", b"greatpower\x00\x00\x00\x00\x00\x00"),
        ("0123456789abcdef", b"0123456789abcdef"),
        ("1234567890123456", b"1234567890123456"),
    ]
    keys_to_try.extend(builtin_keys)

    iv = header[2:18]
    found_key = False

    for name, key in keys_to_try:
        if len(key) != 16:
            continue

        for mode in ["CBC", "ECB"]:
            try:
                if mode == "CBC":
                    dec = try_aes_decrypt(payload[:16], key, iv, mode)
                else:
                    dec = try_aes_decrypt(data[:16], key, b"\x00" * 16, mode)

                if check_telink_signature(dec):
                    print(f"  *** KEY FOUND ({mode})! ***")
                    print(f"  Key name: {name}")
                    print(f"  Key hex: {key.hex()}")
                    print(f"  Decrypted first block: {dec.hex()}")
                    found_key = True

                    # Full decryption
                    if mode == "CBC":
                        full_dec = try_aes_decrypt(payload, key, iv, mode)
                    else:
                        full_dec = try_aes_decrypt(data, key, b"\x00" * 16, mode)

                    outfile = filepath.replace(".bin", "_decrypted.bin")
                    Path(outfile).write_bytes(full_dec)
                    print(f"  Decrypted firmware saved to: {outfile}")
                    break
            except Exception:
                pass

        if found_key:
            break

    if not found_key:
        print(f"  No key found with built-in attempts.")
        print(f"\n  To decrypt, you need the AES key from the WakeBand Android APK.")
        print(f"  Steps to extract the key:")
        print(f"    1. Download the APK: com.homedics.bracelet")
        print(f"    2. Decompile with: jadx com.homedics.bracelet.apk")
        print(f"    3. Search for AES key in Java source:")
        print(f'       grep -r "AES" --include="*.java" .')
        print(f'       grep -r "SecretKey\\|Cipher\\|decrypt" --include="*.java" .')
        print(f"    4. Run this tool with: --key <hex_key>")

    # --- Summary ---
    print(f"\n{'=' * 70}")
    print(f" FIRMWARE SUMMARY")
    print(f"{'=' * 70}")
    print(f"  Device: Homedics WakeBand (silent vibrating alarm wristband)")
    print(f"  App package: com.homedics.bracelet")
    print(f"  Firmware version: 1.8.0 (from WakeBandFirmwareVersion.txt)")
    print(f"  File: {filepath} ({size} bytes)")
    print(f"  Structure: 2-byte prefix + 16-byte IV + {len(payload)}-byte AES-encrypted payload")
    print(f"  Payload size: {len(payload)} bytes = {len(payload) // 1024}KB = 0x{len(payload):X}")
    print(f"  AES blocks: {len(payload) // 16}")
    print(f"  Likely SoC: Telink TLSR82xx (BLE, common in wristbands)")
    print(f"  Encryption: AES-128-CBC (key in APK)")
    print(f"  Telink magic: 'KNLT' expected at plaintext offset 8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="WakeBand Firmware Analyzer")
    parser.add_argument("firmware", help="Path to firmware .bin file")
    parser.add_argument("--key", help="AES-128 key in hex (32 hex chars)")
    parser.add_argument("--key-file", help="File with one hex key per line")
    args = parser.parse_args()

    analyze_firmware(args.firmware, args.key, args.key_file)
