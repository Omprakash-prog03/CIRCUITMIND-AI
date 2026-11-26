# Deterministic engineering-math slice

## Current implemented scope

`circuitmind.engineering` is the Python calculation/workflow layer. It does not infer values from a component label. The repository now also has an independent C++20 core in `core/`; the bounded RC-low-pass synthesis workflow invokes that native core through C ABI v1 for its authoritative transient result. Existing Python DC MNA and generic transient functions remain legacy implementations pending migration and must not be confused with the new C++ execution path. Project-linked workflows include bounded DC/RC calculations, a low-pass candidate synthesis, LED-series-resistor analysis, clock divider/timebase work, and foundational Ohm's law, resistor/capacitor networks, RC time constant, and ideal energy calculations.

Every quantity is entered as `{ "value": number, "unit": "..." }`. Supported input unit conversions are explicit and dimension-checked. Values are normalized to SI before calculation; result inputs retain original and SI values. NaN, infinity, booleans, unsupported unit spellings, wrong dimensions and nonpositive values where prohibited fail closed.

## Native C++ core (foundation milestone)

The C++20 engine is independently built with CMake and contains bounded dense linear DC MNA and fixed-step linear RC backward-Euler transient solving. It accepts typed SI element records through a versioned C ABI; Python validates dimensions and marshals the indexed circuit snapshot, while C++ performs the numerical solve. The new RC low-pass synthesis workflow uses this core; missing native binaries return an explicit service-unavailable error rather than falling back to Python. Build/test commands and current limits are documented in `README.md` and `docs/CIRCUIT_ENGINE_ARCHITECTURE.md`. The existing Python solver implementations are legacy paths until their API/result parity migration is completed.

## LED series resistor

Inputs are explicit user values:

- nominal supply voltage and nominal LED forward voltage; if either operating range is omitted, that quantity is treated as fixed at its nominal value for corner analysis;
- target current;
- optional supply minimum/maximum and LED forward-voltage minimum/maximum;
- resistor tolerance (default 5%), E-series (E6/E12/E24), and optional maximum current;
- optional resistor power rating and a required derating fraction (default 0.5).

The calculation is:

```text
R_ideal = (V_supply,nom - V_F,nom) / I_target
R_candidate = smallest supported E-series value >= R_ideal
R_min = R_candidate × (1 - tolerance)
R_max = R_candidate × (1 + tolerance)
I_min = max(0, (V_supply,min - V_F,max) / R_max)
I_max = max(0, (V_supply,max - V_F,min) / R_min)
P_R,max = max(0, V_supply,max - V_F,min)^2 / R_min
P_rating,required = P_R,max / derating_fraction
```

Input ranges must include their nominal values. If a current limit or power rating is not supplied, that compliance check reports `not-assessed`, never `pass`. A known limit failure is reported as `fail`; the workflow does not silently change the user's target or pick an unrequested substitute. The selected E-series candidate, corner values, substitutions, assumptions, validity limits, warnings and compliance states are included in the result.

## Project dependency/staleness behavior

`POST /api/projects/<id>/calculations/led-resistor` runs this real calculation with explicit inputs. Optional `source_refs` values must identify components in the saved circuit model, but they are labels only: numerical values are not read from part descriptions or inferred from those references. The report stores both a canonical circuit-model SHA-256 and an input SHA-256.

`GET /api/projects/<id>/calculations` compares each report's circuit hash with the current saved model and returns `current` or `stale`. A changed circuit does not relabel an old numerical result as current, and the tool does not silently rerun or modify the design. The user can rerun the calculation against the new revision. `current` means revision-hash match only, not engineering approval.

## Integer clock-divider candidate

`clock_divider_candidate` and `POST /api/projects/<id>/calculations/clock-divider` accept explicit source frequency, target tick rate, and source tolerance. They calculate `N = max(1, nearest_integer(f_source / f_target))`, then report the nominal output rate, quantization error, and source-tolerance drift bounds. The rounding is nearest integer with half-up behavior. This is useful for exploring a divider ratio from supplied values; it does not derive component choices or check a physical counter/MCU implementation. The modulus is restricted to the JavaScript-safe integer range to keep saved reports interoperable.

If the requested output exceeds the source frequency, integer division cannot up-convert it; modulus one is returned as the nearest candidate with a warning. A candidate is never called a pass or a hardware design.

## Linear DC MNA operating point

`solve_dc_operating_point` and `POST /api/projects/<id>/simulations/dc-operating-point` solve an explicitly supplied, bounded linear network containing only ideal resistors, independent DC voltage sources, and independent DC current sources. The implementation assembles the modified nodal conductance matrix, solves it by row-scaled Gaussian elimination with partial pivoting, rejects singular/weak-pivot systems, and reports node voltages, element voltage/current/power, KCL and source-constraint residuals, normalized residual, and power balance.

The JSON netlist has an explicit ground label and two-terminal elements with typed quantities. Current-source direction follows the listed terminal order; voltage-source voltage is terminal zero minus terminal one. The implementation is bounded to 500 elements, 150 non-ground nodes and 180 MNA unknowns. It rejects unsupported and malformed entries rather than guessing device models.

A graph-bound endpoint, `POST /api/projects/<id>/simulations/dc-operating-point/from-model`, uses saved graph connectivity but still requires an explicit mapping for every component: supported R/V/I type, both declared pin numbers in order, and a typed numeric value; otherwise a component must be explicitly excluded with a reason. It does not parse the component's description, symbol ID, or datasheet. The report retains the model hash, mapping hash, generated netlist hash, net-to-solver-node table, mapped/excluded lists, and residuals. “All components accounted for” means only that each was mapped or explicitly excluded—not that the electrical model is complete or physically correct. Exclusions are omitted from the solve and visibly warn that it is partial.

This is a real numerical **linear DC operating-point solve**, not a nonlinear SPICE run or result from ngspice. Capacitors/inductors, dependent sources, semiconductor models, tolerances, temperature, parasitics, and transient behavior are unsupported. The user-supplied netlist is hash-recorded but is not generated from or compared with the saved circuit graph. A model edit is shown separately from netlist integrity; it does not claim that the netlist changed or rerun it.

## Linear RC transient

`POST /api/projects/<id>/simulations/transient-rc/from-model` executes a fixed-step, backward-Euler simulation on a saved graph's connectivity. It requires an explicit per-run R/C/V/I mapping, ordered declared pins, units and values, a source that is either constant or a single ideal step, and an initial voltage for every capacitor. Every saved component must be mapped or explicitly excluded. The report stores circuit, mapping, and generated-netlist hashes and the full time/node/capacitor traces; `GET /api/projects/<id>/simulations` returns a compact trace summary to avoid returning all samples in history responses.

The companion model is `G_C = C/Δt`, `I_eq = -(C/Δt)·V_C,previous`; the source changes at its requested step time and is sampled at the first following timestep. The initial network is solved using the supplied capacitor initial voltages as ideal voltage constraints; incompatible ideal initial/source conditions are rejected.

The implementation is capped at 100 elements, 24 non-ground nodes, 32 effective MNA unknowns, and 2,000 fixed steps. Only ideal linear RC networks with independent DC/one-step sources are supported. Backward Euler damps fast transients; timestep adequacy and convergence are not assessed, so repeat with smaller steps to compare sensitivity. Inductors, nonlinear semiconductors, parasitics, temperature, and component tolerances are unsupported. This is an actual in-process numerical time-domain simulation, but not ngspice-equivalent validation or design approval.

## Clock timebase error budget

`clock_timebase_error` and `POST /api/projects/<id>/calculations/clock-timebase` accept a user-entered nominal post-divider tick rate, target tick rate, and symmetric frequency tolerance in ppm. The report computes the nominal rate error, bounded actual rates, signed rate-error bounds, and accumulated drift bounds per day, 30 days, and 365.25 days. This helps state an explicit timing-accuracy budget for the digital-clock benchmark without assuming a crystal, NE555, MCU timer, divider, or part tolerance. Inputs are not obtained from a datasheet or circuit model; “current” in calculation history still means only that the saved-model hash matches.

The model assumes a constant rate and a symmetric tolerance around the nominal rate. It excludes temperature dependence, aging, startup behavior, missed/extra ticks, calibration, and correction logic. It does not derive a divider ratio, instantiate digital logic, or run a simulation.

## Foundational functions

- `ohms_law`: `I = V/R`, with `P = V×I`.
- `resistor_network`: series sum or reciprocal-conductance equivalent.
- `capacitor_network`: parallel sum or reciprocal-capacitance series equivalent.
- `rc_time_constant`: `τ = R×C`; includes the first-order 1% settling estimate `4.605τ`.
- `capacitor_energy`: `E = ½CV²`.
- `inductor_energy`: `E = ½LI²`.

These assume ideal lumped components. ESR, ESL, leakage, temperature coefficients, layout parasitics, nonlinear I-V behavior and thermal coupling are not modeled by these functions.

## Not implemented yet

This slice is not a general-purpose unit algebra, MNA solver, nonlinear solver, symbolic engine, complete resistor/capacitor/inductor library, Monte Carlo system, or automated circuit-to-calculation dependency graph. It does not calculate the project clock benchmark automatically, convert the graph into a simulation model, generate firmware, or verify a physical circuit. Further functions must follow the same evidence envelope and receive independent reference tests before being surfaced as supported calculations.
