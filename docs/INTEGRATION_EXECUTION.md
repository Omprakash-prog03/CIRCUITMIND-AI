# Engineering tool execution boundary

## KiCad schematic ERC (implemented adapter)

1. Attach one existing modern KiCad schematic through the UI or `PUT /api/projects/<id>/schematic` with JSON `{ "content": "..." }`. The API accepts a `.kicad_sch` S-expression header, limits the source to 12 MB, records its SHA-256, and requires explicit replacement confirmation while archiving the prior file. Importing is **not** a successful parse or validation.
2. Request `POST /api/projects/<id>/integrations/kicad-erc`. The adapter locates `kicad-cli` on `PATH`, executes a fixed argument vector (`kicad-cli sch erc --output <report> <schematic>`) without a shell, enforces a 90-second timeout, captures bounded logs, and preserves a unique output report plus `kicad_erc_result.json` and append-only `tool_runs.jsonl`.
3. Outcomes are separate: `unavailable`, `not-run`, `timeout`, `failed`, `failed-to-start`, `completed-unparsed`, `completed-with-messages`, or `completed-no-messages`. Exit code zero alone is not considered clean. A recognized `ERC messages: 0` report summary is required to record zero messages.
4. `validation_report.json` stores the operation outcome, not an assertion of hardware or manufacturing correctness. ERC on a schematic is not PCB DRC or electrical bench validation.

## KiCad board DRC (implemented adapter)

1. Attach an existing `.kicad_pcb` through the UI or `PUT /api/projects/<id>/board` with JSON `{ "content": "..." }`. The API checks the S-expression header, limits source to 12 MB, records SHA-256, and requires explicit replacement confirmation while archiving the previous board. This is **not** a complete parse or board validation.
2. Request `POST /api/projects/<id>/integrations/kicad-drc`. The adapter runs fixed argv (`kicad-cli pcb drc --output <report> <board>`) without a shell, with a 120-second timeout and bounded captured output, then preserves the input board SHA-256, a unique report, `kicad_drc_result.json`, and an append-only `tool_runs.jsonl` entry.
3. Outcomes include `unavailable`, `not-run`, `timeout`, `failed`, `failed-to-start`, `completed-unparsed`, `completed-with-violations`, and `completed-no-reported-violations`. A zero exit is not clean by itself: the report must include explicit counts for both DRC violations and unconnected pads, and both must be zero for the no-reported-violations status.
4. This status reflects only KiCad's report for the imported board. It does not establish electrical design correctness, successful fabrication, manufacturing-rule suitability, assembly quality, or hardware operation. Review the report and confirm board-specific constraints separately.

## Datasheet PDF source intake and text extraction

1. Use **Import datasheet PDF** or send a raw PDF body to `PUT /api/projects/<id>/datasheets?filename=<name.pdf>&reference=<saved-ref>` with `Content-Type: application/pdf`. The source must begin with `%PDF-` and fit within 20 MB. The endpoint records SHA-256, size, original filename, and import timestamp; it stores one content-addressed PDF and an append-only source record. An optional component reference must already exist in the saved circuit model; the associated model SHA-256 is recorded. This is a header check, not PDF validation.
2. `GET /api/projects/<id>/datasheets` lists source records. PDF files are downloadable project artifacts; duplicate byte-identical uploads reuse the same stored PDF while retaining separate source records/associations.
3. **Extract latest PDF text** requests `POST /api/projects/<id>/datasheets/<sha256>/extract`. If `pdftotext` is installed, the adapter executes fixed argv with a 90-second timeout in a project-local temporary directory and captures bounded process output. Extracted text is limited to 8 MB and saved with explicit `SOURCE PDF PAGE n` delimiters; the report references the original SHA-256 and page count. `GET /api/integrations` probes `pdftotext -v` only.
4. Extraction is only text transcription. It does not interpret tables, cross-check variants or errata, identify trusted component ratings, or populate pinouts/design requirements. Scanned/image-only pages may yield no text. No real PDF extraction was run in this environment; adapter tests use a controlled temporary executable. PDF parsers are native local processes and not security sandboxes; use trusted datasheets.

## Installed KiCad symbol audit (read-only)

1. In the circuit editor, choose **Audit installed symbols**. The UI sends the current draft to `POST /api/projects/<id>/circuit/symbols/resolve` as JSON `{ "model": ... }`; the stored model is not replaced or saved by this operation.
2. The resolver searches a project-local `sym-lib-table`, an optional `KICAD_SYMBOL_LIB_TABLE`, user KiCad configuration tables, then common system tables. URI variables such as `${KICAD9_SYMBOL_DIR}` are expanded from the environment; `KICAD_SYMBOL_DIR` may be set as a fallback for deployments where KiCad has not exported its versioned variable. Only local `.kicad_sym` files are read, with 4 MB table and 32 MB symbol-library limits.
3. The report includes exact symbol properties, extracted declared pin number/name/electrical-type data, each library file SHA-256, the submitted model SHA-256, and per-component differences against the model's current declared pins. The JSON audit is retained as `kicad_symbol_audit_<run-id>.json`.
4. No symbol ID or pinout is inferred or written back. Missing libraries remain unavailable; inherited definitions are explicitly marked unexpanded and are not treated as complete. This lookup is not a datasheet cross-check, electrical check, ERC, or proof that the symbol/footprint is suitable for a particular part.

## ngspice batch simulation (implemented adapter)

1. Attach a `.cir`, `.sp`, or `.ckt` netlist through `PUT /api/projects/<id>/simulation-deck` with JSON `{ "content": "..." }`. The source is size-limited, checked against a conservative directive allowlist before storage, and its replacement requires confirmation with the previous deck archived.
2. The static gate requires exactly one `.end`, at least one supported analysis (`.op`, `.dc`, `.ac`, `.tran`), and rejects `.control`/`.endc`, include/library/HDL directives, unknown dot-directives, and exclamation escapes. This narrows the batch language; it is **not** a complete SPICE grammar or proof that a deck/parser is safe.
3. Request `POST /api/projects/<id>/integrations/ngspice`. The adapter executes fixed argv (`ngspice -b -r <raw> <deck>`) without a shell, with a 60-second timeout, bounded stdout/stderr tails, a 64 MB post-run raw-file limit, unique raw/log names, a result JSON, and a `tool_runs.jsonl` entry. A run is `completed` only when exit status is zero, no recognized error appears, and output reports a positive data-row count; otherwise it is failed, timed out, unavailable, rejected, or completed-unparsed.
4. Raw files are downloaded as binary artifacts. A simulation status is evidence of this simulator operation only—not hardware behavior, datasheet correctness, ERC, or manufacturing readiness.

## Trust and resource limits

Native tool adapters use fixed commands without a shell, wall-clock timeouts, bounded captured logs, a restricted child environment, and project-local temporary directories. They are **not secure sandboxes**: binaries are not isolated from the user's OS, filesystem, or network; the SPICE checker is only a static allowlist. The installed-symbol audit parses local library files in-process and is likewise not a security boundary for hostile files. Only run trusted local tool binaries and trusted input files. Process memory/CPU/filesystem quotas and OS/container isolation are future work.

## Other tool status

`GET /api/integrations` only probes executable availability/version. It runs no ERC, DRC, simulation, datasheet extraction, or compilation. This workspace has neither `kicad-cli` nor `ngspice`, so no real ERC, DRC, or simulation has been performed here. Adapter regression tests use temporary controlled executables to verify argv, timeout, output capture, and status interpretation; they are not real engineering-tool results. PlatformIO, Arduino CLI build, firmware compilation, and other integrations remain pending.
