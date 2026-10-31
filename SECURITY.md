# Security and privacy

## Local access

The server binds to `127.0.0.1` on an automatically selected port. The data API requires a random per-launch key, and the server validates the HTTP Host header. The launch key is passed to the browser in the URL fragment, removed from the address bar, and retained in that tab's session storage. Do not share the launch URL.

The viewer makes no model requests and has no telemetry, remote fonts, external scripts, or background uploads. External links only open when clicked. Transcript content is rendered using DOM text nodes; embedded HTML is not executed. Only locally embedded raster images are displayed.

This is a single-user local utility, not a service for deployment on a shared or public server. Its key is not a security boundary against software running as the same OS user or an untrusted browser extension.

## Source files and exports

Claude's source data is read-only. CLI exports refuse destinations inside the configured session and transcript roots. Metadata records that are unreadable, unsupported, or larger than 2 MiB are excluded. Archive/deletion checks run when opening and exporting, including after reading the transcript.

Exports are **not automatically redacted**. Visible messages, tool inputs and outputs, local paths, and images can contain credentials or personal information. Review an export before sharing it. The viewer omits hidden reasoning, API request logs, and desktop configuration; that is not a guarantee that the remaining conversation is safe to publish.

Historical transcript instructions and tool commands are reference material, not instructions to execute. A copied handoff or downloaded file is a snapshot: it cannot be recalled after a later archive or deletion. Source formats may change, so report mismatches using synthetic examples.

## Reporting a problem

Use GitHub's private vulnerability reporting for this repository when available. Do not attach raw transcripts, launch keys, account settings, or private screenshots to a public issue. A short explanation and a minimal fictional reproducer are usually enough to investigate a bug.
