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
- Python provides dimension-checked SI normalization, graph mapping, workflow orchestration, and report formatting. DC MNA and generic RC transient entry points delegate numerical solving to the standalone C++20/CMake core through the versioned C ABI. Bounded linear R/L/C AC frequency sweeps also execute in the native core and are exposed through explicit-netlist and graph-bound report APIs. The deterministic single-pole RC low-pass synthesis workflow emits a typed schema-v2 candidate graph and uses native C++ transient results. These are not nonlinear SPICE equivalence or inferred component models. Python also provides Ohm's law, resistor/capacitor networks, RC time constant, ideal stored energy, LED-resistor analysis, clock rate-error/drift budgets, and nearest-integer divider candidates from explicit inputs. Project reports retain model/input hashes and are surfaced stale after model edits. Inputs are explicit manual values; they are not inferred from names or auto-populated from the circuit model. The timebase calculation is not a divider design, digital-logic simulation, or oscillator specification.
- Native C++ implements bounded combinational Boolean truth tables and sampled digital waveforms with selectable rising/falling-edge D flip-flops, explicit initial state, and hash-linked API reports. The browser project view provides explicit AC-netlist analysis with selectable node magnitude/phase charts, graph-bound RC/RLC transient analysis with selectable node-voltage and inductor-current traces, and digital explicit-netlist editors with half-adder and two-bit counter examples. Sampled state is not a full sequential runtime: propagation timing, setup/hold, counters beyond composing DFFs, event scheduling, and mixed-signal simulation remain pending.
- A bounded user-limit comparison is available for saved graph-bound DC operating points and sampled reactive peaks in RC/RLC transients, covering explicit absolute terminal voltage/current/power or capacitor-voltage/inductor-current bounds with provenance; entered limits are not verified ratings and transient inter-sample peaks are not estimated. Verified part/variant and package identity, source-linked parameter extraction with review/approval, expanded calculation families (tolerance propagation beyond this LED case, Monte Carlo, filters, broad semiconductor model libraries), and datasheet-backed component compatibility checks remain pending.
- Explicit uncertainty, assumptions, and provenance on every inferred value.

## Firmware and hardware/software consistency

- Initial ESP32 firmware-scaffold slice implemented: explicit `esp32dev` target and user-provided GPIO-to-saved-net mappings generate a hashed PlatformIO config, C++ pin-mode starter, and manifest. The endpoint is restricted to projects explicitly marked `architecture: esp32`; discrete projects cannot request firmware scaffolding. Generic classic-ESP32 pin availability, input-only restrictions, duplicate assignments, strapping pins, and UART0 collisions are checked or surfaced as warnings.
- Still pending: compile and test generated firmware with a detected real toolchain; validate a specific board/module revision, voltage/current compatibility, and cross-layer peripheral behavior; generate application logic from reviewed requirements.
- Firmware generation remains absent for all-discrete designs.

## Agent loop and model providers (initial deterministic tool slice implemented)

- A provider-neutral registry and bounded dispatcher expose read-only project summary, circuit schema/topology validation, and declared connectivity tools. Calls accept no free-form arguments, are limited to eight per request, and produce a bounded 1,000-record SHA-256-linked audit log with hashes and timing; record edits and tail truncation are detectable, but the unsigned chain is not protection against complete rewriting. Tool outputs do not claim electrical validation.
- The fixed `circuit_review` workflow observes project metadata, executes topology and connectivity tools, and checks identity/hash consistency. It saves a compact report and audit trail; this is deterministic orchestration, not an AI-generated plan.
- A separate `circuit.replace_model` proposal flow stages an explicit, schema/topology-valid candidate against the current model hash; it expires after 30 minutes and applies only after a separate one-time approval request. Stale and replayed proposals are rejected. The local API approval is an explicit action, not authenticated identity.
- Opt-in provider configuration and model-list capability probes are implemented for local Ollama and OpenAI-compatible endpoints, restricted to loopback HTTP; probes send no project or prompt data. A plan-only adapter can send an explicit user prompt and tool policy to the configured local model, validate a bounded allowlisted read-only plan, persist its hashes, and wait for user review before execution. A separate one-time execution endpoint verifies the saved plan hash, runs only those calls, and checks project identity/topology/hash consistency. An explicitly requested optional narrative may send only compact saved checks/counts to the loopback model; it is stored as unverified prose with cited evidence keys and exact cited values under a report digest. The source execution report and audit link are rechecked after generation to prevent a time-of-check/time-of-use mismatch. Bounded execution history now lists recent reports and only returns a selected report after verifying its digest and audit link; the summary-history route revalidates stored summary digests, cited values, source report integrity, and audit links on reload. Still pending: iterative multi-turn observe/replan cycles and provider-grounded synthesis from tool results, authenticated approval identity, managed credential storage, and richer evidence-aware tools.
- The dispatcher has no shell or arbitrary path tool. Circuit edits are possible only through the separately approved candidate flow; no values are inferred.

## Editor, wiring, and delivery

- The JSON circuit editor now provides bounded grouped undo/redo, browser-local draft autosave/restore/discard, and an interactive read-only graph map of declared component-pin/net endpoints; server saves remain explicit revision operations. A true schematic/symbol view, breadboard view, graph-aware visual editing, and collaborative/remote persistence remain unimplemented.
- Hash-linked JSON/CSV pin-to-net connectivity schedules export declared graph endpoints and unconnected pins, but do not describe physical routes or wire sizing. Project ZIP backup import now creates a new project ID after bounded archive/path, project/model, topology, and snapshot-integrity validation; artifacts are preserved without artifact-specific validation. Verified BOM generation, physical wiring instructions, unified delivery reports, and PCB/manufacturing exports remain pending.
- GitHub Actions CI is configured to run native CMake/CTest/self-test and Python/JavaScript checks on Ubuntu and Windows with Python 3.11 and 3.12; the first hosted matrix run is pending. macOS coverage remains unimplemented, as do desktop packaging, signing/update pipeline, security review, crash reporting and production deployment.

## Release gate

The digital clock benchmark must pass separate discrete and ESP32-hybrid acceptance suites. A result must state AI-generated, electrically checked, compiled, simulated, hardware tested, and manufacturing checked independently. No status is promoted without evidence from the relevant tool.
