# Circuit Engine development progress

**Status date:** 2026-11-26

**Current delivery:** foundation milestone in progress; not production-ready.

## Repository inspection summary

The inspected checkout is a Python 3.11 standard-library web application with a browser UI, schema-v2 circuit graph, snapshots, Python unit-aware calculations, Python dense MNA and RC backward-Euler solvers, and adapters for KiCad/ngspice. It has project/API regression tests but had no C++ build, native binding, SymPy/AI agent layer, CMake configuration, or C++ tests before this work. Existing functions and adapters are being preserved during migration. The current master workflow and detailed subsystem boundaries are in [`CIRCUIT_ENGINE_ARCHITECTURE.md`](CIRCUIT_ENGINE_ARCHITECTURE.md).

## Completed and verified in this milestone

- Added a portable CMake/C++20 core under `core/`, built from typed `Circuit`, `Element`, and solver request/result types.
- Added bounded dense MNA for linear DC resistor/independent-source circuits and linear RC backward-Euler transient analysis. Capacitors have explicit initial-voltage constraints; independent sources support DC values or one explicit step waveform.
- Added numerical guardrails: node/element/unknown/step limits, finite-value checks, source/capacitor consistency checks, pivot/singularity rejection, integer timestep grids, and equation residual reporting.
- Added versioned C ABI v1 with caller-owned buffers, input/output capacity checks, and error translation; Python bridges through `ctypes` and does not silently fall back to a Python solver.
- Added standalone CLI (`--version`, `--self-test`) and CTest reference tests (voltage divider, RC step response, singular/limit rejections, C ABI round trip).
- Added a typed, deterministic single-pole RC low-pass synthesis workflow: E-series nominal selection, schema-v2 graph with typed electrical model v1 metadata, nominal/tolerance analysis, graph validation, C++ transient test, report hashing, and opt-in snapshot-aware graph application.
- Added explicit no-claims for unverified component identity/ratings, free-text AI interpretation, physical schematic/ERC, and ngspice equivalence.
- Verification completed: CMake configure/build succeeded; CTest passed 1/1 native test executable; CLI self-test passed; Python suite passed **89 tests**; Python compileall, JavaScript syntax check, and `git diff --check` passed.

## Incomplete

- Existing Python DC MNA and generic transient implementations are still callable legacy paths. The new synthesis workflow uses the C++ transient solver, but the whole platform is not yet C++-authoritative.
- Native solver uses bounded dense matrices (maximum 32 MNA unknowns); sparse factorization, conditioning estimates beyond scaled pivot rejection, controlled sources, inductors, nonlinear devices, adaptive integration, and AC analysis are not implemented.
- The C++ core consumes a normalized typed circuit snapshot through C ABI; it does not yet own the full schema-v2 graph, hierarchical sheets, power domains, or electrical pin compatibility rules.
- Component catalogue/datasheet intelligence, SymPy derivations, requirement-to-topology AI, optimization, digital/mixed-signal engines, and autonomous repair are not implemented.
- Python binding has transient integration for the first workflow; DC C ABI exists but is not yet wrapped or used by the existing DC API.
- No native install/package target, Windows build, CI workflow, performance benchmark suite, ABI compatibility matrix, thread-safe job queue, or cancellation facility yet.

## Blocked / environmental

- No trusted manufacturer libraries/datasheet model corpus is configured, so component identity and model qualification cannot be claimed.
- This environment has g++ 12.2.0. CMake was installed into a temporary local virtual environment for validation; no runtime Python package dependency was added. KiCad/ngspice validation claims remain limited to the existing documented adapters and actual installed-tool availability.

## Migration plan and gates

1. **Core/API foundation — in progress.** Keep C++ independent and C ABI versioned; add unit, integration, and numerical reference tests for every exposed behavior.
2. **Migrate legacy solvers.** Port existing Python graph-bound DC and transient endpoints to native C++, retain old report contracts, compare parity on all current regression fixtures, then remove Python numerical kernels only after tests establish parity.
3. **Canonical model evolution.** Introduce a deliberate schema migration for richer typed graph fields, pin electrical types/limits, datasheet/model provenance, hierarchical nets, and simulation configuration. No implicit name-based model lookup.
4. **Analog capability expansion.** Add AC/DC sweep and actual ngspice backend integration with generated deck, executable/version capture, parsing, raw data retention, and reference comparisons.
5. **Digital/mixed-signal.** Add bounded discrete-event logic simulation and explicit interface models while retaining one project graph and report identity.
6. **Python engineering intelligence.** Add typed requirements/questions, symbolic calculations, candidate generation and optimization only when component/source evidence and solver interfaces are defined.
7. **Production hardening.** Cross-platform builds, ABI/install tests, sparse solver evaluation, deterministic benchmark circuits, resource limits, cancellation, security review, CI and reproducible packaging.

No “production-grade,” “fully autonomous,” “SPICE-equivalent,” or “verified hardware” status should be used until the corresponding gates have passed with retained evidence.
