const $ = (selector) => document.querySelector(selector);
const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
let currentProject = null;
let projectCache = [];
let toastTimer;

async function request(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try { message = (await response.json()).error || message; } catch (_) {}
    throw new Error(message);
  }
  return response;
}
function toast(message) {
  const node = $('#toast'); node.textContent = message; node.classList.add('show');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => node.classList.remove('show'), 3200);
}
function showView(name) {
  document.querySelectorAll('.view').forEach((view) => view.classList.add('hidden'));
  const target = $(`#view-${name}`); if (target) target.classList.remove('hidden');
  document.querySelectorAll('.nav-item').forEach((button) => button.classList.toggle('active', button.dataset.view === name || (name === 'result' && button.dataset.view === 'new')));
  $('#crumb-current').textContent = name === 'new' ? 'New project' : name === 'result' ? (currentProject?.name || 'Project analysis') : name === 'projects' ? 'Project library' : 'Toolchain health';
  window.scrollTo({top: 0, behavior: 'smooth'});
}
function setProjectCount() { $('#project-count').textContent = projectCache.length; }

async function loadProjects() {
  const data = await (await request('/api/projects')).json();
  projectCache = data.projects || []; setProjectCount(); renderProjectList();
}
function renderProjectList() {
  const container = $('#projects-list');
  if (!projectCache.length) { container.innerHTML = '<div class="empty-state">No projects yet. Start with a new engineering brief.</div>'; return; }
  container.innerHTML = projectCache.map((project) => `<article class="project-row"><div class="project-glyph">⌁</div><div class="project-info"><b>${escapeHtml(project.name)}</b><small>${escapeHtml(project.architecture)} · ${escapeHtml(new Date(project.created_at).toLocaleString())}</small></div><span class="status-badge">${escapeHtml(project.status)}</span><button class="text-button" data-open="${escapeHtml(project.project_id)}">Open</button></article>`).join('');
  container.querySelectorAll('[data-open]').forEach((button) => button.addEventListener('click', () => {
    const project = projectCache.find((item) => item.project_id === button.dataset.open); if (project) renderResult(project);
  }));
}

function renderResult(project) {
  currentProject = project;
  $('#crumb-current').textContent = project.name;
  const findings = project.findings?.length ? project.findings : ['No feasibility findings were recorded.'];
  const checks = project.validation?.checks || {};
  const inventoryRows = (project.inventory || []).map((item) => `<tr><td>${escapeHtml(item.name)}</td><td>${escapeHtml(item.quantity ?? 'unspecified')}</td><td>Unverified</td></tr>`).join('');
  const statusLines = Object.entries(checks).map(([name, status]) => `<div class="validation-line"><span>${escapeHtml(name.replaceAll('_',' '))}</span><span>${escapeHtml(status)}</span></div>`).join('');
  const artifactButtons = (project.artifacts || []).map((name) => name.endsWith('.raw') || name.endsWith('.pdf')
    ? `<a class="artifact-button" href="/api/projects/${encodeURIComponent(project.project_id)}/artifact/${encodeURIComponent(name)}" download>↓ &nbsp;Download ${escapeHtml(name)}</a>`
    : `<button class="artifact-button" data-artifact="${escapeHtml(name)}">↗ &nbsp;${escapeHtml(name)}</button>`).join('');
  $('#view-result').innerHTML = `<div class="result-top"><div class="eyebrow"><span class="eyebrow-line"></span> ENGINEERING INTAKE COMPLETE</div><h1>${escapeHtml(project.name)}</h1><p>${escapeHtml(project.architecture)} architecture · Created ${escapeHtml(new Date(project.created_at).toLocaleString())}</p><div class="result-actions"><a class="primary-button" href="/api/projects/${encodeURIComponent(project.project_id)}/export"><span>Download project ZIP</span><span class="button-arrow">↓</span></a><button class="small-button" id="edit-circuit">Edit circuit model</button><button class="small-button" id="run-led-resistor-calculation">Analyze LED resistor</button><button class="small-button" id="run-clock-timebase-calculation">Analyze clock timebase</button><button class="small-button" id="run-clock-divider-calculation">Find divider candidate</button><button class="small-button" id="run-dc-mna">Solve explicit linear DC MNA</button><button class="small-button" id="run-graph-mna">Map graph to DC MNA</button><button class="small-button" id="run-rc-transient">Run graph RC transient</button><button class="small-button" id="synthesize-rc-lowpass">Synthesize RC low-pass</button><button class="small-button" id="import-schematic">Import KiCad schematic</button><button class="small-button" id="run-kicad-erc">Run KiCad ERC</button><button class="small-button" id="import-board">Import KiCad PCB</button><button class="small-button" id="run-kicad-drc">Run KiCad DRC</button><button class="small-button" id="import-datasheet">Import datasheet PDF</button><button class="small-button" id="extract-datasheet-text">Extract latest PDF text</button><button class="small-button" id="import-spice">Import SPICE deck</button><button class="small-button" id="run-ngspice">Run ngspice</button><button class="small-button" id="show-snapshots">Snapshots</button><button class="small-button" id="back-to-brief">← New analysis</button></div></div>
  <div class="status-banner">⚠ &nbsp; Incomplete — this is requirements and feasibility analysis, not a completed electrical design.</div>
  <div class="result-grid"><div><section class="result-panel"><h2>Feasibility findings <span class="optional">${findings.length} ITEMS</span></h2>${findings.map((finding) => `<div class="finding"><span class="finding-icon">!</span><span>${escapeHtml(finding)}</span></div>`).join('')}</section>
  <section class="result-panel"><h2>Supplied component inventory</h2>${inventoryRows ? `<table class="inventory-table"><thead><tr><th>PART / DESCRIPTION</th><th>QTY</th><th>VERIFICATION</th></tr></thead><tbody>${inventoryRows}</tbody></table>` : '<div class="empty-state">No inventory provided.</div>'}</section></div>
  <div><section class="result-panel"><h2>Validation ledger</h2>${statusLines}</section><section class="result-panel"><h2>Engineering calculations</h2><p class="calc-explanation">“Current” means the report’s circuit-model hash matches this revision; it is not design approval.</p><div id="calculation-status"><div class="empty-state">Checking calculation revision status…</div></div></section><section class="result-panel"><h2>Linear DC MNA operating points</h2><p class="calc-explanation">Solves an explicit ideal R/V/I netlist. It is not derived from the saved graph or a SPICE/transient run.</p><div id="simulation-status"><div class="empty-state">Checking saved operating-point reports…</div></div></section><section class="result-panel"><h2>Generated artifacts <span class="optional">${(project.artifacts||[]).length}</span></h2><div class="artifact-list">${artifactButtons}</div></section></div></div>`;
  $('#view-result').querySelectorAll('[data-artifact]').forEach((button) => button.addEventListener('click', () => openArtifact(project.project_id, button.dataset.artifact)));
  $('#back-to-brief').addEventListener('click', () => showView('new'));
  $('#edit-circuit').addEventListener('click', openCircuitEditor);
  $('#run-led-resistor-calculation').addEventListener('click', openLedResistorCalculation);
  $('#run-clock-timebase-calculation').addEventListener('click', openClockTimebaseCalculation);
  $('#run-clock-divider-calculation').addEventListener('click', openClockDividerCalculation);
  $('#run-dc-mna').addEventListener('click', openMnaDialog);
  $('#run-graph-mna').addEventListener('click', openGraphMnaDialog);
  $('#run-rc-transient').addEventListener('click', openGraphTransientDialog);
  $('#synthesize-rc-lowpass').addEventListener('click', openRcLowpassSynthesisDialog);
  loadCalculationStatus(project.project_id);
  loadSimulationStatus(project.project_id);
  $('#import-schematic').addEventListener('click', () => $('#schematic-file').click());
  $('#run-kicad-erc').addEventListener('click', runKiCadErc);
  $('#import-board').addEventListener('click', () => $('#board-file').click());
  $('#run-kicad-drc').addEventListener('click', runKiCadDrc);
  $('#import-datasheet').addEventListener('click', () => $('#datasheet-file').click());
  $('#extract-datasheet-text').addEventListener('click', extractLatestDatasheetText);
  $('#import-spice').addEventListener('click', () => $('#spice-file').click());
  $('#run-ngspice').addEventListener('click', runNgspice);
  $('#show-snapshots').addEventListener('click', showSnapshots);
  showView('result');
}

async function openArtifact(projectId, filename) {
  try {
    const artifact = await (await request(`/api/projects/${encodeURIComponent(projectId)}/artifact/${encodeURIComponent(filename)}`)).json();
    $('#artifact-title').textContent = artifact.name; $('#artifact-content').textContent = artifact.content;
    $('#artifact-dialog').showModal();
  } catch (error) { toast(error.message); }
}
function circuitFeedback(message, kind = '') {
  const node = $('#circuit-feedback'); node.textContent = message; node.className = `dialog-feedback ${kind}`;
}
async function loadCalculationStatus(projectId) {
  const node = $('#calculation-status');
  if (!node) return;
  try {
    const data = await (await request(`/api/projects/${encodeURIComponent(projectId)}/calculations`)).json();
    if (!data.calculations?.length) { node.innerHTML = '<div class="empty-state">No project-bound calculations have been run.</div>'; return; }
    node.innerHTML = [...data.calculations].reverse().map((item) => `<div class="validation-line"><span>${escapeHtml(item.kind.replaceAll('_',' '))}</span><span class="calc-state ${item.is_stale ? 'stale' : 'current'}">${item.status} · ${escapeHtml(new Date(item.created_at).toLocaleString())}</span></div>`).join('');
  } catch (error) { node.textContent = `Calculation status unavailable: ${error.message}`; }
}
function openLedResistorCalculation() {
  $('#led-calc-feedback').textContent = 'Calculations use the entered quantities and current saved-model revision; optional references identify parts but do not supply values.';
  $('#led-calc-feedback').className = 'dialog-feedback';
  $('#led-calc-dialog').showModal();
}
function optionalNumber(id) {
  const raw = $(`#${id}`).value.trim();
  if (!raw) return null;
  const value = Number(raw);
  if (!Number.isFinite(value)) throw new Error(`${id} must be a finite number`);
  return value;
}
function rangeInput(minId, maxId, unit, label) {
  const minimum = optionalNumber(minId), maximum = optionalNumber(maxId);
  if (minimum === null && maximum === null) return null;
  if (minimum === null || maximum === null) throw new Error(`Enter both ${label} minimum and maximum, or leave both blank`);
  return {min:{value:minimum, unit}, max:{value:maximum, unit}};
}
async function loadSimulationStatus(projectId) {
  const node = $('#simulation-status');
  if (!node) return;
  try {
    const data = await (await request(`/api/projects/${encodeURIComponent(projectId)}/simulations`)).json();
    if (!data.simulations?.length) { node.innerHTML = '<div class="empty-state">No linear DC MNA or RC transient runs have been saved.</div>'; return; }
    node.innerHTML = [...data.simulations].reverse().map((run) => `<div class="validation-line"><span>${escapeHtml(run.kind.replaceAll('_',' '))}</span><span class="calc-state ${run.model_status === 'same-at-run' && run.netlist_status === 'intact' ? 'current' : 'stale'}">${escapeHtml(run.model_status)} · netlist ${escapeHtml(run.netlist_status)} · map ${escapeHtml(run.mapping_status)} · ${escapeHtml(new Date(run.created_at).toLocaleString())}</span></div>`).join('');
  } catch (error) { node.textContent = `Simulation history unavailable: ${error.message}`; }
}
function openMnaDialog() {
  $('#mna-feedback').textContent = 'Enter a complete independent-source/resistor netlist. No topology is imported from the saved graph; save a model-aligned netlist only after checking it yourself.';
  $('#mna-feedback').className = 'dialog-feedback';
  $('#mna-dialog').showModal();
}
async function runMna(event) {
  event.preventDefault();
  const feedback = $('#mna-feedback');
  try {
    const netlist = JSON.parse($('#mna-netlist').value);
    const response = await fetch(`/api/projects/${encodeURIComponent(currentProject.project_id)}/simulations/dc-operating-point`, {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({netlist})
    });
    const report = await response.json();
    if (!response.ok) throw new Error(report.error || `MNA solve failed (${response.status})`);
    const out = report.calculation.outputs;
    const nodeRows = Object.entries(out.node_voltages).map(([node, voltage]) => `${node}: ${Number(voltage.value).toPrecision(7)} V`).join('\n');
    const elementRows = out.elements.map((element) => `${element.reference} (${element.type}): V=${Number(element.voltage.value).toPrecision(6)} V, I=${Number(element.current.value).toPrecision(6)} A, P_abs=${Number(element.power_absorbed.value).toPrecision(6)} W`).join('\n');
    feedback.textContent = `Solver state: ${out.solver_status}\nNode voltages:\n${nodeRows}\nElement results (positive power is absorbed):\n${elementRows}\nPower balance: ${Number(out.total_power_balance.value).toExponential(4)} W · normalized equation residual: ${Number(out.normalized_equation_residual).toExponential(3)}\n${report.calculation.warnings.join(' ')}\nSaved as ${report.artifact}; netlist SHA-256 ${report.netlist_sha256.slice(0,16)}…`;
    feedback.className = 'dialog-feedback ok';
    const projectId = currentProject.project_id;
    await loadProjects();
    currentProject = projectCache.find((project) => project.project_id === projectId) || currentProject;
    await loadSimulationStatus(projectId);
    const artifactList = $('#view-result .artifact-list');
    if (artifactList && !artifactList.querySelector(`[data-artifact="${report.artifact}"]`)) {
      const button = document.createElement('button'); button.className = 'artifact-button'; button.textContent = `↗  ${report.artifact}`;
      button.addEventListener('click', () => openArtifact(projectId, report.artifact)); artifactList.append(button);
    }
  } catch (error) {
    feedback.textContent = error.message; feedback.className = 'dialog-feedback error';
  }
}
function openGraphMnaDialog() {
  $('#graph-mna-feedback').textContent = 'Specify every saved component exactly once: map it to an explicit R/V/I type, ordered pair of declared pins, and typed numeric value, or exclude it with a reason. No component descriptions are parsed.';
  $('#graph-mna-feedback').className = 'dialog-feedback';
  $('#graph-mna-dialog').showModal();
}
async function runGraphMna(event) {
  event.preventDefault();
  const feedback = $('#graph-mna-feedback');
  try {
    const mapping = JSON.parse($('#graph-mna-mapping').value);
    const projectId = currentProject.project_id;
    const response = await fetch(`/api/projects/${encodeURIComponent(projectId)}/simulations/dc-operating-point/from-model`, {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({mapping})
    });
    const report = await response.json();
    if (!response.ok) throw new Error(report.error || `Graph mapping/MNA failed (${response.status})`);
    const out = report.calculation.outputs;
    const nodeRows = Object.entries(report.mapping_report.net_name_to_solver_node).map(([name, node]) => `${name} (${node}): ${Number(out.node_voltages[node].value).toPrecision(7)} V`).join('\n');
    const componentRows = report.component_coverage;
    feedback.textContent = `Solver state: ${out.solver_status}\nNodes derived from saved graph:\n${nodeRows}\nComponents accounted: ${componentRows.mapped} mapped, ${componentRows.excluded} explicitly excluded of ${componentRows.total}.\nExclusions: ${report.mapping_report.excluded_components.map((item) => `${item.reference}: ${item.reason}`).join('; ') || 'none'}\nPower balance: ${Number(out.total_power_balance.value).toExponential(4)} W · normalized equation residual: ${Number(out.normalized_equation_residual).toExponential(3)}\n${report.calculation.warnings.join(' ')}\nMapping SHA-256 ${report.mapping_sha256.slice(0,16)}…; saved ${report.artifact}.`;
    feedback.className = componentRows.has_exclusions ? 'dialog-feedback' : 'dialog-feedback ok';
    await loadProjects();
    currentProject = projectCache.find((project) => project.project_id === projectId) || currentProject;
    await loadSimulationStatus(projectId);
    const artifactList = $('#view-result .artifact-list');
    if (artifactList && !artifactList.querySelector(`[data-artifact="${report.artifact}"]`)) {
      const button = document.createElement('button'); button.className = 'artifact-button'; button.textContent = `↗  ${report.artifact}`;
      button.addEventListener('click', () => openArtifact(projectId, report.artifact)); artifactList.append(button);
    }
  } catch (error) {
    feedback.textContent = error.message; feedback.className = 'dialog-feedback error';
  }
}
function openGraphTransientDialog() {
  $('#rc-transient-feedback').textContent = 'Enter a complete mapping plus a fixed timestep and stop time. R/C/source values, waveforms, capacitor initial voltages, and pin order must be explicit; the app will not infer them.';
  $('#rc-transient-feedback').className = 'dialog-feedback';
  $('#rc-transient-dialog').showModal();
}
async function runGraphTransient(event) {
  event.preventDefault();
  const feedback = $('#rc-transient-feedback');
  try {
    const mapping = JSON.parse($('#rc-transient-mapping').value);
    const projectId = currentProject.project_id;
    const payload = {mapping,
      time_step:{value:optionalNumber('rc-transient-step'), unit:'s'},
      stop_time:{value:optionalNumber('rc-transient-stop'), unit:'s'}};
    const response = await fetch(`/api/projects/${encodeURIComponent(projectId)}/simulations/transient-rc/from-model`, {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)
    });
    const report = await response.json();
    if (!response.ok) throw new Error(report.error || `RC transient solve failed (${response.status})`);
    const out = report.calculation.outputs;
    const netMap = report.mapping_report.net_name_to_solver_node;
    const finalNodes = Object.entries(netMap).map(([name,node]) => `${name}: ${Number(out.node_voltage_traces_v[node].at(-1)).toPrecision(7)} V`).join('\n');
    const finalCaps = Object.entries(out.capacitor_voltage_traces_v).map(([ref,trace]) => `${ref}: ${Number(trace.at(-1)).toPrecision(7)} V`).join('\n');
    const coverage = report.component_coverage;
    feedback.textContent = `Backward Euler linear-RC time-domain run · ${out.sample_count} samples through ${Number(out.stop_time.value).toPrecision(6)} s.\nFinal node voltages:\n${finalNodes}\nFinal capacitor voltages:\n${finalCaps}\nComponents mapped: ${coverage.mapped}; explicitly excluded: ${coverage.excluded} of ${coverage.total}.\n${report.calculation.warnings.join(' ')}\nModel SHA-256 ${report.circuit_model_sha256.slice(0,16)}…; mapping SHA-256 ${report.mapping_sha256.slice(0,16)}…; full traces saved in ${report.artifact}.`;
    feedback.className = coverage.has_exclusions ? 'dialog-feedback' : 'dialog-feedback ok';
    await loadProjects();
    currentProject = projectCache.find((project) => project.project_id === projectId) || currentProject;
    await loadSimulationStatus(projectId);
    const artifactList = $('#view-result .artifact-list');
    if (artifactList && !artifactList.querySelector(`[data-artifact="${report.artifact}"]`)) {
      const button = document.createElement('button'); button.className = 'artifact-button'; button.dataset.artifact = report.artifact; button.textContent = `↗  ${report.artifact}`;
      button.addEventListener('click', () => openArtifact(projectId, report.artifact)); artifactList.append(button);
    }
  } catch (error) {
    feedback.textContent = error.message; feedback.className = 'dialog-feedback error';
  }
}
function openRcLowpassSynthesisDialog() {
  $('#rc-synthesis-feedback').textContent = 'Supported deterministic workflow: one ideal-source, unloaded, single-pole RC low-pass. Inputs below are explicit constraints; no free-text requirements, inventory, catalogue, manufacturer, footprint, or datasheet is inferred.';
  $('#rc-synthesis-feedback').className = 'dialog-feedback';
  $('#rc-synthesis-dialog').showModal();
}
async function runRcLowpassSynthesis(event) {
  event.preventDefault();
  const feedback = $('#rc-synthesis-feedback');
  try {
    const inputs = {
      cutoff_frequency:{value:optionalNumber('rc-synthesis-cutoff'), unit:'Hz'},
      preferred_resistance:{value:optionalNumber('rc-synthesis-resistance'), unit:'ohm'},
      series:$('#rc-synthesis-series').value,
      resistor_tolerance_percent:optionalNumber('rc-synthesis-r-tolerance'),
      capacitor_tolerance_percent:optionalNumber('rc-synthesis-c-tolerance'),
      max_cutoff_error_percent:optionalNumber('rc-synthesis-max-error')
    };
    const projectId = currentProject.project_id;
    const apply = $('#rc-synthesis-apply').checked;
    if (apply && currentProject?.artifacts?.includes('circuit_model.json')) {
      if (!window.confirm('Replace the saved circuit model with this generated RC candidate? The previous revision will be snapshotted.')) return;
    }
    const response = await fetch(`/api/projects/${encodeURIComponent(projectId)}/designs/rc-low-pass`, {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({request_version:1, inputs, apply_to_circuit:apply})
    });
    const report = await response.json();
    if (!response.ok) throw new Error(report.error || `RC synthesis failed (${response.status})`);
    const design = report.design;
    const values = design.selected_values;
    const transient = design.transient_test;
    feedback.textContent = `Candidate status: ${design.design_status}\nTarget cutoff: ${Number(design.requirements.target_cutoff_frequency.value).toPrecision(6)} Hz · selected nominal: ${Number(values.nominal_cutoff_frequency.value).toPrecision(7)} Hz (${Number(values.nominal_cutoff_error_percent).toFixed(4)}%)\nSelected values: R1 ${Number(values.resistance.value).toPrecision(6)} ohm (${design.requirements.series}); C1 ${Number(values.capacitance.value).toPrecision(6)} F\nTolerance cutoff bounds: ${Number(values.cutoff_tolerance_bounds.minimum.value).toPrecision(6)} to ${Number(values.cutoff_tolerance_bounds.maximum.value).toPrecision(6)} Hz\nBackward-Euler unit-step check: ${Number(transient.simulated_final_output_voltage.value).toPrecision(7)} V at ${Number(transient.stop_time.value).toPrecision(6)} s; analytic reference ${Number(transient.analytic_final_output_voltage.value).toPrecision(7)} V.\n${design.findings.join(' ')}\nGraph SHA-256 ${report.candidate_circuit_model_sha256.slice(0,16)}…; artifact ${report.artifact}.${report.applied_to_circuit_model ? ' Candidate applied to the saved circuit model; previous revision snapshotted.' : report.apply_refused_reason ? ` Not applied: ${report.apply_refused_reason}` : ' Saved graph was not changed; candidate is in the report artifact.'}`;
    feedback.className = design.constraints.nominal_cutoff_error === 'pass' ? 'dialog-feedback ok' : 'dialog-feedback';
    await refreshProject(projectId);
  } catch (error) {
    feedback.textContent = error.message; feedback.className = 'dialog-feedback error';
  }
}
function openClockDividerCalculation() {
  $('#clock-divider-feedback').textContent = 'Enter the supplied source and desired output rates plus a sourced tolerance. The integer result is a math candidate; device limits and logic behavior are not checked.';
  $('#clock-divider-feedback').className = 'dialog-feedback';
  $('#clock-divider-dialog').showModal();
}
async function calculateClockDivider(event) {
  event.preventDefault();
  const feedback = $('#clock-divider-feedback');
  try {
    const inputs = {
      source_frequency:{value:optionalNumber('calc-divider-source'), unit:'Hz'},
      target_frequency:{value:optionalNumber('calc-divider-target'), unit:'Hz'},
      source_tolerance_ppm:optionalNumber('calc-divider-tolerance'),
    };
    const response = await fetch(`/api/projects/${encodeURIComponent(currentProject.project_id)}/calculations/clock-divider`, {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({inputs})
    });
    const report = await response.json();
    if (!response.ok) throw new Error(report.error || `Calculation failed (${response.status})`);
    const out = report.calculation.outputs;
    const fmt = (quantity) => `${Number(quantity.value).toPrecision(6)} ${quantity.unit}`;
    feedback.textContent = `${report.calculation.substitution}\nCandidate modulus N: ${out.candidate_divider_modulus} (nearest integer only)\nNominal output rate: ${fmt(out.nominal_output_frequency)} · Nominal error: ${fmt(out.nominal_rate_error)}\nRate error range: ${fmt(out.minimum_rate_error)} to ${fmt(out.maximum_rate_error)}\nDrift/day: ${fmt(out.minimum_clock_drift_per_day)} to ${fmt(out.maximum_clock_drift_per_day)}\nBound to saved circuit SHA-256 ${report.circuit_model_sha256.slice(0,16)}…; report ${report.artifact}.\nImplementation not assessed; see report limitations.`;
    feedback.className = `dialog-feedback ${report.calculation.warnings.length ? 'error' : 'ok'}`;
    const projectId = currentProject.project_id;
    await loadProjects();
    currentProject = projectCache.find((project) => project.project_id === projectId) || currentProject;
    await loadCalculationStatus(projectId);
    const artifactList = $('#view-result .artifact-list');
    if (artifactList && !artifactList.querySelector(`[data-artifact="${report.artifact}"]`)) {
      const button = document.createElement('button'); button.className = 'artifact-button'; button.textContent = `↗  ${report.artifact}`;
      button.addEventListener('click', () => openArtifact(projectId, report.artifact)); artifactList.append(button);
    }
  } catch (error) {
    feedback.textContent = error.message; feedback.className = 'dialog-feedback error';
  }
}
function openClockTimebaseCalculation() {
  $('#clock-timebase-feedback').textContent = 'Enter a user-specified post-divider tick rate and symmetric tolerance. This does not infer an oscillator or divider.';
  $('#clock-timebase-feedback').className = 'dialog-feedback';
  $('#clock-timebase-dialog').showModal();
}
async function calculateClockTimebase(event) {
  event.preventDefault();
  const feedback = $('#clock-timebase-feedback');
  try {
    const inputs = {
      nominal_tick_rate:{value:optionalNumber('calc-clock-nominal'), unit:'Hz'},
      target_tick_rate:{value:optionalNumber('calc-clock-target'), unit:'Hz'},
      tolerance_ppm:optionalNumber('calc-clock-tolerance'),
    };
    const response = await fetch(`/api/projects/${encodeURIComponent(currentProject.project_id)}/calculations/clock-timebase`, {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({inputs})
    });
    const report = await response.json();
    if (!response.ok) throw new Error(report.error || `Calculation failed (${response.status})`);
    const out = report.calculation.outputs;
    const fmt = (quantity) => `${Number(quantity.value).toPrecision(6)} ${quantity.unit}`;
    feedback.textContent = `${report.calculation.substitution}\nNominal rate error: ${fmt(out.nominal_rate_error)}\nRate-error bound: ${fmt(out.minimum_rate_error)} to ${fmt(out.maximum_rate_error)}\nClock drift/day: ${fmt(out.minimum_clock_drift_per_day)} to ${fmt(out.maximum_clock_drift_per_day)}\nClock drift/30 days: ${fmt(out.minimum_clock_drift_per_30_days)} to ${fmt(out.maximum_clock_drift_per_30_days)}\nClock drift/365.25 days: ${fmt(out.minimum_clock_drift_per_365_25_days)} to ${fmt(out.maximum_clock_drift_per_365_25_days)}\nBound to saved circuit SHA-256 ${report.circuit_model_sha256.slice(0,16)}…; report ${report.artifact}.\n${report.calculation.warnings.join(' ')}`;
    feedback.className = 'dialog-feedback ok';
    const projectId = currentProject.project_id;
    await loadProjects();
    currentProject = projectCache.find((project) => project.project_id === projectId) || currentProject;
    await loadCalculationStatus(projectId);
    const artifactList = $('#view-result .artifact-list');
    if (artifactList && !artifactList.querySelector(`[data-artifact="${report.artifact}"]`)) {
      const button = document.createElement('button'); button.className = 'artifact-button'; button.textContent = `↗  ${report.artifact}`;
      button.addEventListener('click', () => openArtifact(projectId, report.artifact)); artifactList.append(button);
    }
  } catch (error) {
    feedback.textContent = error.message; feedback.className = 'dialog-feedback error';
  }
}
async function calculateLedResistor(event) {
  event.preventDefault();
  const feedback = $('#led-calc-feedback');
  try {
    const inputs = {
      supply:{value:optionalNumber('calc-supply'), unit:'V'},
      led_forward:{value:optionalNumber('calc-vf'), unit:'V'},
      target_current:{value:optionalNumber('calc-current'), unit:'mA'},
      resistor_tolerance_percent:optionalNumber('calc-tolerance'),
      e_series:Number($('#calc-series').value),
      power_derating_fraction:optionalNumber('calc-derating'),
    };
    const supplyRange = rangeInput('calc-supply-min','calc-supply-max','V','supply voltage');
    const vfRange = rangeInput('calc-vf-min','calc-vf-max','V','LED forward voltage');
    if (supplyRange) inputs.supply_range = supplyRange;
    if (vfRange) inputs.led_forward_range = vfRange;
    const maximumCurrent = optionalNumber('calc-max-current');
    if (maximumCurrent !== null) inputs.maximum_current = {value:maximumCurrent, unit:'mA'};
    const powerRating = optionalNumber('calc-power-rating');
    if (powerRating !== null) inputs.resistor_power_rating = {value:powerRating, unit:'W'};
    const source_refs = {};
    for (const [role, id] of [['supply','calc-supply-ref'],['led','calc-led-ref'],['resistor','calc-resistor-ref']]) {
      const reference = $(`#${id}`).value.trim(); if (reference) source_refs[role] = reference;
    }
    const response = await fetch(`/api/projects/${encodeURIComponent(currentProject.project_id)}/calculations/led-resistor`, {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({inputs, source_refs})
    });
    const report = await response.json();
    if (!response.ok) throw new Error(report.error || `Calculation failed (${response.status})`);
    const out = report.calculation.outputs;
    const formatQuantity = (value) => value ? `${Number(value.value).toPrecision(5)} ${value.unit}` : 'not supplied';
    feedback.textContent = `${report.calculation.name}\n${report.calculation.formula}\n${report.calculation.substitution}\nE-series candidate: ${formatQuantity(out.selected_resistance)} · Design status: ${out.design_status} · Nominal current: ${formatQuantity(out.nominal_current)}\nWorst-case current: ${formatQuantity(out.worst_case_minimum_current)} to ${formatQuantity(out.worst_case_maximum_current)} · Max resistor power: ${formatQuantity(out.resistor_maximum_power)}\nCurrent limit: ${out.compliance.maximum_current} · Power rating: ${out.compliance.resistor_power}\n${report.calculation.assumptions.join(' ')}\nBound to saved circuit SHA-256 ${report.circuit_model_sha256.slice(0,16)}…; report ${report.artifact}.`;
    feedback.className = `dialog-feedback ${Object.values(out.compliance).includes('fail') ? 'error' : 'ok'}`;
    await loadProjects();
    currentProject = projectCache.find((project) => project.project_id === currentProject.project_id) || currentProject;
    await loadCalculationStatus(currentProject.project_id);
    const artifactList = $('#view-result .artifact-list');
    if (artifactList && !artifactList.querySelector(`[data-artifact="${report.artifact}"]`)) {
      const button = document.createElement('button'); button.className = 'artifact-button'; button.textContent = `↗  ${report.artifact}`;
      button.addEventListener('click', () => openArtifact(currentProject.project_id, report.artifact)); artifactList.append(button);
    }
  } catch (error) {
    feedback.textContent = error.message; feedback.className = 'dialog-feedback error';
  }
}

async function openCircuitEditor() {
  if (!currentProject) return;
  try {
    const data = await (await request(`/api/projects/${encodeURIComponent(currentProject.project_id)}/circuit`)).json();
    $('#circuit-json').value = JSON.stringify(data.model, null, 2);
    circuitFeedback('Graph checks do not establish electrical correctness.');
    $('#circuit-dialog').showModal();
  } catch (error) { toast(error.message); }
}
async function auditCircuitSymbols() {
  if (!currentProject) return;
  try {
    const model = JSON.parse($('#circuit-json').value);
    const response = await fetch(`/api/projects/${encodeURIComponent(currentProject.project_id)}/circuit/symbols/resolve`, {
      method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({model})
    });
    const report = await response.json();
    if (!response.ok) { circuitFeedback(report.error || `Symbol audit failed (${response.status})`, 'error'); return; }
    const mismatches = report.components.filter((item) => item.status === 'pin-manifest-differs').map((item) => item.reference);
    const unresolved = report.components.filter((item) => !['pin-manifest-match', 'pin-manifest-differs'].includes(item.status)).map((item) => item.reference);
    const detail = `Read-only symbol audit: ${report.resolved_count} IDs resolved, ${report.mismatch_count} declared pin manifest(s) differ, ${report.unresolved_count} unresolved. ${mismatches.length ? `Differences: ${mismatches.join(', ')}. ` : ''}${unresolved.length ? `Unresolved: ${unresolved.join(', ')}. ` : ''}Saved ${report.artifact}. No model changes or electrical validation.`;
    circuitFeedback(detail, report.mismatch_count || report.unresolved_count ? 'error' : 'ok');
    await loadProjects();
    currentProject = projectCache.find((project) => project.project_id === currentProject.project_id) || currentProject;
    const artifactList = $('#view-result .artifact-list');
    if (artifactList && !artifactList.querySelector(`[data-artifact="${report.artifact}"]`)) {
      const artifactButton = document.createElement('button');
      artifactButton.className = 'artifact-button'; artifactButton.textContent = `↗  ${report.artifact}`;
      artifactButton.addEventListener('click', () => openArtifact(currentProject.project_id, report.artifact));
      artifactList.append(artifactButton);
    }
  } catch (error) {
    circuitFeedback(`Symbol audit could not run: ${error.message}`, 'error');
  }
}

async function validateCircuitDraft() {
  try {
    const model = JSON.parse($('#circuit-json').value);
    const response = await fetch(`/api/projects/${encodeURIComponent(currentProject.project_id)}/circuit/validate`, {
      method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({model})
    });
    const body = await response.json();
    if (!response.ok) {
      const errors = body.topology_report?.errors || [body.error || 'Model validation failed'];
      circuitFeedback(errors.join(' · '), 'error'); return false;
    }
    const warnings = body.topology_report.warnings || [];
    circuitFeedback(`Topology valid: ${body.topology_report.component_count} components, ${body.topology_report.net_count} nets. ${body.kicad_export}${warnings.length ? ` · Warnings: ${warnings.join('; ')}` : ''}`, 'ok');
    return true;
  } catch (error) { circuitFeedback(`Invalid JSON: ${error.message}`, 'error'); return false; }
}
async function refreshProject(projectId) {
  await loadProjects();
  const updated = projectCache.find((project) => project.project_id === projectId);
  if (updated) renderResult(updated);
}
async function showSnapshots() {
  if (!currentProject) return;
  try {
    const data = await (await request(`/api/projects/${encodeURIComponent(currentProject.project_id)}/snapshots`)).json();
    const list = $('#snapshots-list');
    if (!data.snapshots.length) list.innerHTML = '<div class="empty-state">No revisions yet. Saving a circuit-model change creates the first snapshot of the previous state.</div>';
    else list.innerHTML = [...data.snapshots].reverse().map((snapshot) => `<div class="snapshot-row"><div class="snapshot-info"><b>${escapeHtml(snapshot.snapshot_id)}</b><small>${escapeHtml(snapshot.created_at)} · SHA-256 ${escapeHtml(snapshot.sha256.slice(0,16))}…</small></div><button class="small-button" data-restore="${escapeHtml(snapshot.snapshot_id)}">Restore</button></div>`).join('');
    list.querySelectorAll('[data-restore]').forEach((button) => button.addEventListener('click', async () => {
      if (!window.confirm('Restore this model revision? The current state will be snapshotted first.')) return;
      try {
        const response = await request(`/api/projects/${encodeURIComponent(currentProject.project_id)}/snapshots/restore`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({snapshot_id:button.dataset.restore})});
        const restored = await response.json(); $('#snapshots-dialog').close();
        await refreshProject(currentProject.project_id); toast(`Restored revision. ${restored.topology_report.component_count} components in model.`);
      } catch (error) { toast(error.message); }
    }));
    $('#snapshots-dialog').showModal();
  } catch (error) { toast(error.message); }
}

async function uploadSource(url, content, label) {
  let response = await fetch(url, {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({content})});
  let result = await response.json();
  if (response.status === 409 && window.confirm(`${label} already exists. Archive the current version and replace it?`)) {
    response = await fetch(url, {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({content, replace:true})});
    result = await response.json();
  }
  if (!response.ok) throw new Error(result.deck_validation?.errors?.join(' · ') || result.error || `Upload failed (${response.status})`);
  return result;
}
async function importSchematicFile(file) {
  if (!file) return;
  if (!file.name.toLowerCase().endsWith('.kicad_sch')) { toast('Choose a .kicad_sch file.'); return; }
  if (file.size > 12_000_000) { toast('Schematic exceeds the 12 MB limit.'); return; }
  try {
    const content = await file.text();
    await uploadSource(`/api/projects/${encodeURIComponent(currentProject.project_id)}/schematic`, content, 'A schematic');
    await refreshProject(currentProject.project_id);
    toast('Schematic attached. It has not been parsed or checked yet.');
  } catch (error) { toast(error.message); }
}
async function importSpiceFile(file) {
  if (!file) return;
  if (!/\.(cir|sp|ckt)$/i.test(file.name)) { toast('Choose a .cir, .sp, or .ckt netlist.'); return; }
  if (file.size > 12_000_000) { toast('SPICE deck exceeds the 12 MB limit.'); return; }
  try {
    const content = await file.text();
    await uploadSource(`/api/projects/${encodeURIComponent(currentProject.project_id)}/simulation-deck`, content, 'A SPICE deck');
    await refreshProject(currentProject.project_id);
    toast('SPICE deck attached and passed the static directive allowlist; simulation has not run.');
  } catch (error) { toast(error.message); }
}
async function importBoardFile(file) {
  if (!file) return;
  if (!file.name.toLowerCase().endsWith('.kicad_pcb')) { toast('Choose a .kicad_pcb file.'); return; }
  if (file.size > 12_000_000) { toast('PCB file exceeds the 12 MB limit.'); return; }
  try {
    const content = await file.text();
    await uploadSource(`/api/projects/${encodeURIComponent(currentProject.project_id)}/board`, content, 'A PCB board');
    await refreshProject(currentProject.project_id);
    toast('PCB board attached after a header check only. It has not been parsed, DRC-checked, or manufacturing validated.');
  } catch (error) { toast(error.message); }
}
async function importDatasheetFile(file) {
  if (!file) return;
  if (!file.name.toLowerCase().endsWith('.pdf')) { toast('Choose a PDF datasheet.'); return; }
  if (file.size > 20_000_000) { toast('Datasheet PDF exceeds the 20 MB limit.'); return; }
  const reference = window.prompt('Link this source to a saved circuit component reference (leave blank for unassigned):', '');
  if (reference === null) return;
  try {
    const query = new URLSearchParams({filename: file.name});
    if (reference.trim()) query.set('reference', reference.trim());
    const response = await fetch(`/api/projects/${encodeURIComponent(currentProject.project_id)}/datasheets?${query}`, {
      method: 'PUT', headers: {'Content-Type':'application/pdf'}, body: await file.arrayBuffer()
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `Datasheet upload failed (${response.status})`);
    await refreshProject(currentProject.project_id);
    toast(`Datasheet source attached (${result.sha256.slice(0,12)}…). PDF header only; no specifications extracted or verified.`);
  } catch (error) { toast(error.message); }
}
async function extractLatestDatasheetText() {
  if (!currentProject) return;
  try {
    const sources = (await (await request(`/api/projects/${encodeURIComponent(currentProject.project_id)}/datasheets`)).json()).sources || [];
    const source = sources.at(-1);
    if (!source) { toast('Attach a datasheet PDF first.'); return; }
    const response = await fetch(`/api/projects/${encodeURIComponent(currentProject.project_id)}/datasheets/${source.sha256}/extract`, {method:'POST'});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `Text extraction failed (${response.status})`);
    await refreshProject(currentProject.project_id);
    const detail = result.status === 'completed'
      ? `Raw text extracted from ${result.page_count} page(s) into ${result.text_file}.`
      : `PDF text extraction status: ${result.status}.`;
    toast(`${detail} No component specifications or design claims were inferred.`);
  } catch (error) { toast(error.message); }
}
async function runNgspice() {
  if (!currentProject) return;
  toast('Running local ngspice batch simulation…');
  try {
    const response = await fetch(`/api/projects/${encodeURIComponent(currentProject.project_id)}/integrations/ngspice`, {method:'POST'});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `Simulation request failed (${response.status})`);
    await refreshProject(currentProject.project_id);
    toast(`ngspice status: ${result.status}. See ngspice_result.json and tool run log.`);
  } catch (error) { toast(error.message); }
}
async function runKiCadErc() {
  if (!currentProject) return;
  try {
    const response = await fetch(`/api/projects/${encodeURIComponent(currentProject.project_id)}/integrations/kicad-erc`, {method:'POST'});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `ERC request failed (${response.status})`);
    await refreshProject(currentProject.project_id);
    const detail = result.status === 'completed-no-messages' ? 'KiCad report summary: 0 messages.' : `KiCad ERC status: ${result.status}.`;
    toast(`${detail} See kicad_erc_result.json for captured evidence.`);
  } catch (error) { toast(error.message); }
}

async function runKiCadDrc() {
  if (!currentProject) return;
  try {
    const response = await fetch(`/api/projects/${encodeURIComponent(currentProject.project_id)}/integrations/kicad-drc`, {method:'POST'});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `DRC request failed (${response.status})`);
    await refreshProject(currentProject.project_id);
    const detail = result.status === 'completed-no-reported-violations'
      ? `KiCad reported ${result.drc_violation_count} DRC violations and ${result.unconnected_pad_count} unconnected pads.`
      : `KiCad DRC status: ${result.status}.`;
    toast(`${detail} This is not electrical or manufacturing approval; see kicad_drc_result.json.`);
  } catch (error) { toast(error.message); }
}

async function loadTools() {
  try {
    const data = await (await request('/api/integrations')).json();
    const tools = data.integrations || [];
    const found = tools.filter((tool) => tool.status === 'detected').length;
    $('#integration-summary').innerHTML = `<span class="status-dot"></span> ${found} of ${tools.length} toolchains detected · probe only`;
    $('#tools-list').innerHTML = tools.map((tool) => `<article class="tool-row"><div class="project-glyph">⌘</div><div class="tool-info"><b>${escapeHtml(tool.name)}</b><small>${escapeHtml(tool.version || tool.detail)}${tool.executable ? ` · ${escapeHtml(tool.executable)}` : ''}</small></div><span class="tool-status ${escapeHtml(tool.status)}">${escapeHtml(tool.status)}</span></article>`).join('');
  } catch (error) { $('#integration-summary').textContent = `Toolchain check failed: ${error.message}`; }
}

document.querySelectorAll('.nav-item').forEach((button) => button.addEventListener('click', async () => {
  showView(button.dataset.view);
  if (button.dataset.view === 'projects') await loadProjects().catch((error) => toast(error.message));
  if (button.dataset.view === 'tools') await loadTools();
}));
document.querySelectorAll('.choice').forEach((choice) => choice.addEventListener('click', () => {
  document.querySelectorAll('.choice').forEach((item) => item.classList.remove('selected')); choice.classList.add('selected'); choice.querySelector('input').checked = true;
}));
$('#project-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = $('#submit-button'); button.disabled = true; button.querySelector('span:first-child').textContent = 'Analyzing…';
  try {
    const body = Object.fromEntries(new FormData(event.currentTarget).entries());
    const response = await request('/api/projects', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
    const project = await response.json();
    projectCache.unshift(project); setProjectCount(); renderResult(project); toast('Intake artifacts generated and saved locally.');
  } catch (error) { toast(error.message); }
  finally { button.disabled = false; button.querySelector('span:first-child').textContent = 'Analyze project'; }
});
$('#dialog-close').addEventListener('click', () => $('#artifact-dialog').close());
$('#artifact-dialog').addEventListener('click', (event) => { if (event.target === $('#artifact-dialog')) $('#artifact-dialog').close(); });
document.querySelectorAll('[data-close]').forEach((button) => button.addEventListener('click', () => $(`#${button.dataset.close}`).close()));
$('#validate-circuit').addEventListener('click', validateCircuitDraft);
$('#audit-circuit-symbols').addEventListener('click', auditCircuitSymbols);
$('#schematic-file').addEventListener('change', (event) => {
  const file = event.target.files?.[0]; event.target.value = '';
  importSchematicFile(file);
});
$('#board-file').addEventListener('change', (event) => {
  const file = event.target.files?.[0]; event.target.value = '';
  importBoardFile(file);
});
$('#datasheet-file').addEventListener('change', (event) => {
  const file = event.target.files?.[0]; event.target.value = '';
  importDatasheetFile(file);
});
$('#spice-file').addEventListener('change', (event) => {
  const file = event.target.files?.[0]; event.target.value = '';
  importSpiceFile(file);
});
$('#led-calc-form').addEventListener('submit', calculateLedResistor);
$('#clock-timebase-form').addEventListener('submit', calculateClockTimebase);
$('#clock-divider-form').addEventListener('submit', calculateClockDivider);
$('#mna-form').addEventListener('submit', runMna);
$('#graph-mna-form').addEventListener('submit', runGraphMna);
$('#rc-transient-form').addEventListener('submit', runGraphTransient);
$('#rc-synthesis-form').addEventListener('submit', runRcLowpassSynthesis);
$('#circuit-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    const model = JSON.parse($('#circuit-json').value);
    const response = await fetch(`/api/projects/${encodeURIComponent(currentProject.project_id)}/circuit`, {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({model})});
    const body = await response.json();
    if (!response.ok) {
      const errors = body.topology_report?.errors || [body.error || 'Unable to save model'];
      circuitFeedback(errors.join(' · '), 'error'); return;
    }
    $('#circuit-dialog').close();
    await refreshProject(currentProject.project_id);
    const exportState = body.kicad_export?.status || 'updated';
    toast(`Circuit model saved. ${body.snapshots.count} snapshot(s). Netlist: ${exportState}.`);
  } catch (error) { circuitFeedback(`Invalid JSON: ${error.message}`, 'error'); }
});
loadProjects().catch(() => {}); loadTools();
