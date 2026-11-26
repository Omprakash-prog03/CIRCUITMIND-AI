# Circuit Engine architecture and migration boundary

## Current architecture

The repository contains a Python 3.11 standard-library HTTP/UI application, a schema-v2 project circuit graph, Python engineering/report adapters, KiCad/ngspice adapters, and a standalone C++20 numerical core. The native core owns numerical computation for the bounded linear DC MNA and RC backward-Euler entry points, including graph-mapped endpoints and RC synthesis. Python validates and marshals explicit inputs, maps graph bindings, and formats the established evidence reports; it does not solve these systems. Other engineering calculations and workflow orchestration remain in Python, so the platform is not wholly C++-implemented or production-ready.

### Layer ownership and versioned contracts

| Layer | Contract / owner | Current integration |
|---|---|---|
| Requirement interpreter | Typed, versioned requirements; separate hard constraints, preferences, and assumptions | RC workflow request v1 is structured input (cutoff, preferred R, E-series, tolerances, maximum nominal error). No free-text or AI interpretation is claimed. |
| Architecture synthesizer | Candidate topology + rationale + stable workflow version | `synthesize_rc_lowpass`, workflow v1, creates the one-pole ideal-source / series-R / shunt-C candidate. |
| Component intelligence | Verified identity, specs, provenance, operating limits | Not implemented. E-series values are nominal candidates only; no manufacturer, stock, footprint, rating, or datasheet is selected. |
| Canonical graph | Project `circuit_model.json`, schema v2, graph/hash | Candidate graph uses schema v2 and optional typed `electrical_model` v1 on each component. Candidate is an artifact by default; explicit apply saves through the existing snapshot-aware store. |
| C++ numerical core | C++20 typed `Circuit`, element records, bounded MNA | `core/`: linear DC R/V/I MNA (up to 500 elements, 150 non-ground nodes, 180 total unknowns) and fixed-step linear RC backward-Euler transient (up to 100 elements, 24 non-ground nodes, 32 unknowns, 2,000 steps). Dense Gaussian elimination is intentionally bounded; sparse, nonlinear, and AC are not implemented. |
| Python/native interface | C ABI v1 requests + legacy DC result v1 and expanded DC result v2; `ctypes` adapter | `core/include/circuitmind/c_api.h` and `circuitmind.engineering.cpp_engine`. Typed SI values and node-indexed elements cross the boundary; Python performs unit validation/marshalling, not the native solve. Missing library is an explicit error (API 503), never a Python fallback. |
| Mathematical analysis | Python requirement/candidate arithmetic plus native solver evidence | RC nominal cutoff, independent tolerance bounds, time constant and peak ideal step resistor power are calculated in Python; the transient state solve and equation residual are from C++. |
| Simulation orchestration | Explicit model/config, reproducible result and limits | Generated graph mapping feeds C++ BE test: 0→1 V at t=0, capacitor initial voltage 0 V, Δt=τ/20, stop=5τ (100 intervals). This is not ngspice. |
| Optimization | Bounded candidates and objective/constraints | Not implemented. Nearest E-series rounding is deterministic candidate selection, not global or multi-objective optimization. |
| Verification | Evidence-linked findings/status/scope | Graph topology, nominal cutoff constraint, calculated tolerance bounds, and analytical-vs-C++ endpoint comparison. No ERC, manufacturer limits, physical, or manufacturing verification. |
| Orchestration | Auditable, permissioned workflow | API stores versioned design report and append-only run record. Applying the candidate is opt-in, browser-confirmed, and snapshots the prior graph. No autonomous agent is claimed. |
| Visualization | Graph/report-derived UI | Synthesis dialog, result summary, artifacts, current JSON editor. No graphical schematic canvas or plotted waveforms yet. |

## Build and native interface

The core is standard C++20 with no third-party dependency in this initial slice:

```bash
cmake -S . -B build -DCIRCUITMIND_BUILD_TESTS=ON
cmake --build build --config Release
ctest --test-dir build --output-on-failure
build/core/circuitmind_engine_cli --version
build/core/circuitmind_engine_cli --self-test
```

The shared library reports ABI version 1 and exposes versioned request functions for DC and transient operations. DC preserves the original v1 node-voltage result and adds a v2 result with per-element voltages/currents/powers, residuals, power balance, pivot metrics, and unknown count. C structs use fixed-width integer fields, SI-valued doubles, caller-owned buffers, request-version checks, bounded output capacity, and exception-to-error-buffer translation. The CLI is independent of Python and has a reference RC self-test. Python discovers the shared library in standard build locations or through `CIRCUITMIND_ENGINE_LIB`.

The C ABI accepts an indexed, typed circuit snapshot derived from the canonical project graph; it does not store a second project graph or inspect component descriptions. The Python generic graph simulation routes still require their explicit mapping. The synthesis workflow is allowed to map its own typed output models because those values were produced by the deterministic workflow, not inferred from free-text component descriptions.

## First complete vertical workflow

`POST /api/projects/<id>/designs/rc-low-pass` accepts exactly:

```json
{
  "request_version": 1,
  "apply_to_circuit": false,
  "inputs": {
    "cutoff_frequency": {"value": 1000, "unit": "Hz"},
    "preferred_resistance": {"value": 10000, "unit": "ohm"},
    "series": "E24",
    "resistor_tolerance_percent": 5,
    "capacitor_tolerance_percent": 10,
    "max_cutoff_error_percent": 5
  }
}
```

The workflow deterministically rounds the preferred R and derived C to the requested E-series, emits a schema-v2 graph with structured electrical models, validates the graph, computes `fc = 1/(2πRC)` and tolerance corners, then runs the explicit graph-derived testbench in the C++ core. For the reference inputs above: R=10 kΩ, C=16 nF, nominal fc≈994.7183943 Hz; C++ backward Euler produces Vout(5τ)≈0.9923955100 V for Δt=τ/20, versus the analytical `1-exp(-5)` reference≈0.9932620530 V. The deviation is expected from the specified coarse fixed step; timestep adequacy is not certified.

With `apply_to_circuit: false`, the saved circuit is unchanged and the candidate remains in the report artifact. With `true`, the candidate replaces the saved graph only after explicit user action and the previous graph is snapshotted. Symbol identities are deliberately null; netlist export is blocked pending exact symbol resolution. The generated V1 is a testbench stimulus, not hardware.

## Evidence semantics

- `candidate-meets-nominal-cutoff-constraint` means the nominal calculated cutoff error meets only the user-entered bound. It is not safety, component qualification, or design approval.
- Tolerance corners assume independent symmetric R/C tolerances; no datasheet specification or yield is verified.
- The transient endpoint comparison is evidence for the implemented ideal RC model only, not physical components.
- The C++ core reports numerical residuals and fails closed on malformed, singular, incompatible, non-finite, or over-budget requests.
- Existing Python DC MNA and generic transient entry points now delegate to the native core and retain Python report formatting. Current regression suites pass, but no independent cross-engine parity test remains because the Python numerical kernels have been removed. The APIs still require explicit idealized netlists/mappings and do not validate physical circuits.

## Remaining platform scope (context, not a phased delivery plan)

The user's active delivery preference is to implement requested work as one integrated bulk effort rather than splitting it into milestone deliveries. This document records current boundaries and gaps; the list below is not a sequence of partial deliverables. Remaining scope includes richer canonical graph/model ownership in C++, AC/DC sweep and nonlinear/SPICE-backed simulation, discrete-event digital/mixed-signal support, provenance-backed component intelligence, symbolic analysis and bounded optimization, and production hardening (cross-platform packaging/CI, sparse-solver evaluation, quotas, cancellation, thread safety, and security review).

Any implementation must keep deterministic reference tests, preserve API/report behavior where applicable, bound resource use, and state unsupported scope explicitly. No platform-level production-readiness claim is made.
