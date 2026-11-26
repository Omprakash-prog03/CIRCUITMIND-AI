# Engineering tool execution boundary

## KiCad schematic ERC (implemented adapter)

1. Attach one existing modern KiCad schematic through the UI or `PUT /api/projects/<id>/schematic` with JSON `{ "content": "..." }`. The API accepts a `.kicad_sch` S-expression header, limits the source to 12 MB, records its SHA-256, and refuses overwrite so the imported original is not silently replaced. Importing is **not** a successful parse or validation.
2. Request `POST /api/projects/<id>/integrations/kicad-erc`. The adapter locates `kicad-cli` on `PATH`, executes a fixed argument vector (`kicad-cli sch erc --output <report> <schematic>`) without a shell, enforces a 90-second timeout, captures bounded logs, and preserves a unique output report plus `kicad_erc_result.json` and append-only `tool_runs.jsonl`.
3. Outcomes are separate: `unavailable`, `not-run`, `timeout`, `failed`, `failed-to-start`, `completed-unparsed`, `completed-with-messages`, or `completed-no-messages`. Exit code zero alone is not considered clean. A recognized `ERC messages: 0` report summary is required to record zero messages.
4. `validation_report.json` stores the operation outcome, not an assertion of hardware or manufacturing correctness. ERC on a schematic is not PCB DRC or electrical bench validation.

## Trust and resource limits

This adapter uses a wall-clock timeout, fixed command, bounded input file, restricted child environment, and project-local temporary directory. **It is not a secure sandbox**: the local KiCad process is not isolated from the user's OS, filesystem, or network. Only run trusted local KiCad binaries and trusted `.kicad_sch` files. Process memory/CPU limits and OS/container isolation are future work. Other compiler/simulation integrations are not enabled through this interface.

## Other tool status

`GET /api/integrations` only probes executable availability/version. It runs no ERC, simulation, or compilation. This workspace has no `kicad-cli`, so the integration operation is reported unavailable here; adapter regression tests use a temporary controlled executable to verify argv, timeout, output capture, and status interpretation, not to claim a real KiCad check.
