# CIRCUITMIND AI

CIRCUITMIND AI is a local engineering workbench for turning electronics requirements and supplied inventories into traceable project data. **Implemented slices:** deterministic intake/feasibility, a versioned circuit graph with a JSON editor, graph validation, revision snapshots/restore, a gated KiCad XML netlist exporter, a read-only installed-symbol audit, source-hashed PDF datasheet intake with page-labelled raw-text extraction, KiCad schematic-ERC and PCB-DRC adapters, and an ngspice batch-simulation adapter for user-imported source files when those tools are installed.

## Run

Requires Python 3.11+; no third-party packages are needed.

```bash
python -m circuitmind
```

Open <http://localhost:8000>. To bind for a preview or choose another project directory:

```bash
CIRCUITMIND_HOST=0.0.0.0 CIRCUITMIND_PORT=8000 CIRCUITMIND_DATA_DIR=./workspace python -m circuitmind
```

The server is intended for local use. It does not offer a general shell, arbitrary build, or model-provider credential store; it can invoke fixed KiCad/ngspice adapters and Poppler text extraction on user-imported files when those tools are installed. Those native processes are not OS-isolated, so do not expose the app to an untrusted network or run it against untrusted input files.

## What works

- Intake accepts a natural-language project brief, a line-based component inventory, and discrete-logic or ESP32-hybrid direction.
- The benchmark feasibility check identifies insufficient stock instead of silently replacing supplied parts.
- Projects persist requirements, parsed BOM, architecture notes, assumptions/calculation examples, a validation ledger, and a ZIP export.
- Circuit-model schema v2 stores component references, symbol IDs, pin metadata, placement coordinates, nets, and pin-to-net terminals.
- The UI can edit this JSON model, ask the server for graph/schema checks, save revisions, and restore an earlier snapshot.
- KiCad XML netlist output is generated only when the model has components, consistent declared pins/nets, and caller-supplied symbol IDs. It is not a schematic export.
- The circuit editor can resolve exact symbol IDs from configured local KiCad symbol tables and compare declared pins with library metadata. Audits retain library/model SHA-256 provenance and differences; they are read-only and never fill or replace pin data.
- Datasheet PDFs can be attached by SHA-256 and optionally linked to a saved component reference. If Poppler `pdftotext` is installed, raw text extraction preserves page labels and source linkage; it does not interpret tables, verify ratings, or populate the circuit model.
- ZIP exports include revision snapshots and project artifacts.
- A user can attach an existing `.kicad_sch` and request KiCad CLI schematic ERC; source replacement requires explicit confirmation and archives the previous file.
- A user can attach a restricted batch SPICE deck (`.cir`, `.sp`, `.ckt`) and request ngspice execution; confirmed replacements archive earlier decks. The static allowlist rejects control scripting and file/include directives. Integration runners retain process outputs/status and append each attempt to `tool_runs.jsonl`; simulation raw files can be downloaded.
- A user can attach an existing `.kicad_pcb` and request KiCad CLI board DRC. Upload checks only the S-expression header; confirmed replacements archive the previous board. DRC evidence and reports are retained, and zero exit alone is not called clean without explicit zero violation and unconnected-pad summaries.

API routes for the model are `GET /api/projects/<id>/circuit`, `POST /api/projects/<id>/circuit/validate`, `POST /api/projects/<id>/circuit/symbols/resolve` (JSON `{ "model": ... }`), `PUT /api/projects/<id>/circuit`, `GET /api/projects/<id>/snapshots`, and `POST /api/projects/<id>/snapshots/restore` with `{"snapshot_id":"..."}`. Existing schematic operations are `PUT /api/projects/<id>/schematic` with a JSON `content` string and `POST /api/projects/<id>/integrations/kicad-erc`; PCB board operations are `PUT /api/projects/<id>/board` and `POST /api/projects/<id>/integrations/kicad-drc`. SPICE routes are `PUT /api/projects/<id>/simulation-deck` and `POST /api/projects/<id>/integrations/ngspice`. Datasheet routes are binary `PUT /api/projects/<id>/datasheets?filename=<name.pdf>&reference=<saved-ref>`, `GET /api/projects/<id>/datasheets`, and `POST /api/projects/<id>/datasheets/<sha256>/extract`. Saving a model snapshots the prior version first. Unsupported model versions fail closed; schema v1 projects are migrated to v2 without inferred symbol IDs.

## Engineering limits (important)

Graph validation checks references, pin existence, duplicate assignments, and model structure only. It does **not** establish electrical correctness, datasheet provenance, symbol-library validity, ERC/DRC, or a physically drawable/editable KiCad schematic. The XML netlist candidate is not run through KiCad here because `kicad-cli` is not installed; the symbol audit has only been exercised against controlled test libraries, not this environment's installed libraries. The ERC and ngspice adapters invoke trusted local CLIs with timeouts and no shell, but **are not OS/container sandboxes**, so only import files/decks you trust. Their binaries are not installed in this environment; no real ERC or simulation was performed here. Poppler `pdftotext` availability is also optional and no real datasheet extraction was run. Deck filtering is a guardrail, not proof of parser safety. Firmware compilation, PCB design, hardware testing, and manufacturing checks are not implemented. KiCad DRC now runs only on an explicitly imported board when `kicad-cli` is installed; no real DRC was performed in this environment. A DRC summary is evidence only for that tool run, not electrical or manufacturing approval. Validation artifacts distinguish these states; do not interpret a graph pass or netlist file as electrical approval.

The clock benchmark intentionally reports that the sample inventory is insufficient for the discrete six-digit design (including only three CD4511 display drivers) and does not invent pin wiring. Calculation examples are illustrative, not approved design values.

## Architecture

- `circuitmind/`: Python standard-library server, domain/workflow services, schema migration and validation, KiCad XML adapter, persistence/snapshots, integration discovery.
- `web/`: browser-based local project intake and circuit-model editor.
- `tests/`: regression tests for BOM/calculations, circuit schema/migration/export, snapshots, project APIs, and tool adapters (temporary test executables and synthetic symbol-library fixtures; no native KiCad/ngspice run).
- `docs/DEVELOPMENT_PLAN.md`: milestone roadmap and release criteria.

Projects live below `CIRCUITMIND_DATA_DIR/projects/<project-id>/`. Tool availability probes report installation/version only; they do not imply a validation operation ran.

## Tests

```bash
python -m unittest discover -s tests -v
```
