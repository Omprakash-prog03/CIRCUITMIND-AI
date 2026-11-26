# Circuit model v2

The native source of design connectivity is `circuit_model.json`. The current UI exposes JSON editing; it does not yet provide drag/drop schematic rendering.

## Shape

- `schema_version`: exactly `2`. Legacy v1 data migrates on load/save; unsupported future versions are rejected.
- `metadata`: `{ "units": "mm", "grid_mm": positive finite number }`.
- `status`: `requirements-only`, `draft`, or `connectivity-draft`. User-authored data cannot mark itself electrically checked or hardware-tested.
- `components`: unique KiCad-style reference, value, optional caller-supplied `{library,name}` symbol identity, optional footprint/datasheet, `pins` (`number`, `name`, KiCad electrical `type`), `position_mm` (`x`,`y`), and `rotation_deg`.
- `nets`: unique names and endpoint arrays of `{reference,pin}`. A declared pin may belong to no more than one net. Dangling/single-terminal nets and unconnected pins warn; invalid references/pins and duplicate assignments fail validation.
- `inventory`: audit records, separate from instantiated circuit components. A BOM item is never silently converted into a placed component.
- `limitations`: textual limitations and provenance caveats.

## Example graph (not an electrical design recommendation)

```json
{
  "schema_version": 2,
  "metadata": {"units": "mm", "grid_mm": 1.27},
  "status": "draft",
  "components": [
    {"reference":"R1", "value":"10k", "symbol":{"library":"Device","name":"R"}, "footprint":"", "datasheet":"", "pins":[{"number":"1","name":"~","type":"passive"},{"number":"2","name":"~","type":"passive"}], "position_mm":{"x":10,"y":10}, "rotation_deg":0},
    {"reference":"R2", "value":"10k", "symbol":{"library":"Device","name":"R"}, "footprint":"", "datasheet":"", "pins":[{"number":"1","name":"~","type":"passive"},{"number":"2","name":"~","type":"passive"}], "position_mm":{"x":20,"y":10}, "rotation_deg":0}
  ],
  "nets": [
    {"name":"N_A", "terminals":[{"reference":"R1","pin":"1"},{"reference":"R2","pin":"1"}]},
    {"name":"N_B", "terminals":[{"reference":"R1","pin":"2"},{"reference":"R2","pin":"2"}]}
  ],
  "inventory": [],
  "limitations": []
}
```

The IDs/pins above are supplied example data. They are not looked up or verified against KiCad libraries by the application.

## Export and validation boundaries

The adapter writes KiCad legacy XML netlist (`kicad_netlist.net`) from the model's own pins/nets. It refuses export when there are no instantiated components, graph errors, or unresolved symbol IDs. It does not resolve installed library symbols, check symbol pin-table truth, emit `.kicad_sch`, run ERC, or prove that power/electrical constraints are safe. `connectivity_report.json` is graph/schema evidence only. KiCad CLI validation is recorded as not run unless a future integration actually invokes the binary.

## Revision behavior

On each accepted save, the prior model is serialized to `snapshots/<UTC timestamp>-<content hash prefix>.json`; `snapshots/manifest.json` retains up to 100 revisions. Restore snapshots the current state before restoring the requested revision. Snapshots are included in project ZIP exports.
