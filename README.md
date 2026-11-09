# Signal SQLite

Extract Signal's newer **local backup v2 directory format** into searchable SQLite
and decrypted attachment files. The expected input is a `SignalBackups/` directory
containing timestamped snapshots (`main`, `metadata`, `files`) and a shared
`files/` media directory. The original backup is read without modification.

## Setup

Use Python 3.15.2 and Hatch. The root `pyproject.toml` is the dependency manifest;
Hatch creates the project-local `.venv` using its virtual environment type.

```bash
uv python install 3.15.2
cd /data/shared/code/projects/signal-backup-to-sqlite
hatch env create
hatch run signal-sqlite --help
```

Dependencies stay inside the virtual environment. Python source is in
`src/signal_sqlite/`; tests are in `tests/`.

## Extract

```text
signal-sqlite extract-backup [--skip-attachments] [--key-file PATH] INPUT_PATH OUTPUT_PATH
```

```bash
hatch run signal-sqlite extract-backup \
  --key-file /run/user/1000/signal-backup.key \
  /home/jglanz/Dropbox/Documents/SignalAppBackups/SignalBackups \
  /data/shared/code/projects/signal-backup-to-sqlite/output
```

Both positional paths are required. `INPUT_PATH` accepts the backup root or a
specific timestamped snapshot directory. For a root, the newest snapshot is
selected and printed. An incomplete newest snapshot is reported instead of
silently switching to older data.

`OUTPUT_PATH` is a directory. A missing directory and all missing parents are
created with `os.makedirs`. An existing empty directory is accepted; existing
files and nonempty directories are never overwritten. The output must be outside
the source backup tree.

Attachments are exported by default. Add `--skip-attachments` to retain their
metadata without decrypting the files. Full message bodies held in long-text
attachments can only be indexed when those attachments are exported.

Without `--key-file`, the CLI prompts with hidden input in an interactive terminal.
Noninteractive runs require a key file. Key files accept UTF-8 text with spaces,
tabs, and line breaks. Signal's uppercase display format is accepted, including
`#` for the letter O and `=` for zero. Those documented display substitutions are
reversed and whitespace is removed; the result must contain exactly 64 ASCII
letters/digits. Key files must be regular files owned by the
current user with no group/other access (`chmod 600 PATH`); symlinks are rejected.

### Temporary key file

Run this command in **your terminal**. Paste the key on one line (spaces are
fine). Input is hidden, and the command prints only a randomly named private file
path under your runtime directory. Share only that path with an agent running
the extraction, never the key itself.

```bash
bash -c '
  set -e
  SIGNAL_KEY_TEMPLATE="${XDG_RUNTIME_DIR:?}/signal-backup.XXXXXXXX.key"
  SIGNAL_KEY_OUTPUT_FORMAT="%s"
  SIGNAL_PATH_OUTPUT_FORMAT="\n%s\n"
  signal_key_file=$(mktemp "$SIGNAL_KEY_TEMPLATE")
  IFS= read -r -s -p "Recovery key (one line; hidden): " signal_recovery_key
  printf "$SIGNAL_KEY_OUTPUT_FORMAT" "$signal_recovery_key" > "$signal_key_file"
  unset signal_recovery_key
  printf "$SIGNAL_PATH_OUTPUT_FORMAT" "$signal_key_file"
'
```

Remove that temporary file after extraction. The CLI leaves caller-provided key
files untouched. Recovery and derived master keys are never written to logs or
output. Outputs are plaintext and include the backup's original account/message
records; treat them as private. New directories are mode `0700`, new files `0600`,
including any newly created intermediate directories.

## Output and search

### Query commands

Queries open an existing export read-only; no recovery key or re-extraction is
needed. Global options go **before** the command:

```text
signal-sqlite [-f/--format text|json] [-d/--database PATH] COMMAND ...
```

The default format is `text`. The default database is `output/signal.sqlite3`,
relative to the working directory. `--database` accepts either a SQLite file or
an export directory containing `signal.sqlite3`.

| Command | Aliases | Options |
| --- | --- | --- |
| `channels list` | `ch`, `all` | `--page`, `--page-size` |
| `channels search QUERY` | `ch`, `match` | Paging, `--regex` / `--exp` / `-r`, `--include-messages` |
| `conversations get CHANNEL_ID` | `convo` / `co`, `g` | `--offset`, `--limit`, `--since`, `--until`, `--search`, regex flags, `--include-revisions` |

Group and subcommand aliases can be combined, for example `ch all`,
`ch match`, or `co g`.

```bash
hatch run signal-sqlite channels list --page 1 --page-size 25
hatch run signal-sqlite ch match 'Alice'
hatch run signal-sqlite channels search --regex --include-messages 'contract|employment'
hatch run signal-sqlite --format text conversations get 187
hatch run signal-sqlite --format json co g 187 --since 2026-01-01 --until 2026-11-08
hatch run signal-sqlite --database /path/to/export convo get 187 --offset 20 --limit 50
```

Channels are ordered by most recent activity, then numeric channel ID. Each
channel includes its ID, group name when applicable, participants, and latest
activity in UTC and original milliseconds. Activity includes conversation
updates and sent/received times. Empty channels have a null timestamp. Group
participants combine the saved membership snapshot with historical authors and
reactors. Available names are used; a member without an identifiable contact is
represented by its service ID. Your own recipient is included in participants.

Search is a case-insensitive literal substring by default, including saved
profile/system names and nicknames. Regex matching is opt-in and case-insensitive.
`--include-messages` also searches bodies, quotes, captions, filenames, and earlier
revisions. Channel pages default to 50 entries, with a maximum of 1,000; page
numbers start at 1. JSON channel results contain `channels`, `page`, `page_size`,
`total`, and `total_pages`.

Conversation messages are chronological and include the current versions by
default. `--include-revisions` adds historical versions with `parent_message_id`
and `revision_index`. Filters are applied before `--offset` / `--limit`; without
a limit the full matching conversation is returned. `--search` searches the same
message fields as channel message matching; regex flags require `--search`.

Dates accept ISO 8601 dates/times or epoch milliseconds. Naive dates/times use
UTC; explicit timezone offsets are respected. Start and end timestamps are
inclusive. A date-only `--until` includes that whole day. For example,
`--since 2026-11-01 --until 2026-11-01` selects one full UTC day.

Conversation JSON is one object with `channel`, `messages`, `attachments`,
`total_messages`, `offset`, `limit`, and `has_more`. Attachments appear both in
the top-level array and beneath their message, with `relative_path` from the
database directory and `absolute_path`. Both paths are null for unavailable or
deliberately skipped attachments. Only attachments belonging to the selected
messages are included.

Text conversations place your messages on the right and other messages on the
left, with quotes, reactions, and attachment paths beneath their owning message.
There is no separate attachment listing. Absolute attachment paths are kept
intact, even when longer than the terminal width. Conversation updates and deleted
messages have descriptive placeholders.

The global JSON format also works for `extract-backup`; progress and warnings go
to stderr so stdout contains exactly one JSON object.

### Terminal UI

```bash
hatch run signal-sqlite tui
hatch run signal-sqlite --database /path/to/export tui
```

The TUI uses Textual, installed by Hatch from the runtime dependencies in
`pyproject.toml`. It shares the read-only query layer with the CLI and has three
tabs:

- **Channels:** participant/group search, regex and message matching, a keyboard
  navigable table, and Previous/Next paging. Select a row and press Enter to open
  its conversation.
- **Conversation:** your messages appear on the right, others on the left. It
  opens the latest 100 messages; Older, Newer, and Latest navigate the history.
  Expand Message filters to search text, apply regex, set date bounds, or include
  revisions. Apply and Reset update the selected message range.
- **Output:** select the current channel page or current conversation message
  page, preview text/JSON, and save it to a new file. JSON includes message and
  attachment arrays with relative/absolute attachment paths. Saving creates
  missing directories with private permissions and refuses to overwrite files.

`Ctrl+F` focuses channel search, `Esc` returns to the channel table, `Ctrl+R`
refreshes the current data, and `Ctrl+E` opens Output. Use Tab/Shift+Tab to move
between controls and `q` (outside text inputs) or `Ctrl+Q` to quit. The global
`--format` chooses the initial Output preview format. The TUI requires an
interactive terminal; its layout is tested at 80×24 and 120×40.

### Database and files

```text
OUTPUT_PATH/
├── signal.sqlite3
├── attachments/       # absent with --skip-attachments
└── manifest.json
```

- `frames`: byte-exact protobuf payloads and decoded JSON, including the backup
  header and unknown frame types. Record IDs retain their stream ordering.
- `recipients`, `chats`, `messages`, `reactions`, `attachments`: relational
  projections. Source recipient/chat IDs are stored as text to preserve uint64s.
- `messages.parent_message_id` and `revision_index`: distinguish current messages
  from their revisions. Original millisecond timestamps are preserved.
- `message_details`: messages joined to sender/conversation names and UTC dates.
- `messages_fts`: Unicode FTS5 search over bodies, quoted text, other text
  (including captions), and filenames; revisions are searchable too.
- `export_metadata`: the manifest information embedded in the database.

For example, in any SQLite client with FTS5 support:

```sql
SELECT m.date_sent_utc, m.conversation_name, m.author_name, m.body
FROM messages_fts
JOIN message_details AS m ON m.id = messages_fts.rowid
WHERE messages_fts MATCH 'employment'
  AND m.parent_message_id IS NULL
ORDER BY m.date_sent_ms;
```

Omit the `parent_message_id` condition to include revision matches. SQLite's FTS5
query syntax supports phrases and Boolean operators. Exported attachment names
are generated from local media identifiers with safe suffixes; the original
filename, caption, relative output path, verification hashes, and export status
are stored in `attachments`.

`manifest.json` records source snapshot hashes, backup/tool versions, counts,
warnings, and completeness. The frame count includes the header; current messages
and revisions are counted separately. Attachment counts describe references,
so multiple references may share a single exported file.

The main backup is authenticated before parsing. Attachments are authenticated,
trimmed to their declared plaintext size, and checked against their plaintext
hash before publication. Work is staged privately and published by directory
rename after SQLite/FTS integrity verification.

Exit status `0` means extraction completed (including deliberately skipped media).
Status `1` indicates an error; if only individual attachment extraction/indexing
failed, a usable database is still published with `complete: false`. Missing
remote-only attachments are recorded as `unavailable`. Usage errors return `2`.
Invalid keys, a corrupt main stream, unsupported versions, or invalid references
publish no extracted data; a newly created output directory may remain empty.

## Development and compatibility

```bash
hatch run check
hatch build
hatch run proto
```

The tests use synthetic encrypted backups and Signal's public derivation vectors.
They never require a real recovery key. The pinned schemas and provenance are in
`src/signal_sqlite/_proto/`; their upstream license is retained in
`THIRD_PARTY_NOTICES.md`. Regenerate bindings with `hatch run proto`, then rerun
checks and build.

This version supports local metadata version 1 and message backup version 1.
Unknown protobuf fields remain in original record BLOBs even when they cannot be
represented in normalized tables. This tool creates an analysis database; it
does not restore a Signal installation, fetch remote media, or parse older
Android single-file backups or the live Signal Desktop SQLCipher database.
