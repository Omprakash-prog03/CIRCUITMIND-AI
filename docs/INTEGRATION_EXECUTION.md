# Engineering tool execution boundary

## KiCad schematic ERC (implemented adapter)

1. Attach one existing modern KiCad schematic through the UI or `PUT /api/projects/<id>/schematic` with JSON `{ "content": "..." }`. The API accepts a `.kicad_sch` S-expression header, limits the source to 12 MB, records its SHA-256, and requires explicit replacement confirmation while archiving the prior file. Importing is **not** a successful parse or validation.
2. Request `POST /api/projects/<id>/integrations/kicad-erc`. The adapter locates `kicad-cli` on `PATH`, executes a fixed argument vector (`kicad-cli sch erc --output <report> <schematic>`) without a shell, enforces a 90-second timeout, captures bounded logs, and preserves a unique output report plus `kicad_erc_result.json` and append-only `tool_runs.jsonl`.
3. Outcomes are separate: `unavailable`, `not-run`, `timeout`, `failed`, `failed-to-start`, `completed-unparsed`, `completed-with-messages`, or `completed-no-messages`. Exit code zero alone is not considered clean. A recognized `ERC messages: 0` report summary is required to record zero messages.
4. `validation_report.json` stores the operation outcome, not an assertion of hardware or manufacturing correctness. ERC on a schematic is not PCB DRC or electrical bench validation.

## ngspice batch simulation (implemented adapter)

1. Attach a `.cir`, `.sp`, or `.ckt` netlist through `PUT /api/projects/<id>/simulation-deck` with JSON `{ "content": "..." }`. The source is size-limited, checked against a conservative directive allowlist before storage, and its replacement requires confirmation with the previous deck archived.
2. The static gate requires exactly one `.end`, at least one supported analysis (`.op`, `.dc`, `.ac`, `.tran`), and rejects `.control`/`.endc`, include/library/HDL directives, unknown dot-directives, and exclamation escapes. This narrows the batch language; it is **not** a complete SPICE grammar or proof that a deck/parser is safe.
3. Request `POST /api/projects/<id>/integrations/ngspice`. The adapter executes fixed argv (`ngspice -b -r <raw> <deck>`) without a shell, with a 60-second timeout, bounded stdout/stderr tails, a 64 MB post-run raw-file limit, unique raw/log names, a result JSON, and a `tool_runs.jsonl` entry. A run is `completed` only when exit status is zero, no recognized error appears, and output reports a positive data-row count; otherwise it is failed, timed out, unavailable, rejected, or completed-unparsed.
4. Raw files are downloaded as binary artifacts. A simulation status is evidence of this simulator operation only—not hardware behavior, datasheet correctness, ERC, or manufacturing readiness.

## Trust and resource limits

Both adapters use fixed commands without shell, wall-clock timeouts, bounded captured logs, a restricted child environment, and project-local temporary directories. **Neither is a secure sandbox**: binaries are not isolated from the user's OS, filesystem, or network; the SPICE checker is only a static allowlist. Only run trusted local tool binaries and trusted input files. Process memory/CPU/filesystem quotas and OS/container isolation are future work.

## Other tool status

`GET /api/integrations` only probes executable availability/version. It runs no ERC, simulation, or compilation. This workspace has neither `kicad-cli` nor `ngspice`, so no real ERC or simulation has been performed here. Adapter regression tests use temporary controlled executables to verify argv, timeout, output capture, and status interpretation; they are not real engineering-tool results. PlatformIO, Arduino CLI build, firmware compilation, KiCad DRC, and other integrations remain pending.
