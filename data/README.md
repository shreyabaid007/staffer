# `data/` — ingestion layers

Content-addressed, immutable ingestion storage (AD-066). Layout:

```
data/
  raw/{supply,resumes,feedback}/   # source inputs (PII-dense — gitignored)
  bronze/
    blobs/sha256/<hash>            # verbatim bytes, immutable (PII-dense — gitignored)
    records/<hash>.jsonl           # rows as read: verbatim strings, zero normalization
    manifest.jsonl                 # append-only landing log + crash-recovery commit marker
  .cache/                          # derivation cache, keyed on (content_hash, version)
```

Only the `.gitkeep` placeholders are tracked; all raw and bronze contents are gitignored.

## Encryption-at-rest boundary

Raw inputs and bronze blobs/records hold raw PII-dense bytes (names, emails, resume/feedback
text).

- **MVP (now):** plaintext on local disk, gitignored. The trust boundary is the developer's
  machine; there is **no at-rest encryption** at this layer yet.
- **Later (AD-066, ee-ingestion-architecture §4):** the `BlobStore` protocol swaps to object
  storage, where encryption-at-rest + IAM provide the PII-at-rest control.

This boundary is documented, not implemented here — the `BlobStore`/`Manifest` protocols are
kept backend-swappable so the later move requires no caller changes.
