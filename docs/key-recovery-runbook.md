# Encryption Key Backup & Recovery Runbook

**Severity: CRITICAL — key loss = total, unrecoverable loss of all encrypted PHI.**

Companion encrypts PHI at rest with per-tenant envelope encryption
(see `caregiver-access-and-privacy.md` §7 and `app/services/field_crypto.py`):
a **KEK** (`COMPANION_FIELD_ENCRYPTION_KEY` / `COMPANION_FIELD_KEYRING`) wraps a
per-user **DEK** in the `user_encryption_keys` table; fields are encrypted with
the user's DEK as `f2:` ciphertext.

## What must be backed up off-cluster (all of it, or it's worthless)

1. **The field KEK(s)** — `COMPANION_FIELD_ENCRYPTION_KEY` (the `k1` KEK), any
   `COMPANION_FIELD_KEYRING`, and `COMPANION_FIELD_LEVEL_KEYRING`. These unwrap
   every per-user DEK. Source of truth: the `companion-secrets` Secret.
2. **The sealed-secrets controller private keys** (namespace `infra`,
   label `sealedsecrets.bitnami.com/sealed-secrets-key` — there may be several
   after rotation; back up **all**). Per-cluster; without them, none of the
   committed `SealedSecret` YAML can be unsealed on a rebuilt cluster.
3. The **database backup** (CNPG → MinIO → restic offsite) already covers the
   `user_encryption_keys` rows + encrypted columns — but those are decryptable
   **only** with (1).

Store (1) and (2) in a password vault (1Password) "break-glass" item, **not** in
git, **not** alongside the DB backup. Re-export after any key rotation.

## The coupling

Encrypted PHI is recoverable only with **DB backup + field KEK** together. DB
backup alone = undecryptable. KEK alone = no data. Keep them from compatible
points in time.

## Recovery (cluster rebuild)

1. New cluster + sealed-secrets controller.
2. **Restore the controller keys first**, before any SealedSecret:
   `kubectl apply -f <controller-keys>.yaml` (ns `infra`), then
   `kubectl -n infra rollout restart deploy sealed-secrets-controller`.
   Committed `SealedSecret`s (incl. `companion-secrets`) now unseal as-is.
3. Restore the database (restic → CNPG).
4. App boots with the restored KEK, unwraps each user's DEK, decrypts fields.
   No re-encryption needed.

## Quarterly test-restore

On a scratch cluster: restore the controller keys, apply one SealedSecret,
confirm it materializes its plaintext Secret. Validates the backup **before**
it's needed for real.

## Export procedure (to refresh the backup)

```sh
# Controller keys (all of them):
kubectl get secret -n infra -l sealedsecrets.bitnami.com/sealed-secrets-key -o yaml > controller-keys.yaml
# Field KEKs (plaintext):
kubectl get secret companion-secrets -n companion \
  -o jsonpath='{.data.COMPANION_FIELD_ENCRYPTION_KEY}' | base64 -d
kubectl get secret companion-secrets -n companion \
  -o jsonpath='{.data.COMPANION_FIELD_LEVEL_KEYRING}' | base64 -d
```
Move the outputs into the vault, then shred the local files (`rm -P`).
