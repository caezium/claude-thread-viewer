# Implementation and data provenance

Thread Library is an independent Python and browser implementation. It does not bundle, patch, or execute Claude Desktop, and it does not use Anthropic credentials or private network APIs.

Its file-format support was developed from local session metadata and transcript observations. Limited inspection of JavaScript distributed with the installed desktop app was also used to understand archive and deletion behavior. This project is not represented as a clean-room implementation. No Anthropic application source, binaries, or assets are distributed here.

The tests construct fictional sessions in temporary directories. Real transcripts, account identifiers, project histories, exports, screenshots of private conversations, and local machine configuration are not release fixtures. The two default storage paths are resolved from the current user's home directory at runtime.

The project license covers this implementation. It does not grant rights to third-party applications or transcript contents, and publication is not a legal determination about any third-party terms. Claude is an Anthropic product; this project is not affiliated with or endorsed by Anthropic.
