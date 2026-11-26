# CIRCUITMIND AI — development plan

The repository began as a README-only shell. Implement in vertical slices; each milestone must keep engineering status explicit and add regression tests.

## Milestone 1 — Project intake and feasibility (implemented in this branch)

- Local desktop-style browser workbench with project-description and BOM intake.
- Deterministic inventory parser, benchmark-specific discrete-clock feasibility analysis, architecture selection, and calculation primitives.
- Persist a project model and traceable report artifacts; list/reopen projects and export a ZIP.
- Add a typed circuit-model boundary and connectivity validation; report limitations accurately.
- Acceptance: no-dependency server starts; user can create project end to end; missing components are surfaced; outputs do not claim unrun checks.

## Milestone 2 — Versioned circuit graph and netlist interoperability (implemented slice; schematic work remains)

- Versioned schema v2 for model components, declared pins, placements, nets, and endpoints; v1 migration preserves only known fields and never infers symbols.
- Browser JSON editor, non-mutating schema/topology validation, atomic saves, SHA-256 snapshots, restore, and archive inclusion.
- KiCad legacy XML netlist generation gated on explicit symbol IDs and consistent graph/pin declarations; no static image is passed off as a schematic.
- Symbol library resolution against actual installed libraries, pinout provenance, visual schematic editor, and KiCad CLI/ERC execution remain unimplemented. This environment has no `kicad-cli`; generated netlist bytes are not CLI-validated.

## Milestone 3 — Engineering integrations (KiCad ERC slice implemented; others pending)

- Read-only version probes remain separate from operations. A trusted local `kicad-cli sch erc` adapter is now available for a user-imported `.kicad_sch` file, with timeout, fixed argv/no shell, restricted child environment, captured output, unique reports, append-only run log, exit status, and explicit ERC-message parsing.
- Imported schematics are size-limited, immutable via API, and not called validated until the real CLI operation runs. A zero process exit without a recognized zero-message summary is reported as unparsed, not clean.
- This is not an OS/container sandbox; do not use it on untrusted project files. Resource limits beyond a wall-clock timeout and strong process isolation remain future work.
- ngspice, PlatformIO, Arduino CLI, firmware compilation, KiCad DRC, and hardened sandboxing remain unimplemented. Missing tools report unavailable; a version probe is not a test result.

## Milestone 4 — Datasheets, component library, calculations

- Datasheet import and source-linked extraction; verified part/variant and package identity.
- Expand tested calculation library (tolerances, operating corners, unit-aware values) and component compatibility checks.
- Explicit uncertainty, assumptions, and provenance on every inferred value.

## Milestone 5 — Firmware and hardware/software consistency

- Reproducible project scaffolding for supported MCU targets; typed GPIO assignment map shared with circuit model.
- Compile and test generated firmware using detected real toolchains; validate cross-layer pin/interface consistency.
- Firmware generation must be absent for all-discrete designs.

## Milestone 6 — Agent loop and model providers

- Provider-neutral structured tool-call interface, local Ollama/OpenAI-compatible adapters, capability detection, and permission controls.
- Bounded observe/plan/execute/check loop, action log, immutable snapshots, approval gates for destructive operations, and credential vault.
- Agent can call only implemented tools; all tool outputs are authoritative evidence.

## Milestone 7 — Editor, wiring, and delivery

- Interactive schematic and breadboard views driven from the same validated connectivity graph; undo/redo and autosave.
- BOM, wiring instructions, simulation/test/validation reports, PCB/manufacturing exports, project backup/restore.
- Desktop packaging, signing/update pipeline, CI matrix, security review, crash reporting and production deployment.

## Release gate

The digital clock benchmark must pass separate discrete and ESP32-hybrid acceptance suites. A result must state AI-generated, electrically checked, compiled, simulated, hardware tested, and manufacturing checked independently. No status is promoted without evidence from the relevant tool.
