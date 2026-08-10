# How OneNote section-password encryption actually works

Microsoft's own [MS-ONESTORE] spec says a password-protected section's
`ObjectDataEncryptionKeyV2FNDX` node's data "MUST be ignored." That's not a
security boundary — it's Microsoft declining to document something that
turns out to be a completely ordinary, already-public format. Everything
below was confirmed **empirically, end-to-end, against a real
password-protected section** (known password, known plaintext content) —
not inferred from the (silent) spec.

## The blob is an ordinary MS-OFFCRYPTO Agile Encryption descriptor

The bytes the `ObjectDataEncryptionKeyV2FNDX` node's out-of-line reference
points at are — after stripping an 8-byte header, an 8-byte footer, and a
small (24-byte, still-unidentified but ignorable) binary prefix — a
byte-for-byte ordinary **ECMA-376/MS-OFFCRYPTO Agile Encryption**
`<encryption>` XML descriptor. This is the exact same format used for
password-protected `.docx`/`.xlsx`/`.pptx` files, fully publicly documented
by Microsoft (just never cross-referenced from the OneNote spec).

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
`encryptedKeyValue` into the real file/content encryption key. See
`decrypt.py`'s `derive_and_verify_key` — it's a direct, small
implementation of exactly this.

## The one genuinely OneNote-specific (undocumented) part

**The entire `.one` file, from byte 0, is treated as one contiguous
"EncryptedPackage" stream** — the same convention OOXML uses for its own
`EncryptedPackage`, split into independently-IV'd, CBC-chained 4096-byte
segments:

```
segment_index = absolute_file_offset // 4096
IV = HASH(keyData.saltValue + LE32(segment_index))[:blockSize]
```

This holds **even though most of the file is genuinely plaintext already**
— the Header, the FileNodeList skeleton, object declarations/jcids, and
every position/reference described in
[onenote-file-format.md](onenote-file-format.md) are all independently
readable straight off the raw bytes. Decrypting those structural regions
just produces noise nobody needs to read. **Only a PropertySet's own
property *values* turn out to be real ciphertext** when read from the raw
file. This tool's whole decryption strategy (`_patches.py`) follows
directly from that: build one decrypted "shadow buffer" for the whole file
once per parse, and redirect *only* `PropertySet.__init__`'s reads through
it (at the same file offset — encryption is length-preserving, so offsets
in the real file and the shadow buffer always agree) while every other
structural read stays on the original file.

### Embedded file payloads are a SEPARATE stream, not part of the above

This was a real bug, found and fixed by testing against an actual large
(3.5MB) recovered image: an embedded file's payload — the bytes after its
own 36-byte plaintext header inside a `FileDataStoreObject` — is **not**
part of the whole-file segmented stream above. It's independently
encrypted as its **own single, continuously-chained CBC stream**: **one**
IV (segment index 0, computed relative to the payload's own start, not the
absolute file offset), no periodic re-keying for the rest of the payload's
length.

Treating it as periodically-segmented (like the rest of the file) produces
a very specific, very recognizable corruption pattern: the first ~4KB or so
decrypts correctly (matching JFIF/JPEG headers and looking completely
valid), then the image degrades into visual noise from that point on. The
mechanism: a wrong IV corrupts only the *first* AES block of whatever
"segment" you (wrongly) define — but JPEG's entropy coding loses bit-sync
permanently after any single corrupted byte, so one wrong 16-byte block
early in a multi-megabyte payload cascades into "clean top, garbage
everything after" for the rest of the image. Removing the periodic
IV-reset — one `Cipher`, one IV, chained straight through the whole
payload — decrypts the same file byte-perfect (`decrypt.decrypt_file_payload`,
verified via PIL: a correct, undamaged JPEG, no decode error).

The decrypted payload's first 8 bytes are a little-endian `uint64` giving
the *true* content size — the file node's own `cbLength` field is that true
size padded up to the next 16-byte AES block boundary, plus this 8-byte
prefix (confirmed against a real file: `cbLength=3450128 ==
ceil((3450108+8)/16)*16`).

## OIDs/OSIDs/ContextIDs streams are ALSO structural plaintext

This was the single biggest recovery breakthrough this project made, and
it directly contradicted the assumption every earlier attempt had made.

An `ObjectSpaceObjectPropSet` reads three compact-ID streams (OIDs, OSIDs,
ContextIDs — used to resolve `ParagraphStyle`/`TextRunFormatting`/
`PictureContainer`/etc. *reference*-typed properties to the objects they
point at) **before** the PropertySet body itself. The natural assumption —
made and acted on for a long time before this was actually tested — was
that these streams live in the same encrypted region as everything else a
PropertySet reads, so they'd need the same shadow-buffer redirection as the
body.

That assumption is **wrong**. Reading these three streams through the
shadow (decrypted) buffer reliably produces "implausible stream count"
garbage (decrypting already-plaintext bytes with AES just produces noise)
— which for a long time looked like confirmation that OID resolution was
simply unavailable for protected content, full stop.

Testing the opposite directly — reading the same three streams from the
**raw, undecrypted** file, at the exact same object offset — instead
produces small, completely consistent, genuinely valid structures across a
dozen-plus unrelated real objects in a real file (`OIDs.Count=2`, no OSID
stream, no ContextID stream — 12 bytes total), whose resolved
`ExtendedGUID`s are real, plausible object references (the same base style
GUID recurring across many different text runs' `ParagraphStyle`, exactly
matching the shape you'd expect).

**These ID streams are structural, not content — plaintext even in a
protected section, exactly like every other position/type/identity value
described in [onenote-file-format.md](onenote-file-format.md).** They were
just never tested that way before, because the "PropertySet reads are all
encrypted" mental model, formed from getting the body itself right, was
never re-examined for the framing *around* the body.

Wiring this in (`_patches._read_plaintext_id_streams`, `_ReplayIDStream`)
is what makes `ParagraphStyle`/`TextRunFormatting`/`PictureContainer`
resolve to their real referenced objects at all for protected content, for
the first time — see
[recovery-techniques-log.md](recovery-techniques-log.md) for the two real
regressions this caused before it was made safe, and exactly how each was
fixed.

## The "bare body, no ID-stream prefix" scan (Attempt 2)

Even with the ID streams solved, one thing remains true and unresolved: a
protected object's **body** doesn't reliably decrypt starting exactly at
`ref.stp` the way an unprotected file's would (Attempt 1). Empirically, the
real, decryptable body is often a short, *variable* distance further into
the shadow buffer, with no ID-stream framing immediately before it —
scanning a small forward window (128 bytes) for the first position that
looks like a real `PropertySet` (a plausible property count whose property
IDs mostly resolve to real, known names — not just *a* plausible count,
which garbage can produce too) reliably finds it for most objects.

This scan is unavoidably heuristic, and widening its window carelessly is
a real, previously-hit failure mode (a neighboring, unrelated object's body
can score just as well by chance) — see
[recovery-techniques-log.md](recovery-techniques-log.md) for the false
positive that taught this, and the narrower, safe exception that was found
for it (a title-specific rescue scan that requires the actual
`CachedTitleString` property, not just a plausible score).
