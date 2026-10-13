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

The OneNote-specific (undocumented) part is WHAT gets encrypted. Everything
structural — the Header, FileNodeLists, object declarations/jcids, and each
object's OIDs/OSIDs/ContextIDs compact-id streams — stays plaintext. Two
kinds of payload are encrypted, each as its own independent AES-CBC stream
with the same IV, `HASH(keyData.saltValue + LE32(0))[:blockSize]`:

- An ObjectSpaceObjectPropSet's PropertySet body. In place of the body the
  file holds `uint32 cb` followed by `cb` bytes of ciphertext (`cb` is a
  multiple of the block size). The plaintext is
  `[16-byte random block][uint16 padLen][PropertySet][padLen bytes padding]`
  — see `decrypt_object_body`. Verified against every one of 2203 objects in
  a real protected section: all parse as a PropertySet that ends exactly
  `padLen` bytes before the end of the plaintext.
- An embedded file's payload (`FileDataStoreObject`) — see
  `decrypt_file_payload`.
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
        """Salt used for the content IV (keyData, NOT the password encryptor's own salt)."""
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


def decrypt_object_body(ciphertext: bytes, info: EncryptionInfo, file_key: bytes) -> bytes | None:
    """Decrypts one ObjectSpaceObjectPropSet body (the `cb` ciphertext bytes
    that follow its plaintext ID streams and `uint32 cb`) and returns just
    the PropertySet bytes, or None if `ciphertext` doesn't have the shape of
    an encrypted body (wrong length, or a padding length that doesn't fit)."""
    if len(ciphertext) < 2 * info.block_size or len(ciphertext) % info.block_size:
        return None
    iv = _segment_iv(info.package_salt, 0, info.hash_algorithm, info.block_size)
    plain = _aes_cbc_decrypt(file_key, iv, ciphertext)
    pad, = struct.unpack_from('<H', plain, 16)
    end = len(plain) - pad
    if end < 18:
        return None
    return plain[18:end]


def decrypt_file_payload(ciphertext: bytes, info: EncryptionInfo, file_key: bytes) -> bytes:
    """Decrypts a `FileDataStoreObject` payload (the bytes after its 36-byte
    plaintext header — see onenote_tool._patches's `_filedatastore_init`).

    One CBC stream over the whole payload, same IV as `decrypt_object_body`
    (index 0), no periodic re-keying. Unlike a PropertySet body there's no
    random first block: the plaintext starts with a uint64 true content size
    (the payload is that size plus 8, padded to the block size).
    """
    iv = _segment_iv(info.package_salt, 0, info.hash_algorithm, info.block_size)
    return _aes_cbc_decrypt(file_key, iv, ciphertext)
