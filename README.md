# CIRCUITMIND AI

CIRCUITMIND AI is a local engineering workbench for turning electronics requirements and supplied inventories into traceable project data. **Milestone 2 is implemented:** deterministic intake/feasibility, a versioned circuit graph with a JSON editor, graph validation, revision snapshots/restore, and a gated KiCad XML netlist exporter.

## Run

Requires Python 3.11+; no third-party packages are needed.

```bash
python -m circuitmind
```

Open <http://localhost:8000>. To bind for a preview or choose another project directory:

```bash
CIRCUITMIND_HOST=0.0.0.0 CIRCUITMIND_PORT=8000 CIRCUITMIND_DATA_DIR=./workspace python -m circuitmind
```

The server is intended for local use. It does not expose code execution or store API credentials. Do not expose it to an untrusted network.

## What works

- Intake accepts a natural-language project brief, a line-based component inventory, and discrete-logic or ESP32-hybrid direction.
- The benchmark feasibility check identifies insufficient stock instead of silently replacing supplied parts.
- Projects persist requirements, parsed BOM, architecture notes, assumptions/calculation examples, a validation ledger, and a ZIP export.
- Circuit-model schema v2 stores component references, symbol IDs, pin metadata, placement coordinates, nets, and pin-to-net terminals.
- The UI can edit this JSON model, ask the server for graph/schema checks, save revisions, and restore an earlier snapshot.
- KiCad XML netlist output is generated only when the model has components, consistent declared pins/nets, and caller-supplied symbol IDs. It is not a schematic export.
- ZIP exports include revision snapshots and project artifacts.

API routes for the model are `GET /api/projects/<id>/circuit`, `POST /api/projects/<id>/circuit/validate`, `PUT /api/projects/<id>/circuit`, `GET /api/projects/<id>/snapshots`, and `POST /api/projects/<id>/snapshots/restore` with `{"snapshot_id":"..."}`. Saving a model snapshots the prior version first. Unsupported model versions fail closed; schema v1 projects are migrated to v2 without inferred symbol IDs.

## Engineering limits (important)

Graph validation checks references, pin existence, duplicate assignments, and model structure only. It does **not** establish electrical correctness, datasheet provenance, symbol-library validity, ERC/DRC, or a physically drawable/editable KiCad schematic. The generated KiCad legacy XML netlist is not run through KiCad in this environment because `kicad-cli` is not installed. ESP32 firmware generation/compilation, SPICE simulation, PCB design, hardware testing, and manufacturing checks are not implemented. Validation artifacts distinguish these unrun checks; do not interpret a graph pass or netlist file as electrical approval.

The clock benchmark intentionally reports that the sample inventory is insufficient for the discrete six-digit design (including only three CD4511 display drivers) and does not invent pin wiring. Calculation examples are illustrative, not approved design values.

## Architecture

- `circuitmind/`: Python standard-library server, domain/workflow services, schema migration and validation, KiCad XML adapter, persistence/snapshots, integration discovery.
- `web/`: browser-based local project intake and circuit-model editor.
- `tests/`: regression tests for BOM parsing, engineering calculation primitives, circuit schema/migration/export, snapshots, and project artifacts.
- `docs/DEVELOPMENT_PLAN.md`: milestone roadmap and release criteria.

Projects live below `CIRCUITMIND_DATA_DIR/projects/<project-id>/`. Tool availability probes report installation/version only; they do not imply a validation operation ran.

## Tests

```bash
python -m unittest discover -s tests -v
```
