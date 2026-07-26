# VMOS IKUN Team Profile Design

## Goal

Make the five TikTok accounts on `vmos-02` through `vmos-06` visibly belong to
one IKUN Bags team while preserving their existing account identities and
profile links. `vmos-01` is explicitly outside the operation.

## Approved Profile Mapping

| Device | ADB port | Required visible username | New public nickname |
| --- | ---: | --- | --- |
| `vmos-02` | `65286` | `@saigeava6` | `IKUN Bags | Ava` |
| `vmos-03` | `59455` | `@yarazoey6` | `IKUN Bags | Zoey` |
| `vmos-04` | `54178` | `@helenasavannah` | `IKUN Bags | Savannah` |
| `vmos-05` | `65511` | `@gabriellaantonell37` | `IKUN Bags | Gabriella` |
| `vmos-06` | `58659` | `@kfuknocx78` | `IKUN Bags | Shop` |

The TikTok `@username` must not be edited. `vmos-01` at ADB port `64860` must
not be opened or changed by this operation.

## Avatar

Use the exact PNG supplied by the user in this conversation as the common
avatar for all five in-scope accounts. Stage it only in an ignored runtime
directory or the device media directory; do not commit it to the repository.
Accept TikTok's square crop only after confirming the product remains centered
and recognizable.

## Execution Contract

1. Stop or exclude any publishing or touch worker that could control an
   in-scope device during the profile edit.
2. Connect to one device and open TikTok's own Profile surface.
3. Confirm the exact visible `@username` from the mapping before entering Edit
   profile. A missing or mismatched identity stops that device without edits.
4. Change only the public nickname to the mapped value.
5. Upload and select the approved avatar, preserving a centered square crop.
6. Save through visible TikTok controls.
7. Return to the public Profile surface and verify both the exact nickname and
   the changed avatar are visible. A tap or successful ADB command alone is not
   acceptance.
8. Record only device ID, expected username, nickname result, avatar result,
   and terminal status. Keep screenshots in ignored local storage.

Nickname and avatar saves may be separate TikTok operations. After either save
becomes uncertain, inspect the visible Profile state before retrying so the
operation cannot accidentally toggle, overwrite, or repeat a completed step.

## Failure And Safety Rules

- Never work around login, verification, CAPTCHA, username-change cooldown, or
  account restriction prompts.
- Do not change biography, links, privacy, contact settings, or `@username`.
- Do not proceed on another account identity merely because it is present on
  the expected device.
- One device failure does not authorize changing `vmos-01` or substituting a
  different account.
- Do not resume unrelated publishing or touch work until the profile edit on
  that device has a verified terminal state.

## Acceptance

- `vmos-01` remains unchanged.
- Each of `vmos-02` through `vmos-06` shows its mapped public nickname.
- Each in-scope account visibly shows the supplied common avatar.
- All five original `@usernames` remain unchanged and continue matching local
  identity configuration.
- Every attempted account has a recorded verified success, explicit failure,
  or uncertain state; no result is inferred from a click alone.
