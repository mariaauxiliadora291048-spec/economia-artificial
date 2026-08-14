from __future__ import annotations

import base64
import binascii
import ctypes
import json
import os
from pathlib import Path


class LocalSecretStore:
    """Small local store protected with the current Windows user's DPAPI key."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._values = self._load()

    @property
    def available(self) -> bool:
        return os.name == "nt"

    def get(self, key: str) -> str | None:
        ciphertext = self._values.get(key)
        return _unprotect(ciphertext) if ciphertext else None

    def set(self, key: str, value: str) -> bool:
        ciphertext = _protect(value)
        if ciphertext is None:
            return False
        self._values[key] = ciphertext
        self._persist()
        return True

    def has(self, key: str) -> bool:
        return key in self._values and self.get(key) is not None

    def _load(self) -> dict[str, str]:
        if not self._path.exists():
            return {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return {str(key): str(value) for key, value in raw.items()}

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self._path.with_suffix(".tmp")
        temporary_path.write_text(
            json.dumps(self._values, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary_path.replace(self._path)


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _protect(value: str) -> str | None:
    if os.name != "nt":
        return None
    return _crypt(value.encode("utf-8"), protect=True)


def _unprotect(value: str) -> str | None:
    if os.name != "nt":
        return None
    try:
        decrypted = _crypt(base64.b64decode(value.encode("ascii")), protect=False)
    except (binascii.Error, UnicodeEncodeError, ValueError):
        return None
    return base64.b64decode(decrypted).decode("utf-8") if decrypted else None


def _crypt(value: bytes, *, protect: bool) -> str | None:
    try:
        source = ctypes.create_string_buffer(value)
        input_blob = _DataBlob(len(value), ctypes.cast(source, ctypes.POINTER(ctypes.c_byte)))
        output_blob = _DataBlob()
        crypt32 = ctypes.windll.crypt32
        operation = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
        succeeded = operation(
            ctypes.byref(input_blob),
            None,
            None,
            None,
            None,
            0,
            ctypes.byref(output_blob),
        )
        if not succeeded:
            return None
        try:
            result = ctypes.string_at(output_blob.pbData, output_blob.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(output_blob.pbData)
    except (AttributeError, OSError):
        return None
    return base64.b64encode(result).decode("ascii")
