# Deterministic engineering-math slice

## Current implemented scope

`circuitmind.engineering` is a pure-Python calculation library. It does not invoke ngspice or infer values from a component label. Its first project-linked workflow is a bounded DC LED-series-resistor analysis, accompanied by foundational Ohm's law, resistor/capacitor series-parallel networks, RC time constant, and ideal capacitor/inductor energy calculations.

Every quantity is entered as `{ "value": number, "unit": "..." }`. Supported input unit conversions are explicit and dimension-checked. Values are normalized to SI before calculation; result inputs retain original and SI values. NaN, infinity, booleans, unsupported unit spellings, wrong dimensions and nonpositive values where prohibited fail closed.

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
