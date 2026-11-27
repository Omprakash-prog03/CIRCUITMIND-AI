# Circuit Engine architecture and migration boundary

## Current architecture

The repository contains a Python 3.11 standard-library HTTP/UI application, a schema-v2 project circuit graph, Python engineering/report adapters, KiCad/ngspice adapters, and a standalone C++20 numerical core. The native core owns numerical computation for bounded DC MNA (linear elements plus separate piecewise-linear active-set and bounded Shockley Newton diode models), linear R/L/C AC sweeps, RC/RLC backward-Euler transient entry points, bounded combinational digital truth tables, and sampled digital waveforms with rising- or falling-edge D flip-flops, including graph-mapped analog endpoints and RC synthesis. Python validates and marshals explicit inputs, maps graph bindings, and formats evidence reports; it does not execute the numerical DC/AC/transient/digital solve loops. Simple formula calculations and E-series candidate orchestration remain in Python because they are small workflow/report operations, not the performance-critical solver. The project is CPU-only: CMake enables C++20 only and has no CUDA or required GPU dependency. The application is not production-ready.

### Layer ownership and versioned contracts

| Layer | Contract / owner | Current integration |
|---|---|---|
| Requirement interpreter | Typed, versioned requirements; separate hard constraints, preferences, and assumptions | RC workflow request v1 is structured input (cutoff, preferred R, E-series, tolerances, maximum nominal error). No free-text or AI interpretation is claimed. |
| Architecture synthesizer | Candidate topology + rationale + stable workflow version | `synthesize_rc_lowpass`, workflow v2, creates the one-pole ideal-source / series-R / shunt-C candidate. |
| Component intelligence | Verified identity, specs, provenance, operating limits | Not implemented. E-series values are nominal candidates only; no manufacturer, stock, footprint, rating, or datasheet is selected. |
| Canonical graph | Project `circuit_model.json`, schema v2, graph/hash | Candidate graph uses schema v2 and optional typed `electrical_model` v1 on each component. Candidate is an artifact by default; explicit apply saves through the existing snapshot-aware store. |
| C++ numerical core | C++20 typed `Circuit`, element records, bounded MNA | `core/`: linear DC R/V/I MNA, bounded piecewise-linear diode active-set solves, and explicitly parameterized Shockley diode Newton solves; independent-source sweeps support either diode model separately (500 elements, 150 non-ground nodes, 180 unknowns, 200 sweep points); linear R/L/C AC phasor sweeps (200 elements, 64 nodes, 80 unknowns, 200 frequencies); fixed-step RC/RLC backward-Euler transient (100 elements, 24 nodes, 32 unknowns, 2,000 steps); Boolean combinational gate truth tables (NOT/BUF/AND/OR/XOR/NAND/NOR/XNOR, at most 10 inputs), and sampled Boolean waveforms with explicit initial state and rising- or falling-edge DFF capture (at most 32 inputs and 10,000 samples); both digital APIs support 32 outputs, 512 gates, and 256 nets. Digital evaluation uses a bounded CPU topological pass. DFF state is updated only at input sample boundaries; propagation delay, metastability, timing constraints, and analog behavior are not modeled. Shockley solves use explicit saturation current, ideality factor, and temperature, with bounded parameters/exponents and at most 100 voltage-limited iterations. The current 180-unknown ceiling makes dense CPU elimination a bounded tradeoff; Eigen, BLAS/OpenBLAS, and SuiteSparse were not available in this environment, so no matrix dependency or sparse backend was added without a demonstrated need. Mixing diode models, controlled sources, charge storage, and general nonlinear device models are unsupported. |
| Python/native interface | Stable C ABI version 1 with versioned DC, DC sweep, transient, AC, truth-table, and sampled-digital-waveform request/results; a distinct waveform gate structure preserves the existing truth-table gate layout; `ctypes` adapter | `core/include/circuitmind/c_api.h` and `circuitmind.engineering.cpp_engine`. Typed SI values, RMS source phasor magnitudes/phases, and node-indexed elements cross the boundary; Python performs unit validation/marshalling, not the native solve. Missing or incompatible native library is an explicit error (API 503), never a Python fallback. |
| Mathematical analysis | Python requirement/candidate arithmetic plus native solver evidence | RC nominal cutoff, independent tolerance bounds, time constant and peak ideal step resistor power are calculated in Python; DC, AC complex MNA, transient state solves/residuals, and Boolean truth-table evaluation are native C++. |
| Simulation orchestration | Explicit model/config, reproducible result and limits | Generated graph mapping feeds C++ BE test: 0→1 V at t=0, capacitor initial voltage 0 V, Δt=τ/20, stop=5τ (100 intervals). This is not ngspice. |
| Optimization | Bounded candidates and objective/constraints | RC synthesis exhaustively scans supported E-series resistance candidates, pairs each with its log-nearest capacitor (the other capacitors at that R are dominated for cutoff error), honors the maximum cutoff-error bound when feasible, then minimizes log-distance from preferred R; when infeasible it returns the closest cutoff candidate marked fail. This is a bounded single-objective candidate search, not general or multi-objective optimization. |
| Verification | Evidence-linked findings/status/scope | Graph topology, nominal cutoff constraint, calculated tolerance bounds, and analytical-vs-C++ endpoint comparison. No ERC, manufacturer limits, physical, or manufacturing verification. |
| Orchestration | Auditable, permissioned workflow | API stores versioned design report and append-only run record. Applying the candidate is opt-in, browser-confirmed, and snapshots the prior graph. No autonomous agent is claimed. |
| Visualization | Graph/report-derived UI | Synthesis, AC sweep, graph-bound RC transient, digital truth-table, and digital-waveform dialogs; RC low-pass, half-adder, and two-bit counter examples; saved artifacts; node-selectable AC/transient charts and digital output charts/tables; and current JSON editor. No graphical schematic canvas yet. |

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

The workflow scans every supported E-series R candidate and evaluates its nearest-log-distance E-series C; it first selects a pair meeting the maximum nominal cutoff error with R closest to the preferred value, or otherwise returns the globally closest cutoff pair as a visible failure. It emits a schema-v2 graph with structured electrical models, validates the graph, computes `fc = 1/(2πRC)` and tolerance corners, then runs the explicit graph-derived testbench in the C++ core. For the reference inputs above: R=10 kΩ, C=16 nF, nominal fc≈994.7183943 Hz; C++ backward Euler produces Vout(5τ)≈0.9923955100 V for Δt=τ/20, versus the analytical `1-exp(-5)` reference≈0.9932620530 V. The deviation is expected from the specified coarse fixed step; timestep adequacy is not certified.

With `apply_to_circuit: false`, the saved circuit is unchanged and the candidate remains in the report artifact. With `true`, the candidate replaces the saved graph only after explicit user action and the previous graph is snapshotted. Symbol identities are deliberately null; netlist export is blocked pending exact symbol resolution. The generated V1 is a testbench stimulus, not hardware.

## Bounded DC source sweep

`POST /api/projects/<id>/simulations/dc-sweep` accepts an explicit ideal R/V/I netlist with optional explicit piecewise-linear or Shockley diodes (one model family per solve) and a sweep specification that names one independent voltage or current source, typed start/stop values, point count, and linear/log scale. `POST /api/projects/<id>/simulations/dc-sweep/from-model` obtains connectivity from the saved circuit graph and requires explicit per-component bindings/exclusions. The Python bridge sends the normalized circuit, selected source index, and 2–200 monotonic SI values through the versioned C sweep ABI; C++ applies each source value and calls the native DC MNA solve per point. Python performs request validation/unit conversion and report formatting, not the sweep loop or numerical solve. The result stores voltage traces, equation residuals, source metadata, and input/mapping hashes; history exposes only a bounded final-point summary. At most 200 points are accepted. This is not nonlinear continuation, transfer-curve extraction for nonlinear devices, or transient simulation.

## Bounded RC/RLC transient analysis

The existing graph-bound RC endpoint remains `POST /api/projects/<id>/simulations/transient-rc/from-model`; the sibling `.../transient-rlc/from-model` permits explicit ideal inductors. Both accept `mapping`, `time_step`, and `stop_time`; every reactive element has an explicit initial condition (`initial_voltage` for C, `initial_current` for L). The native solver uses fixed-step backward Euler: capacitor companion conductance `C/Δt` with history current `-(C/Δt)·V_prev`, and inductor companion conductance `Δt/L` with history current `I_prev`. Initial ideal constraints are solved and conflicting initial conditions/source constraints fail closed. The solver returns node-voltage traces; the report adapter derives capacitor-voltage and inductor-current traces from the solved states. The bounded limits remain 100 elements, 24 non-ground nodes, 32 effective unknowns, and 2,000 steps. This is an ideal linear RLC model without parasitics, nonlinear devices, adaptive timestep control, or SPICE-equivalence claims.

## Bounded AC frequency sweep

`POST /api/projects/<id>/simulations/ac-sweep` accepts an explicit linear R/L/C netlist and a bounded linear or logarithmic sweep. `POST /api/projects/<id>/simulations/ac-sweep/from-model` uses the saved graph only for connectivity and requires explicit per-component types, values, ordered pins, AC source RMS magnitudes/phases, or reasoned exclusions. The graph mapping does not infer component models from descriptions.

Example netlist shape:

```json
{
  "ground": "0",
  "frequency_sweep": {
    "start_frequency": {"value": 10, "unit": "Hz"},
    "stop_frequency": {"value": 100000, "unit": "Hz"},
    "points": 101,
    "scale": "log"
  },
  "elements": [
    {"type": "voltage_source", "reference": "V1", "nodes": ["VIN", "0"],
     "voltage": {"value": 1, "unit": "V"}, "phase_degrees": 0},
    {"type": "resistor", "reference": "R1", "nodes": ["VIN", "OUT"],
     "resistance": {"value": 10000, "unit": "ohm"}},
    {"type": "capacitor", "reference": "C1", "nodes": ["OUT", "0"],
     "capacitance": {"value": 16, "unit": "nF"}}
  ]
}
```

The C++ core solves complex nodal equations per frequency using `Y_R = 1/R`, `Y_C = jωC`, and `Y_L = 1/(jωL)`; source magnitude and phase follow RMS phasor convention with `e^(jωt)`. It returns node-voltage real/imaginary, magnitude/phase, and normalized equation residual traces. Work is capped at 200 elements, 64 non-ground nodes, 80 total unknowns, and 200 strictly increasing positive frequency points. Inductors are supported as ideal AC and backward-Euler transient elements, with explicit initial current for transient runs; DC operating-point inductor behavior, frequency-dependent models, nonlinear behavior, noise, tolerance analysis, and general SPICE semantics are not supported. Reports are stored with input hashes and bounded summaries appear in simulation history.

## Bounded digital simulation

`POST /api/projects/<id>/simulations/digital-truth-table` accepts an explicit Boolean combinational network. The native C++ core validates single-driver topology, rejects undriven nets and combinational loops, topologically orders gates, and evaluates all input combinations. Limits are 10 primary inputs, 32 outputs, 512 gates, and 256 nets.

`POST /api/projects/<id>/simulations/digital-waveform` accepts `inputs`, `outputs`, gates, ordered `samples`, and `sample_interval_s`. Gate type `dff` uses `inputs: [D, CLK]` and an explicit integer `initial_value`; optional `edge: "rising"|"falling"` selects the sampled clock transition (rising by default); clock history begins at 0 before sample zero, and Q outputs are available in that returned frame. Combinational logic is evaluated before and after simultaneous DFF state updates. The C++ core validates sample shape/binary values, topology, and positive finite interval; samples are limited to 10,000 and primary inputs to 32. Both APIs support 512 gates, 256 nets, and 32 outputs and persist normalized input hashes and bounded history summaries through the Python API layer. A distinct C waveform-gate structure keeps the existing truth-table gate ABI layout unchanged.

These are ideal Boolean analyses, not HDL/timing verification: propagation delay, setup/hold, metastability, electrical thresholds/loading, analog behavior, and analog/digital co-simulation remain unsupported.

## Evidence semantics

- `candidate-meets-nominal-cutoff-constraint` means the nominal calculated cutoff error meets only the user-entered bound. It is not safety, component qualification, or design approval.
- Tolerance corners assume independent symmetric R/C tolerances; no datasheet specification or yield is verified.
- The transient endpoint comparison is evidence for the implemented ideal RC model only, not physical components.
- The C++ core reports equation residuals and fails closed on malformed, singular, incompatible, non-finite, or over-budget requests. AC results are ideal linear R/L/C phasor responses only; they are not an ngspice run or physical model validation.
- Existing Python DC MNA and generic transient entry points now delegate to the native core and retain Python report formatting. Current regression suites pass, but no independent cross-engine parity test remains because the Python numerical kernels have been removed. The APIs still require explicit idealized netlists/mappings and do not validate physical circuits.

## Remaining platform scope (context, not a phased delivery plan)

The user's active delivery preference is to implement requested work as one integrated bulk effort rather than splitting it into milestone deliveries. This document records current boundaries and gaps; the list below is not a sequence of partial deliverables. Remaining scope includes richer canonical graph/model semantics in C++, general nonlinear/SPICE-backed simulation, full event-driven digital timing and mixed-signal runtime, provenance-backed component intelligence, symbolic and advanced optimization, and production hardening (cross-platform packaging/CI, sparse-solver evaluation as limits grow, quotas, cancellation, controlled concurrency, and security review).

Any implementation must keep deterministic reference tests, preserve API/report behavior where applicable, bound resource use, and state unsupported scope explicitly. No platform-level production-readiness claim is made.
