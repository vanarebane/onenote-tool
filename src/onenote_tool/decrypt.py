"""Decrypt a password-protected OneNote section (.one file).

Contrary to what Microsoft's own [MS-ONESTORE] spec claims ("Encryption
Data... MUST be ignored" — they simply declined to document it), a
password-protected section is NOT using some bespoke, unrecoverable OneNote
scheme. The `ObjectDataEncryptionKeyV2FNDX` FileNode's out-of-line blob is a
plain **ECMA-376/MS-OFFCRYPTO Agile Encryption** `<encryption>` XML descriptor
— byte-for-byte the SAME format used for password-protected .docx/.xlsx/.pptx
— preceded by a small (24-byte, still unidentified/ignorable) binary header.
Confirmed empirically end-to-end against a real protected section (known
password, known plaintext): password verifies against the file's own stored
verifier hash, and the derived key correctly decrypts real content ("Password"
page title, a real person's name from author metadata, etc).

Key derivation is exactly MS-OFFCRYPTO 2.3.4.11-2.3.4.13 (Agile Encryption):
    h0 = HASH(saltValue + UTF16LE(password))
    hN = HASH(LE32(n) + h[n-1])  for n in 0..spinCount-1
    key_for(blockKey) = HASH(hN + blockKey)[:keyBytes]
used first to verify the password (decrypt encryptedVerifierHashInput/Value
and compare), then to unwrap `encryptedKeyValue` into the real file/content
encryption key.

The one OneNote-specific (undocumented) part: **the entire .one file, from
byte 0, is one contiguous "EncryptedPackage" stream** in the same shape as
OOXML's own EncryptedPackage — split into independently-IV'd 4096-byte
segments (`IV = SHA512(keyData.saltValue + LE32(segment_index))[:blockSize]`,
segment_index = absolute file offset // 4096, CBC-chained within each
segment) — even though large parts of the file (the Header, FileNodeList
skeleton, object declarations/jcids) are ALSO independently readable as
plaintext directly from the raw bytes. Only PropertySet BODIES (the actual
per-object content: text, titles, ...) turn out to be real ciphertext when
read from the raw file; decrypting the surrounding structural bytes (which
were never really "for" decryption) just produces noise nobody reads. See
`onenote_tool._patches`'s `_propset_init` patch, which redirects ONLY
`PropertySet.__init__` reads through this decrypted shadow buffer, at the
same file offset, leaving every other structural read on the original file.
"""

from __future__ import annotations

import hashlib
import struct
import xml.etree.ElementTree as ET

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

_ENCRYPTION_NS = '{http://schemas.microsoft.com/office/2006/encryption}'
_PASSWORD_NS = '{http://schemas.microsoft.com/office/2006/keyEncryptor/password}'

# MS-OFFCRYPTO 2.3.4.11: fixed byte blocks appended to the spin-iterated hash
# before deriving each of the three purpose-specific keys.
_BLOCK_VERIFIER_HASH_INPUT = bytes([0xFE, 0xA7, 0xD2, 0x76, 0x3B, 0x4B, 0x9E, 0x79])
_BLOCK_VERIFIER_HASH_VALUE = bytes([0xD7, 0xAA, 0x0F, 0x6D, 0x30, 0x61, 0x34, 0x4E])
_BLOCK_KEY_VALUE = bytes([0x14, 0x6E, 0x0B, 0xE7, 0xAB, 0xAC, 0xD0, 0xD6])

SEGMENT_SIZE = 4096


class EncryptionInfo:
    """Parsed `<encryption>` descriptor from an ObjectDataEncryptionKeyV2FNDX blob."""

    def __init__(self, xml_bytes: bytes):
        start = xml_bytes.find(b'<?xml')
        end = xml_bytes.rfind(b'</encryption>')
        if start == -1 or end == -1:
            raise ValueError('Not a recognizable Agile Encryption descriptor')
        root = ET.fromstring(xml_bytes[start:end + len('</encryption>')])
        key_data = root.find(f'{_ENCRYPTION_NS}keyData')
        key_encryptor = root.find(f'.//{_PASSWORD_NS}encryptedKey')
        if key_data is None or key_encryptor is None:
            raise ValueError('Missing keyData/encryptedKey in encryption descriptor')
        self.key_data = key_data.attrib
        self.key_encryptor = key_encryptor.attrib

    @property
    def package_salt(self) -> bytes:
        """Salt used for the whole-file segment IVs (keyData, NOT the password encryptor's own salt)."""
        return _b64(self.key_data['saltValue'])

    @property
    def hash_algorithm(self) -> str:
        return self.key_encryptor['hashAlgorithm'].lower().replace('-', '')

    @property
    def block_size(self) -> int:
        return int(self.key_encryptor['blockSize'])


def _b64(value: str) -> bytes:
    import base64
    return base64.b64decode(value)


def extract_encryption_info(blob: bytes) -> EncryptionInfo:
    """`blob` is the raw bytes referenced by an ObjectDataEncryptionKeyV2FNDX
    node's `ref` (Header(8) + EncryptionData(variable) + Footer(8) framing —
    this strips those and the small binary prefix ahead of the XML itself)."""
    return EncryptionInfo(blob[8:-8])


def _hash(algo: str, *parts: bytes) -> bytes:
    h = hashlib.new(algo)
    for p in parts:
        h.update(p)
    return h.digest()


def _spin_hash(info: EncryptionInfo, password: str) -> bytes:
    algo = info.hash_algorithm
    salt = _b64(info.key_encryptor['saltValue'])
    spin_count = int(info.key_encryptor['spinCount'])
    h = _hash(algo, salt, password.encode('utf-16-le'))
    for i in range(spin_count):
        h = _hash(algo, struct.pack('<I', i), h)
    return h


def _derive(info: EncryptionInfo, spin_hash: bytes, block_key: bytes) -> bytes:
    key_bytes = int(info.key_encryptor['keyBits']) // 8
    return _hash(info.hash_algorithm, spin_hash, block_key)[:key_bytes]


def _aes_cbc_decrypt(key: bytes, iv: bytes, ciphertext: bytes) -> bytes:
    n = (len(ciphertext) // 16) * 16
    if n == 0:
        return b''
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    d = cipher.decryptor()
    return d.update(ciphertext[:n]) + d.finalize()


def derive_and_verify_key(info: EncryptionInfo, password: str) -> bytes | None:
    """Returns the unwrapped file-encryption key if `password` is correct
    (verified against the descriptor's own stored hash), else None."""
    spin_hash = _spin_hash(info, password)
    salt = _b64(info.key_encryptor['saltValue'])

    key1 = _derive(info, spin_hash, _BLOCK_VERIFIER_HASH_INPUT)
    verifier_hash_input = _aes_cbc_decrypt(key1, salt, _b64(info.key_encryptor['encryptedVerifierHashInput']))

    key2 = _derive(info, spin_hash, _BLOCK_VERIFIER_HASH_VALUE)
    stored_hash = _aes_cbc_decrypt(key2, salt, _b64(info.key_encryptor['encryptedVerifierHashValue']))

    computed_hash = _hash(info.hash_algorithm, verifier_hash_input)
    if computed_hash != stored_hash[:len(computed_hash)]:
        return None

    key3 = _derive(info, spin_hash, _BLOCK_KEY_VALUE)
    return _aes_cbc_decrypt(key3, salt, _b64(info.key_encryptor['encryptedKeyValue']))


def _segment_iv(package_salt: bytes, segment_index: int, hash_algorithm: str, block_size: int) -> bytes:
    return _hash(hash_algorithm, package_salt, struct.pack('<I', segment_index))[:block_size]


def decrypt_package(raw: bytes, info: EncryptionInfo, file_key: bytes) -> bytes:
    """Decrypts the WHOLE file as one segmented EncryptedPackage stream (see
    module docstring — most of the output is never read; only the ranges
    PropertySet.__init__ actually consumes matter)."""
    package_salt = info.package_salt
    algo = info.hash_algorithm
    block_size = info.block_size
    out = bytearray(len(raw))
    for seg_start in range(0, len(raw), SEGMENT_SIZE):
        seg_end = min(seg_start + SEGMENT_SIZE, len(raw))
        chunk = raw[seg_start:seg_end]
        n = (len(chunk) // 16) * 16
        if n == 0:
            continue
        iv = _segment_iv(package_salt, seg_start // SEGMENT_SIZE, algo, block_size)
        out[seg_start:seg_start + n] = _aes_cbc_decrypt(file_key, iv, chunk[:n])
    return bytes(out)


def decrypt_file_payload(ciphertext: bytes, info: EncryptionInfo, file_key: bytes) -> bytes:
    """Decrypts a `FileDataStoreObject` payload (the bytes after its 36-byte
    plaintext header — see onenote_tool._patches's `_filedatastore_init`).

    NOT the same segmentation as `decrypt_package`: an embedded file's
    payload is its own single CBC-chained stream with exactly ONE IV
    (segment index 0), the whole payload long — confirmed empirically by
    finding where a real, large (3.5MB, 876-segment-sized) recovered JPEG
    broke when decrypted with `decrypt_package`'s periodic per-4096-byte IV
    reset: the break was always the first AES block of a new 4096-byte
    span, i.e. `decrypt_package` was re-randomizing the IV at points the
    real encryption never did, corrupting one block every 4096 bytes and
    (since JPEG's entropy coding loses bit-sync permanently after any
    corrupted byte) rendering everything past the first break as noise.
    Removing the periodic reset — one Cipher, one IV, chained straight
    through — decrypts the same file byte-perfect (verified via PIL:
    correct 4000x3000 JPEG, no decode error)."""
    iv = _segment_iv(info.package_salt, 0, info.hash_algorithm, info.block_size)
    return _aes_cbc_decrypt(file_key, iv, ciphertext)
