# CIRCUITMIND AI

CIRCUITMIND AI is a local engineering workbench for turning electronics requirements and supplied inventories into traceable project data. **Implemented slices:** deterministic intake/feasibility, a versioned circuit graph with a JSON editor, graph validation and revision snapshots; a standalone C++20 core with bounded linear DC MNA and RC backward-Euler simulation; and a deterministic single-pole RC low-pass synthesis/analyze/test workflow using the native core. The Python DC MNA and generic RC transient entry points now delegate their numerical solves to C++ while retaining Python validation, graph mapping, and report formatting. Other slices include revision-bound engineering calculations, a gated KiCad XML netlist exporter, read-only symbol audits, source-hashed datasheet text intake, KiCad ERC/DRC adapters, and an ngspice batch adapter for user-imported decks when available.

## Run

The Python web application requires Python 3.11+ and uses the standard library. Building the native core requires CMake 3.20+ and a C++20 compiler. Build the native C++20 core for DC operating-point, RC transient, and RC synthesis endpoints (the app can still start without it, but solver requests return HTTP 503 until the core is built):

```bash
cmake -S . -B build -DCIRCUITMIND_BUILD_TESTS=ON
cmake --build build --config Release
ctest --test-dir build --output-on-failure
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
- The standalone C++20 core (`core/`) provides bounded dense linear DC MNA and fixed-step backward-Euler RC transient solving behind a versioned C ABI. Python entry points marshal explicit inputs and preserve report contracts, while numerical solves run in C++; graph mapping and workflow orchestration remain Python-owned. DC supports up to 500 elements, 150 non-ground nodes, and 180 total unknowns; transient supports up to 100 elements, 24 non-ground nodes, 32 unknowns, and 2,000 steps. Neither is nonlinear SPICE validation. Python also provides unit-checked Ohm's law, resistor/capacitor networks, RC time constant, stored energy, a tolerance-aware E6/E12/E24 LED-resistor analysis, a clock timebase budget, and explicit-input clock-divider candidates. Project reports retain hashes; matching a hash is not engineering approval.
- Circuit-model schema v2 stores component references, symbol IDs, pin metadata, placement coordinates, nets, and pin-to-net terminals.
- The UI can edit this JSON model, ask the server for graph/schema checks, save revisions, and restore an earlier snapshot.
- KiCad XML netlist output is generated only when the model has components, consistent declared pins/nets, and caller-supplied symbol IDs. It is not a schematic export.
- The circuit editor can resolve exact symbol IDs from configured local KiCad symbol tables and compare declared pins with library metadata. Audits retain library/model SHA-256 provenance and differences; they are read-only and never fill or replace pin data.
- Datasheet PDFs can be attached by SHA-256 and optionally linked to a saved component reference. If Poppler `pdftotext` is installed, raw text extraction preserves page labels and source linkage; it does not interpret tables, verify ratings, or populate the circuit model.
- ZIP exports include revision snapshots and project artifacts.
- A user can attach an existing `.kicad_sch` and request KiCad CLI schematic ERC; source replacement requires explicit confirmation and archives the previous file.
- A user can attach a restricted batch SPICE deck (`.cir`, `.sp`, `.ckt`) and request ngspice execution; confirmed replacements archive earlier decks. The static allowlist rejects control scripting and file/include directives. Integration runners retain process outputs/status and append each attempt to `tool_runs.jsonl`; simulation raw files can be downloaded.
- A user can attach an existing `.kicad_pcb` and request KiCad CLI board DRC. Upload checks only the S-expression header; confirmed replacements archive the previous board. DRC evidence and reports are retained, and zero exit alone is not called clean without explicit zero violation and unconnected-pad summaries.

API routes for the model are `GET /api/projects/<id>/circuit`, `POST /api/projects/<id>/circuit/validate`, `POST /api/projects/<id>/circuit/symbols/resolve` (JSON `{ "model": ... }`), `PUT /api/projects/<id>/circuit`, `GET /api/projects/<id>/snapshots`, and `POST /api/projects/<id>/snapshots/restore` with `{"snapshot_id":"..."}`. Existing schematic operations are `PUT /api/projects/<id>/schematic` with a JSON `content` string and `POST /api/projects/<id>/integrations/kicad-erc`; PCB board operations are `PUT /api/projects/<id>/board` and `POST /api/projects/<id>/integrations/kicad-drc`. SPICE routes are `PUT /api/projects/<id>/simulation-deck` and `POST /api/projects/<id>/integrations/ngspice`. The in-process DC solver accepts a user-supplied `{ "netlist": ... }` at `POST /api/projects/<id>/simulations/dc-operating-point`. An explicit graph-bound alternative is `POST /api/projects/<id>/simulations/dc-operating-point/from-model` with `{ "mapping": { "ground_net": ..., "bindings": [...], "exclusions": [...] } }`; each saved component must be mapped to a supported R/V/I type with both ordered pins and a typed numeric value, or explicitly excluded with a reason. No component description is parsed. `POST /api/projects/<id>/simulations/transient-rc/from-model` accepts `{ "mapping": ..., "time_step": ..., "stop_time": ... }` for a graph-bound fixed-step backward-Euler RC run; capacitor initial voltages and source waveforms must be explicit. It stores full traces in the report, while `GET /api/projects/<id>/simulations` returns bounded final-value summaries plus netlist/mapping hash integrity and whether the associated saved model changed since the run. `POST /api/projects/<id>/designs/rc-low-pass` implements a deterministic bounded synthesis vertical slice using versioned explicit requirements, standard E-series nominal values, a schema-v2 candidate graph, cutoff/tolerance analysis, and a numerical backward-Euler test. The request requires `request_version: 1`, typed `inputs`, and an explicit `apply_to_circuit` boolean; applying the graph snapshots the prior circuit. Exclusions make a solve partial, and a complete mapping still does not establish physical/electrical correctness. Calculation routes include `POST /api/projects/<id>/calculations/led-resistor` with `{ "inputs": ..., "source_refs": ... }`, `POST /api/projects/<id>/calculations/clock-timebase` with explicit `{ "inputs": { "nominal_tick_rate": ..., "target_tick_rate": ..., "tolerance_ppm": ... } }`, `POST /api/projects/<id>/calculations/clock-divider` with explicit `{ "inputs": { "source_frequency": ..., "target_frequency": ..., "source_tolerance_ppm": ... } }`, and `GET /api/projects/<id>/calculations` for current/stale revision status. Datasheet routes are binary `PUT /api/projects/<id>/datasheets?filename=<name.pdf>&reference=<saved-ref>`, `GET /api/projects/<id>/datasheets`, and `POST /api/projects/<id>/datasheets/<sha256>/extract`. Saving a model snapshots the prior version first. Unsupported model versions fail closed; schema v1 projects are migrated to v2 without inferred symbol IDs.

## Engineering limits (important)

Graph validation checks references, pin existence, duplicate assignments, and model structure only. It does **not** establish electrical correctness, datasheet provenance, symbol-library validity, ERC/DRC, or a physically drawable/editable KiCad schematic. The XML netlist candidate is not run through KiCad here because `kicad-cli` is not installed; the symbol audit has only been exercised against controlled test libraries, not this environment's installed libraries. The ERC and ngspice adapters invoke trusted local CLIs with timeouts and no shell, but **are not OS/container sandboxes**, so only import files/decks you trust. Their binaries are not installed in this environment; no KiCad ERC or ngspice run was performed here. The in-process MNA solver does execute a linear DC operating-point calculation for explicit ideal R/V/I netlists, but does not constitute transient/nonlinear simulation or validation of the saved graph. Poppler `pdftotext` availability is also optional and no real datasheet extraction was run. Deck filtering is a guardrail, not proof of parser safety. Firmware compilation, PCB design, hardware testing, and manufacturing checks are not implemented. KiCad DRC now runs only on an explicitly imported board when `kicad-cli` is installed; no real DRC was performed in this environment. A DRC summary is evidence only for that tool run, not electrical or manufacturing approval. Validation artifacts distinguish these states; do not interpret a graph pass or netlist file as electrical approval.

The clock benchmark intentionally reports that the sample inventory is insufficient for the discrete six-digit design (including only three CD4511 display drivers) and does not invent pin wiring. Intake-time calculation examples are illustrative. The LED-resistor workflow uses manually entered quantities, is not extracted from the circuit graph, and is not design approval. RC transient and DC MNA mappings are user-authored rather than inferred; digital simulation and automatic model-to-simulator conversion remain unimplemented.

## Architecture

- `core/`: C++20 typed circuit/element model, bounded MNA and RC transient solver, C ABI v1, standalone CLI, and native CTest suite.
- `circuitmind/`: Python standard-library server, typed graph/workflows, unit validation, ctypes bridge, native solver adapters, KiCad adapters, persistence/snapshots.
- `web/`: browser-based local project intake, circuit-model editor, and bounded RC synthesis workflow.
- `tests/`: Python/API/native-ABI regression tests. Python/native tests compile the C++ shared library to a temporary directory; native CMake tests run separately via CTest. No actual KiCad/ngspice run is claimed.
- `docs/DEVELOPMENT_PLAN.md`: capability inventory and release criteria (not a phased delivery plan).
- `docs/CIRCUIT_ENGINE_ARCHITECTURE.md`: versioned subsystem contracts, C++/Python boundary, current RC synthesis workflow, and remaining platform scope.
- `docs/CIRCUIT_ENGINE_PROGRESS.md`: current implementation status, verified checks, and remaining limitations.

Projects live below `CIRCUITMIND_DATA_DIR/projects/<project-id>/`. Tool availability probes report installation/version only; they do not imply a validation operation ran.

## Tests

```bash
cmake -S . -B build -DCIRCUITMIND_BUILD_TESTS=ON
cmake --build build --config Release
ctest --test-dir build --output-on-failure
python -m unittest discover -s tests -v
```
