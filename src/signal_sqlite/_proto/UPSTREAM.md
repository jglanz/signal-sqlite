# Signal protobuf provenance

Source: https://github.com/signalapp/Signal-Desktop

Revision: `5d1bbb7ee231b35cb9cc8cc449ffb819c528efc8`

Definitions copied without changes from `protos/Backups.proto` and
`protos/LocalBackup.proto`. License: AGPL-3.0-only; see root THIRD_PARTY_NOTICES.md.

Regenerate bindings with `hatch run proto`. Generated files are not hand-edited.

Recovery-key display normalization follows the same Signal Desktop revision's
[`ts/util/formatBackupKeyForDisplay.std.ts`](https://github.com/signalapp/Signal-Desktop/blob/5d1bbb7ee231b35cb9cc8cc449ffb819c528efc8/ts/util/formatBackupKeyForDisplay.std.ts):
uppercase letters, groups separated by whitespace, `#` for O, and `=` for zero.
