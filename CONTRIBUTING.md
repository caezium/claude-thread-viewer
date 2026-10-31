# Contributing

Keep the viewer focused on locally saved Claude desktop Code threads. It must remain usable without a Claude login and must not alter source conversations or their lifecycle state.

Use synthetic fixtures for parser and status changes. Build them in temporary directories as the existing tests do; never check in a real session, export, account identifier, or screenshot of someone's history. Avoid adding third-party application code or binaries. See [PROVENANCE.md](PROVENANCE.md) and [SECURITY.md](SECURITY.md).

Run the tests before submitting a change:

```sh
python3 -m unittest -v
node --test test_web.cjs
node --check web/app.js
```

Python uses only the standard library. Node.js 18 or newer is needed for the frontend regression tests, not to run the viewer. Changes to supported formats should explain what is preserved, omitted, or unsupported. Keep archive/deletion checks conservative, and test dismissal during an in-flight read or export when changing those paths.

For new features or a substantial UI change, describe the user problem and expected behavior in an issue before expanding the implementation. This avoids accidentally turning a local reader into an agent launcher or a history-sync service.
