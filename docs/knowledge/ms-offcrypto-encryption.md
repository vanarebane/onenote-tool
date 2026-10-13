# How OneNote section-password encryption actually works

Microsoft's own [MS-ONESTORE] spec says a password-protected section's
`ObjectDataEncryptionKeyV2FNDX` node's data "MUST be ignored." That's not a
security boundary — it's Microsoft declining to document something that
turns out to be built from an ordinary, already-public format. Everything
below was confirmed **empirically, end-to-end, against a real
password-protected section** (known password, and a byte-identical
unprotected copy of the same pages to diff against) — not inferred from the
(silent) spec.

## The key: an ordinary MS-OFFCRYPTO Agile Encryption descriptor

The bytes the `ObjectDataEncryptionKeyV2FNDX` node's out-of-line reference
points at are — after stripping an 8-byte header, an 8-byte footer, and a
small (24-byte, still-unidentified but ignorable) binary prefix — a
byte-for-byte ordinary **ECMA-376/MS-OFFCRYPTO Agile Encryption**
`<encryption>` XML descriptor. This is the exact same format used for
password-protected `.docx`/`.xlsx`/`.pptx` files.

Key derivation is exactly [MS-OFFCRYPTO] 2.3.4.11–2.3.4.13:

```
h0 = HASH(saltValue + UTF16LE(password))
hN = HASH(LE32(n) + h[n-1])   for n in 0..spinCount-1     (spinCount is typically 100,000)
key_for(purpose) = HASH(hN + purpose_block)[:keyBytes]
```

Three fixed 8-byte `purpose_block` values (from the spec) derive three
different keys from the same spun hash: one to decrypt
`encryptedVerifierHashInput`, one for `encryptedVerifierHashValue` (compare
`HASH(verifierHashInput)` against the decrypted `verifierHashValue` — a
match means the password is correct), and one to unwrap
`encryptedKeyValue` into the real content key. See `decrypt.py`'s
`derive_and_verify_key`. The test file uses SHA-512 and AES-256-CBC.

## What is encrypted (the OneNote-specific, undocumented part)

Almost nothing structural is. The Header, every FileNodeList, every object
declaration (jcid, oid, chunk reference) and every object's
OIDs/OSIDs/ContextIDs compact-id streams are plaintext, exactly as in an
unprotected file. Two kinds of payload are encrypted, each as its **own
independent AES-CBC stream** with the same fixed IV:

```
IV = HASH(keyData.saltValue + LE32(0))[:blockSize]
```

(`keyData.saltValue`, not the password key-encryptor's salt.)

### 1. Each object's PropertySet body

An unprotected `ObjectSpaceObjectPropSet` is
`[OIDs][OSIDs?][ContextIDs?][PropertySet]`. In a protected section the
PropertySet is replaced by a length-prefixed ciphertext:

```
[OIDs][OSIDs?][ContextIDs?]   plaintext, unchanged
uint32 cb                     ciphertext length, a multiple of the block size
cb bytes                      AES-CBC ciphertext
(object's chunk may end with a few unused bytes, typically 4 zero bytes)
```

Decrypted, the `cb` bytes are:

```
16 bytes   random block (differs per object even for identical content —
           acts as a nonce, since the IV is fixed; discard it)
uint16     padLen
...        the PropertySet, exactly as an unprotected file stores it
padLen     padding bytes
```

Verified against all 2203 object declarations in the test section: every
one parses as a PropertySet with pyOneNote's own reader, and every one ends
exactly `padLen` bytes before the end of the plaintext. The decrypted
PropertySet references the plaintext ID streams the normal way, so OID /
ObjectSpaceID / ContextID properties (ParagraphStyle, PictureContainer,
AuthorOriginal, ...) resolve with no special handling.

Implementation: `decrypt.decrypt_object_body` plus
`_patches._ospropset_init`, which reads the ID streams from the file, reads
`cb` and the ciphertext, and builds the PropertySet from the decrypted
bytes. If a body doesn't have the encrypted shape it's read as plaintext.

### 2. Each embedded file's payload

A `FileDataStoreObject` keeps its 36-byte header (guidHeader, cbLength, ...)
in plaintext. The `cbLength` bytes after it are one CBC stream, same IV,
**no** random first block: the plaintext starts with a uint64 true content
size, then the file bytes (`cbLength` = that size + 8, padded to the block
size; e.g. `3450128 == ceil((3450108+8)/16)*16`). See
`decrypt.decrypt_file_payload`.

## Result

With both in place, the protected test section converts to exactly the same
output as its unprotected twin: same 20 pages, same block HTML (text,
formatting, lists, tables, links, YouTube embeds, author attribution), same
image and attachment bytes, same floating-box regions, dates, background
and page levels.

## History: the earlier, wrong model

Earlier versions of this tool assumed the whole `.one` file was one OOXML
style "EncryptedPackage" (4096-byte segments, IV per segment index) and
decrypted the entire file into a shadow buffer, reading PropertySets from
it at their file offsets. That model is wrong, but CBC is self-synchronising
after one block, so it produced *partially* correct plaintext: real
property bodies showed up "a short, variable distance" past where they
should start, which led to a heuristic forward scan for plausible-looking
PropertySets, a title-rescue scan, and a structural page-recovery fallback.
Those recovered roughly half of protected content and misplaced some of
it. The per-object framing above replaces all of that.
