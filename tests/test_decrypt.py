import base64

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from onenote_tool.decrypt import (
    EncryptionInfo,
    _aes_cbc_decrypt,
    _segment_iv,
    decrypt_file_payload,
    decrypt_object_body,
    extract_encryption_info,
)

# A minimal, syntactically-valid Agile Encryption <encryption> descriptor —
# values are dummy base64 (not a real derivable key), just enough structure
# for EncryptionInfo to parse the fields derive_and_verify_key
# actually read. Real end-to-end decrypt correctness is covered by the
# real-file smoke test (tests/test_smoke_local_file.py), which needs an
# actual protected section to mean anything.
_SAMPLE_XML = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<encryption xmlns="http://schemas.microsoft.com/office/2006/encryption"
            xmlns:p="http://schemas.microsoft.com/office/2006/keyEncryptor/password">
  <keyData saltValue="AAAAAAAAAAAAAAAAAAAAAA==" hashAlgorithm="SHA512" blockSize="16" keyBits="256"/>
  <keyEncryptors>
    <keyEncryptor uri="http://schemas.microsoft.com/office/2006/keyEncryptor/password">
      <p:encryptedKey spinCount="100000" saltValue="AAAAAAAAAAAAAAAAAAAAAA=="
                       hashAlgorithm="SHA-512" blockSize="16" keyBits="256"
                       encryptedVerifierHashInput="AAAAAAAAAAAAAAAAAAAAAA=="
                       encryptedVerifierHashValue="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
                       encryptedKeyValue="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="/>
    </keyEncryptor>
  </keyEncryptors>
</encryption>"""


def test_encryption_info_parses_key_data_and_encryptor():
    info = EncryptionInfo(_SAMPLE_XML)
    assert info.hash_algorithm == 'sha512'  # lowercased, hyphen stripped
    assert info.block_size == 16
    assert info.package_salt == base64.b64decode('AAAAAAAAAAAAAAAAAAAAAA==')


def test_extract_encryption_info_strips_header_and_footer():
    # ObjectDataEncryptionKeyV2FNDX framing: 8-byte header + blob + 8-byte
    # footer around the XML — extract_encryption_info strips exactly that.
    blob = b'HEADER!!' + _SAMPLE_XML + b'FOOTER!!'
    info = extract_encryption_info(blob)
    assert info.block_size == 16


def test_encryption_info_rejects_garbage():
    with pytest.raises(ValueError):
        EncryptionInfo(b'not xml at all')


def test_segment_iv_differs_per_segment_index():
    salt = b'\x00' * 16
    iv0 = _segment_iv(salt, 0, 'sha512', 16)
    iv1 = _segment_iv(salt, 1, 'sha512', 16)
    assert len(iv0) == 16
    assert iv0 != iv1


def test_aes_cbc_decrypt_round_trip():
    key = bytes(range(32))  # AES-256 key
    iv = bytes(range(16))
    plaintext = (b'0123456789ABCDEF' * 3)  # 48 bytes, 3 full blocks
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    ciphertext = encryptor.update(plaintext) + encryptor.finalize()
    assert _aes_cbc_decrypt(key, iv, ciphertext) == plaintext


def test_aes_cbc_decrypt_empty_input():
    assert _aes_cbc_decrypt(bytes(32), bytes(16), b'') == b''


def _encrypt(info, key, plaintext):
    iv = _segment_iv(info.package_salt, 0, info.hash_algorithm, info.block_size)
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return encryptor.update(plaintext) + encryptor.finalize()


def test_decrypt_object_body_strips_random_block_and_padding():
    # Plaintext framing: 16-byte random block, uint16 padLen, PropertySet,
    # padLen bytes of padding (see decrypt.py's module docstring).
    info = EncryptionInfo(_SAMPLE_XML)
    key = bytes(range(32))
    body = b'\x03\x00' + b'P' * 24
    pad = (-(18 + len(body))) % 16
    ciphertext = _encrypt(info, key, b'R' * 16 + pad.to_bytes(2, 'little') + body + b'\x00' * pad)
    assert decrypt_object_body(ciphertext, info, key) == body


def test_decrypt_object_body_rejects_wrong_shape():
    info = EncryptionInfo(_SAMPLE_XML)
    key = bytes(range(32))
    assert decrypt_object_body(b'', info, key) is None
    assert decrypt_object_body(b'x' * 20, info, key) is None  # not block-aligned
    # padLen larger than the plaintext
    assert decrypt_object_body(_encrypt(info, key, b'R' * 16 + b'\xff\xff' + b'\x00' * 14), info, key) is None


def test_decrypt_file_payload_has_no_random_block():
    info = EncryptionInfo(_SAMPLE_XML)
    key = bytes(range(32))
    plaintext = (5).to_bytes(8, 'little') + b'hello' + b'\x00' * 3
    assert decrypt_file_payload(_encrypt(info, key, plaintext), info, key) == plaintext
