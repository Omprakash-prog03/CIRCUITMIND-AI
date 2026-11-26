# CIRCUITMIND AI

CIRCUITMIND AI is an engineering workbench for turning electronics requirements and supplied inventories into traceable, editable project artifacts. This repository currently delivers Milestone 1: an end-to-end, deterministic project-intake workflow with a browser UI, component inventory audit, architecture-specific feasibility checks, engineering calculation primitives, reproducible project files, and a downloadable ZIP.

## Run

Requires Python 3.11+; no third-party packages are needed.

```bash
python -m circuitmind
```

Then open <http://localhost:8000>. To change the bind address or data directory:

```bash
CIRCUITMIND_HOST=0.0.0.0 CIRCUITMIND_PORT=8000 CIRCUITMIND_DATA_DIR=./workspace python -m circuitmind
```

The server is intended for local use. It does not expose code execution or store API credentials. Do not expose it to an untrusted network.

## Milestone 1 scope and engineering honesty

The workflow accepts natural-language requirements, a line-based component inventory, and a selected discrete-logic or ESP32 hybrid direction. It creates a persistent project with machine-readable requirements, a parsed BOM, architecture and feasibility report, deterministic calculation record, design model, connectivity-check report, and a ZIP export. The design model is an editable project data format, **not** a KiCad schematic. This milestone does not claim schematic/PCB generation, datasheet verification, firmware compilation, SPICE simulation, hardware testing, or manufacturing validation. Missing installed engineering tools are reported as unavailable rather than simulated.

The initial clock benchmark intentionally reports that the sample inventory is insufficient for a complete discrete six-digit clock. It does not silently add unlisted ICs or claim unverified pin-level wiring.

## Architecture

- `circuitmind/`: Python standard-library HTTP application, typed core models, workflow/domain services, artifact generators, and integration discovery.
- `web/`: browser client served by the local application.
- `tests/`: regression tests for BOM parsing, architecture checks, calculations, and generated artifacts.
- `docs/DEVELOPMENT_PLAN.md`: milestone plan and acceptance criteria.

Projects are stored below `CIRCUITMIND_DATA_DIR/projects/<project-id>/`. The intake flow is deterministic and reproducible. Tool integrations will be added as isolated adapters in later milestones, with outputs and versions recorded in project reports.

## Run tests

```bash
python -m unittest discover -s tests -v
```
