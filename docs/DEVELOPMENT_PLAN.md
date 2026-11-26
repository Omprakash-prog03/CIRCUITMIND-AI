# CIRCUITMIND AI — capability scope and release criteria

This document is a capability inventory, not a phased delivery plan. The active delivery preference is to implement requested work in one integrated bulk effort, with tests and limitations maintained throughout. Each capability must keep engineering status explicit and add regression coverage.

## Project intake and feasibility (implemented)

- Local desktop-style browser workbench with project-description and BOM intake.
- Deterministic inventory parser, benchmark-specific discrete-clock feasibility analysis, architecture selection, and calculation primitives.
- Persist a project model and traceable report artifacts; list/reopen projects and export a ZIP.
- Add a typed circuit-model boundary and connectivity validation; report limitations accurately.
- Acceptance: no-dependency server starts; user can create project end to end; missing components are surfaced; outputs do not claim unrun checks.

## Versioned circuit graph and netlist interoperability (implemented slice; schematic work remains)

- Versioned schema v2 for model components, declared pins, placements, nets, and endpoints; v1 migration preserves only known fields and never infers symbols.
- Browser JSON editor, non-mutating schema/topology validation, atomic saves, SHA-256 snapshots, restore, and archive inclusion.
- KiCad legacy XML netlist generation gated on explicit symbol IDs and consistent graph/pin declarations; no static image is passed off as a schematic.
- A read-only installed-symbol audit resolves explicit library IDs through KiCad symbol tables, extracts declared pin metadata with source hashes, and compares it with the draft without changing pins. Inherited symbols are marked incomplete rather than silently expanded. Full datasheet-linked pinout provenance, a visual schematic editor, and netlist validation by KiCad remain unimplemented; this environment has no `kicad-cli` or installed symbol tables.

## Engineering integrations (KiCad ERC/DRC + ngspice adapters implemented; others pending)

- Read-only version probes remain separate from operations. KiCad CLI adapters operate on explicitly imported `.kicad_sch` and `.kicad_pcb` source files; ngspice accepts a restricted batch deck and runs `ngspice -b -r ...`.
- Operations use fixed argv/no shell, timeouts, restricted child environment, bounded captured logs, source uploads with confirmed replacement archiving, unique reports, an append-only run log, and evidence-based outcome interpretation. SPICE control/file-execution directives are rejected before invocation; raw result downloads are supported with a 64 MB post-run cap. PCB attachment checks only its header and does not parse or validate the design.
- DRC is reported clean only when the CLI exits successfully and both explicit zero DRC violation and zero unconnected-pad counts are parsed. A DRC result is not electrical correctness, hardware validation, or manufacturing readiness.
- These are not OS/container sandboxes; do not run against untrusted files. CPU/memory/filesystem quotas and stronger process isolation remain future work. Input filtering cannot prove parser safety.
- PlatformIO, Arduino CLI, firmware compilation, real native-tool execution in this environment, and hardened sandboxing remain unimplemented/unavailable. Tool probes are not validation results.

## Datasheets, component library, and calculations (partial capabilities implemented)

- Source-hashed PDF datasheet attachment with optional link to a saved component reference; optional Poppler `pdftotext` extraction retains page labels and source hash. Extraction is raw text only, not interpreted specifications or verified part data.
- Python provides dimension-checked SI normalization, graph mapping, workflow orchestration, and report formatting. DC MNA and generic RC transient entry points delegate numerical solving to the standalone C++20/CMake core through the versioned C ABI. The deterministic single-pole RC low-pass synthesis workflow emits a typed schema-v2 candidate graph and uses native C++ transient results. These are not nonlinear SPICE equivalence or inferred component models. Python also provides Ohm's law, resistor/capacitor networks, RC time constant, ideal stored energy, LED-resistor analysis, clock rate-error/drift budgets, and nearest-integer divider candidates from explicit inputs. Project reports retain model/input hashes and are surfaced stale after model edits. Inputs are explicit manual values; they are not inferred from names or auto-populated from the circuit model. The timebase calculation is not a divider design, digital-logic simulation, or oscillator specification.
- Verified part/variant and package identity, source-linked parameter extraction with review/approval, expanded calculation families (tolerance propagation beyond this LED case, Monte Carlo, filters, semiconductor models), and component compatibility checks remain pending.
- Explicit uncertainty, assumptions, and provenance on every inferred value.

## Firmware and hardware/software consistency

- Reproducible project scaffolding for supported MCU targets; typed GPIO assignment map shared with circuit model.
- Compile and test generated firmware using detected real toolchains; validate cross-layer pin/interface consistency.
- Firmware generation must be absent for all-discrete designs.

## Agent loop and model providers

- Provider-neutral structured tool-call interface, local Ollama/OpenAI-compatible adapters, capability detection, and permission controls.
- Bounded observe/plan/execute/check loop, action log, immutable snapshots, approval gates for destructive operations, and credential vault.
- Agent can call only implemented tools; all tool outputs are authoritative evidence.

## Editor, wiring, and delivery

- Interactive schematic and breadboard views driven from the same validated connectivity graph; undo/redo and autosave.
- BOM, wiring instructions, simulation/test/validation reports, PCB/manufacturing exports, project backup/restore.
- Desktop packaging, signing/update pipeline, CI matrix, security review, crash reporting and production deployment.

## Release gate

The digital clock benchmark must pass separate discrete and ESP32-hybrid acceptance suites. A result must state AI-generated, electrically checked, compiled, simulated, hardware tested, and manufacturing checked independently. No status is promoted without evidence from the relevant tool.
