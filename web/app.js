const $ = (selector) => document.querySelector(selector);
const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
let currentProject = null;
let pendingAgentPlan = null;
let pendingAgentExecution = null;
let projectCache = [];
let toastTimer;
let lastAcSweepOutputs = null;
let lastGraphAcSweepResult = null;
let lastDcSweepOutputs = null;
let lastGraphDcSweepResult = null;
let lastTransientResult = null;
let circuitUndoStack = [];
let circuitRedoStack = [];
let circuitLastCommittedText = '';
let circuitUndoGroupTimer = null;
let circuitDraftTimer = null;
let circuitDraftKey = null;
let circuitDraftAvailable = false;
let circuitBaseText = '';
let circuitDraftBaseMatches = true;
let circuitMapTimer = null;

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
  container.innerHTML = projectCache.map((project) => `<article class="project-row"><div class="project-glyph">⌁</div><div class="project-info"><b>${escapeHtml(project.name)}</b><small>${escapeHtml(project.architecture)} · ${escapeHtml(new Date(project.created_at).toLocaleString())}</small></div><span class="status-badge">${escapeHtml(project.status)}</span><a class="text-button" href="/api/projects/${encodeURIComponent(project.project_id)}/export" download>Backup ZIP</a><button class="text-button" data-open="${escapeHtml(project.project_id)}">Open</button></article>`).join('');
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
  const artifactButtons = (project.artifacts || []).map((name) => name.endsWith('.raw') || name.endsWith('.pdf') || name.endsWith('.csv')
    ? `<a class="artifact-button" href="/api/projects/${encodeURIComponent(project.project_id)}/artifact/${encodeURIComponent(name)}" download>↓ &nbsp;Download ${escapeHtml(name)}</a>`
    : `<button class="artifact-button" data-artifact="${escapeHtml(name)}">↗ &nbsp;${escapeHtml(name)}</button>`).join('');
  $('#view-result').innerHTML = `<div class="result-top"><div class="eyebrow"><span class="eyebrow-line"></span> ENGINEERING INTAKE COMPLETE</div><h1>${escapeHtml(project.name)}</h1><p>${escapeHtml(project.architecture)} architecture · Created ${escapeHtml(new Date(project.created_at).toLocaleString())}</p><div class="result-actions"><a class="primary-button" href="/api/projects/${encodeURIComponent(project.project_id)}/export"><span>Download project ZIP</span><span class="button-arrow">↓</span></a><button class="small-button" id="edit-circuit">Edit circuit model</button><button class="small-button" id="generate-connectivity-schedule">Pin-to-net schedule</button><button class="small-button" id="run-agent-circuit-review">Run deterministic circuit review</button><button class="small-button" id="open-agent-plan">Plan with local AI</button><button class="small-button" id="run-led-resistor-calculation">Analyze LED resistor</button><button class="small-button" id="run-clock-timebase-calculation">Analyze clock timebase</button><button class="small-button" id="run-clock-divider-calculation">Find divider candidate</button><button class="small-button" id="run-dc-mna">Solve explicit linear DC MNA</button><button class="small-button" id="run-dc-sweep">DC source sweep</button><button class="small-button" id="run-graph-dc-sweep">Graph-bound DC sweep</button><button class="small-button" id="run-graph-mna">Map graph to DC MNA</button><button class="small-button" id="check-operating-limits">Check component limits</button><button class="small-button" id="run-ac-sweep">AC frequency sweep</button><button class="small-button" id="run-graph-ac-sweep">Graph-bound AC sweep</button><button class="small-button" id="run-rc-transient">Run graph RC/RLC transient</button><button class="small-button" id="run-digital-truth-table">Digital truth table</button><button class="small-button" id="run-digital-waveform">Simulate digital waveform</button><button class="small-button" id="synthesize-rc-lowpass">Synthesize RC low-pass</button><button class="small-button" id="import-schematic">Import KiCad schematic</button><button class="small-button" id="run-kicad-erc">Run KiCad ERC</button><button class="small-button" id="import-board">Import KiCad PCB</button><button class="small-button" id="run-kicad-drc">Run KiCad DRC</button><button class="small-button" id="import-datasheet">Import datasheet PDF</button><button class="small-button" id="extract-datasheet-text">Extract latest PDF text</button><button class="small-button" id="import-spice">Import SPICE deck</button><button class="small-button" id="run-ngspice">Run ngspice</button><button class="small-button" id="show-snapshots">Snapshots</button><button class="small-button" id="back-to-brief">← New analysis</button></div></div>
  <div class="status-banner">⚠ &nbsp; Incomplete — this is requirements and feasibility analysis, not a completed electrical design.</div>
  <div class="result-grid"><div><section class="result-panel"><h2>Feasibility findings <span class="optional">${findings.length} ITEMS</span></h2>${findings.map((finding) => `<div class="finding"><span class="finding-icon">!</span><span>${escapeHtml(finding)}</span></div>`).join('')}</section>
  <section class="result-panel"><h2>Supplied component inventory</h2>${inventoryRows ? `<table class="inventory-table"><thead><tr><th>PART / DESCRIPTION</th><th>QTY</th><th>VERIFICATION</th></tr></thead><tbody>${inventoryRows}</tbody></table>` : '<div class="empty-state">No inventory provided.</div>'}</section></div>
  <div><section class="result-panel"><h2>Validation ledger</h2>${statusLines}</section><section class="result-panel"><h2>Engineering calculations</h2><p class="calc-explanation">“Current” means the report’s circuit-model hash matches this revision; it is not design approval.</p><div id="calculation-status"><div class="empty-state">Checking calculation revision status…</div></div></section><section class="result-panel"><h2>Saved simulation runs</h2><p class="calc-explanation">DC, transient, and digital reports are associated with this project revision; digital netlists are explicit user input and are not derived from the saved graph.</p><div id="simulation-status"><div class="empty-state">Checking saved simulation reports…</div></div></section><section class="result-panel"><h2>Generated artifacts <span class="optional">${(project.artifacts||[]).length}</span></h2><div class="artifact-list">${artifactButtons}</div></section></div></div>`;
  $('#view-result').querySelectorAll('[data-artifact]').forEach((button) => button.addEventListener('click', () => openArtifact(project.project_id, button.dataset.artifact)));
  $('#back-to-brief').addEventListener('click', () => showView('new'));
  $('#edit-circuit').addEventListener('click', openCircuitEditor);
  $('#generate-connectivity-schedule').addEventListener('click', generateConnectivitySchedule);
  $('#run-agent-circuit-review').addEventListener('click', runAgentCircuitReview);
  $('#open-agent-plan').addEventListener('click', openAgentPlanDialog);
  $('#run-led-resistor-calculation').addEventListener('click', openLedResistorCalculation);
  $('#run-clock-timebase-calculation').addEventListener('click', openClockTimebaseCalculation);
  $('#run-clock-divider-calculation').addEventListener('click', openClockDividerCalculation);
  $('#run-dc-mna').addEventListener('click', openMnaDialog);
  $('#run-dc-sweep').addEventListener('click', openDcSweepDialog);
  $('#run-graph-dc-sweep').addEventListener('click', openGraphDcSweepDialog);
  $('#run-graph-mna').addEventListener('click', openGraphMnaDialog);
  $('#check-operating-limits').addEventListener('click', openOperatingLimitsDialog);
  $('#run-ac-sweep').addEventListener('click', openAcSweepDialog);
  $('#run-graph-ac-sweep').addEventListener('click', openGraphAcSweepDialog);
  $('#run-rc-transient').addEventListener('click', openGraphTransientDialog);
  $('#run-digital-truth-table').addEventListener('click', openDigitalTruthTableDialog);
  $('#run-digital-waveform').addEventListener('click', openDigitalWaveformDialog);
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
    if (!data.simulations?.length) { node.innerHTML = '<div class="empty-state">No simulation runs have been saved.</div>'; return; }
    node.innerHTML = [...data.simulations].reverse().map((run) => `<div class="validation-line"><span>${escapeHtml(run.kind.replaceAll('_',' '))}</span><span class="calc-state ${run.model_status === 'same-at-run' && run.netlist_status === 'intact' ? 'current' : 'stale'}">${escapeHtml(run.model_status)} · netlist ${escapeHtml(run.netlist_status)} · map ${escapeHtml(run.mapping_status)} · ${escapeHtml(new Date(run.created_at).toLocaleString())}</span></div>`).join('');
  } catch (error) { node.textContent = `Simulation history unavailable: ${error.message}`; }
}
function addProjectArtifact(projectId, artifactName) {
  const artifactList = $('#view-result .artifact-list');
  if (!artifactList || artifactList.querySelector(`[data-artifact="${artifactName}"]`)) return;
  const downloadable = artifactName.endsWith('.csv');
  const artifactButton = document.createElement(downloadable ? 'a' : 'button');
  artifactButton.className = 'artifact-button';
  if (downloadable) {
    artifactButton.href = `/api/projects/${encodeURIComponent(projectId)}/artifact/${encodeURIComponent(artifactName)}`;
    artifactButton.download = artifactName;
  } else {
    artifactButton.dataset.artifact = artifactName;
    artifactButton.addEventListener('click', () => openArtifact(projectId, artifactName));
  }
  artifactButton.textContent = `${downloadable ? '↓' : '↗'}  ${artifactName}`;
  artifactList.append(artifactButton);
}
function addSimulationArtifact(projectId, report) {
  addProjectArtifact(projectId, report.artifact);
}
async function runAgentCircuitReview() {
  if (!currentProject) return;
  const projectId = currentProject.project_id;
  try {
    const response = await request(`/api/projects/${encodeURIComponent(projectId)}/agent/workflows/run`, {
      method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({workflow:'circuit_review'})
    });
    const report = await response.json();
    await refreshProject(projectId);
    const passed = report.result.checks.filter((check) => check.passed).length;
    toast(`Circuit review ${report.status}: ${passed}/${report.result.checks.length} checks. No electrical analysis performed.`);
  } catch (error) { toast(error.message); }
}

function openAgentPlanDialog() {
  if (!currentProject) return;
  pendingAgentPlan = null; pendingAgentExecution = null;
  $('#agent-plan-prompt').value = '';
  $('#agent-plan-feedback').textContent = '';
  $('#agent-plan-result').textContent = '';
  $('#agent-plan-result').classList.add('hidden');
  $('#execute-agent-plan').disabled = true;
  $('#summarize-agent-result').disabled = true;
  $('#load-agent-summaries').disabled = true;
  $('#agent-execution-history').innerHTML = '<option value="">Select a verified execution</option>';
  $('#load-agent-execution').disabled = true;
  $('#agent-plan-dialog').showModal();
  void refreshAgentExecutionHistory();
}
async function generateAgentPlan(event) {
  event.preventDefault();
  if (!currentProject) return;
  const projectId = currentProject.project_id;
  const prompt = $('#agent-plan-prompt').value.trim();
  const generateButton = $('#generate-agent-plan');
  generateButton.disabled = true; generateButton.textContent = 'Generating…';
  $('#agent-plan-feedback').textContent = 'Waiting for the configured local model; no tools are executing.';
  $('#execute-agent-plan').disabled = true; $('#summarize-agent-result').disabled = true;
  $('#load-agent-summaries').disabled = true;
  pendingAgentPlan = null; pendingAgentExecution = null;
  try {
    const response = await request(`/api/projects/${encodeURIComponent(projectId)}/agent/plan`, {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({prompt})
    });
    const plan = await response.json();
    pendingAgentPlan = {project_id: projectId, calls: plan.calls, execution_url: plan.execution_url};
    $('#agent-plan-result').textContent = JSON.stringify({provider:plan.provider, model:plan.model,
      status:plan.status, summary:plan.summary, calls:plan.calls, prompt_sha256:plan.prompt_sha256,
      completion_sha256:plan.completion_sha256, artifact:plan.artifact}, null, 2);
    $('#agent-plan-result').classList.remove('hidden');
    $('#agent-plan-feedback').textContent = 'Review this model-generated proposal. It has not executed.';
    $('#execute-agent-plan').disabled = false;
    await refreshProject(projectId);
  } catch (error) { $('#agent-plan-feedback').textContent = error.message; }
  finally { generateButton.disabled = false; generateButton.textContent = 'Generate plan'; }
}
async function executeAgentPlan() {
  const plan = pendingAgentPlan;
  if (!plan || currentProject?.project_id !== plan.project_id) return;
  if (!window.confirm('Execute only these validated read-only tools against the saved project?')) return;
  const button = $('#execute-agent-plan'); button.disabled = true;
  $('#agent-plan-feedback').textContent = 'Executing the reviewed read-only calls…';
  try {
    const response = await request(plan.execution_url, {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({execute:true})});
    const result = await response.json();
    $('#agent-plan-result').textContent = JSON.stringify(result, null, 2);
    const status = result.report?.status || 'failed';
    $('#agent-plan-feedback').textContent = `Execution ${status}. Audit records are saved with the project.`;
    pendingAgentPlan = null;
    pendingAgentExecution = {project_id: plan.project_id, execution_id: result.report?.execution_id};
    $('#summarize-agent-result').disabled = !pendingAgentExecution.execution_id;
    $('#load-agent-summaries').disabled = !pendingAgentExecution.execution_id;
    await refreshProject(plan.project_id);
    toast(`Reviewed plan ${status}. No circuit changes were made.`);
  } catch (error) { $('#agent-plan-feedback').textContent = error.message; button.disabled = false; }
}

async function summarizeAgentExecution() {
  const execution = pendingAgentExecution;
  if (!execution || currentProject?.project_id !== execution.project_id) return;
  if (!window.confirm('Send only the saved compact topology/connectivity checks to the configured local model for an unverified narrative summary?')) return;
  const button = $('#summarize-agent-result'); button.disabled = true;
  $('#agent-plan-feedback').textContent = 'Requesting a local-model summary of compact saved execution evidence…';
  try {
    const response = await request(`/api/projects/${encodeURIComponent(execution.project_id)}/agent/executions/${encodeURIComponent(execution.execution_id)}/summarize`, {
      method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'
    });
    const summary = await response.json();
    $('#agent-plan-result').textContent = JSON.stringify(summary, null, 2);
    $('#agent-plan-feedback').textContent = 'AI-generated unverified narrative. Inspect the cited evidence keys and original execution report.';
    await refreshProject(execution.project_id);
  } catch (error) {
    $('#agent-plan-feedback').textContent = error.message;
    button.disabled = false;
  }
}

async function loadSavedAgentSummaries() {
  const execution = pendingAgentExecution;
  if (!execution || currentProject?.project_id !== execution.project_id) return;
  const button = $('#load-agent-summaries'); button.disabled = true;
  $('#agent-plan-feedback').textContent = 'Verifying saved summaries against the source report and audit chain…';
  try {
    const response = await request(`/api/projects/${encodeURIComponent(execution.project_id)}/agent/executions/${encodeURIComponent(execution.execution_id)}/summaries`);
    const result = await response.json();
    $('#agent-plan-result').textContent = JSON.stringify(result, null, 2);
    $('#agent-plan-feedback').textContent = 'Saved summary integrity is checked; a valid digest and audit link do not establish narrative truth or electrical correctness.';
  } catch (error) {
    $('#agent-plan-feedback').textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

async function refreshAgentExecutionHistory() {
  if (!currentProject) return;
  const projectId = currentProject.project_id;
  const select = $('#agent-execution-history');
  const button = $('#refresh-agent-executions'); button.disabled = true;
  try {
    const response = await request(`/api/projects/${encodeURIComponent(projectId)}/agent/executions`);
    const result = await response.json();
    if (currentProject?.project_id !== projectId) return;
    select.replaceChildren(new Option('Select a verified execution', ''));
    for (const execution of result.executions || []) {
      const label = `${execution.created_at || 'Unknown date'} · ${execution.status || 'unknown'} · ${execution.integrity}`;
      const option = new Option(label, execution.execution_id || '');
      option.disabled = execution.integrity !== 'verified';
      select.add(option);
    }
    $('#load-agent-execution').disabled = true;
    $('#agent-plan-feedback').textContent = `${(result.executions || []).filter((run) => run.integrity === 'verified').length} verified report(s) listed. Integrity is not proof of design correctness.`;
  } catch (error) {
    $('#agent-plan-feedback').textContent = error.message;
  } finally { button.disabled = false; }
}

async function loadSelectedAgentExecution() {
  if (!currentProject) return;
  const projectId = currentProject.project_id;
  const executionId = $('#agent-execution-history').value;
  if (!executionId) return;
  const button = $('#load-agent-execution'); button.disabled = true;
  try {
    const response = await request(`/api/projects/${encodeURIComponent(projectId)}/agent/executions/${encodeURIComponent(executionId)}`);
    const result = await response.json();
    if (currentProject?.project_id !== projectId || $('#agent-execution-history').value !== executionId) return;
    pendingAgentExecution = {project_id: projectId, execution_id: executionId};
    $('#agent-plan-result').textContent = JSON.stringify(result, null, 2);
    $('#agent-plan-result').classList.remove('hidden');
    $('#summarize-agent-result').disabled = false;
    $('#load-agent-summaries').disabled = false;
    $('#agent-plan-feedback').textContent = 'Loaded a report whose digest and audit link verified. Checks do not prove electrical correctness or design approval.';
  } catch (error) {
    $('#agent-plan-feedback').textContent = error.message;
  } finally { button.disabled = false; }
}

async function generateConnectivitySchedule(event) {
  const button = event.currentTarget;
  if (!currentProject) return;
  button.disabled = true;
  try {
    const projectId = currentProject.project_id;
    const report = await (await request(`/api/projects/${encodeURIComponent(projectId)}/circuit/connectivity-schedule`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'
    })).json();
    addProjectArtifact(projectId, report.artifact);
    addProjectArtifact(projectId, report.csv_artifact);
    await loadProjects();
    currentProject = projectCache.find((project) => project.project_id === projectId) || currentProject;
    toast(`Pin-to-net schedule saved: ${report.net_count} nets, ${report.endpoint_count} endpoints, ${report.unconnected_pin_count} unconnected pins, ${report.no_connect_pin_count} declared no-connect pins. No physical wiring or electrical check is implied.`);
  } catch (error) {
    toast(`Connectivity schedule unavailable: ${error.message}`);
  } finally {
    button.disabled = false;
  }
}
const AC_SWEEP_EXAMPLE = {
  ground: '0',
  frequency_sweep: {
    start_frequency: {value: 10, unit: 'Hz'},
    stop_frequency: {value: 100000, unit: 'Hz'},
    points: 101,
    scale: 'log'
  },
  elements: [
    {type: 'voltage_source', reference: 'V1', nodes: ['VIN', '0'], voltage: {value: 1, unit: 'V'}, phase_degrees: 0},
    {type: 'resistor', reference: 'R1', nodes: ['VIN', 'OUT'], resistance: {value: 10000, unit: 'ohm'}},
    {type: 'capacitor', reference: 'C1', nodes: ['OUT', '0'], capacitance: {value: 1.5915494309189535e-8, unit: 'F'}}
  ]
};
const DC_SWEEP_EXAMPLE = {
  netlist: {ground: '0', elements: [
    {type: 'voltage_source', reference: 'V1', nodes: ['VIN', '0'], voltage: {value: 5, unit: 'V'}},
    {type: 'resistor', reference: 'R1', nodes: ['VIN', 'OUT'], resistance: {value: 10000, unit: 'ohm'}},
    {type: 'resistor', reference: 'R2', nodes: ['OUT', '0'], resistance: {value: 10000, unit: 'ohm'}}
  ]},
  sweep: {reference: 'V1', start_value: {value: 0, unit: 'V'}, stop_value: {value: 10, unit: 'V'}, points: 51, scale: 'linear'}
};
function openDcSweepDialog() {
  const editor = $('#dc-sweep-netlist');
  if (!editor.value.trim()) editor.value = JSON.stringify(DC_SWEEP_EXAMPLE, null, 2);
  $('#dc-sweep-feedback').textContent = 'The starter circuit is a 10 kΩ/10 kΩ divider. Edit the full explicit netlist and sweep before running if needed.';
  $('#dc-sweep-feedback').className = 'dialog-feedback';
  $('#dc-sweep-result').classList.add('hidden');
  lastDcSweepOutputs = null;
  $('#dc-sweep-dialog').showModal();
}
function drawDcSweepTrace(outputs, label, trace, canvasSelector, tableSelector) {
  const values = outputs.sweep_values;
  if (!values?.length || !trace || values.length !== trace.length) throw new Error('The native engine returned an invalid DC sweep trace.');
  const canvas = $(canvasSelector);
  const width = 1100, height = 300, left = 105, right = 1070, top = 24, bottom = 250;
  canvas.width = width; canvas.height = height;
  canvas.setAttribute('aria-label', `${label} voltage versus ${outputs.swept_element_reference} ${outputs.swept_parameter}, ${values.length} points`);
  const context = canvas.getContext('2d');
  if (!context) throw new Error('Canvas rendering is unavailable in this browser.');
  context.clearRect(0, 0, width, height); context.fillStyle = '#111613'; context.fillRect(0, 0, width, height);
  let low = Math.min(...trace), high = Math.max(...trace);
  if (high - low < 1e-15) { const pad = Math.max(Math.abs(high) * 0.1, 1e-9); low -= pad; high += pad; }
  else { const pad = (high - low) * 0.08; low -= pad; high += pad; }
  const xAt = (index) => left + (right - left) * index / Math.max(1, values.length - 1);
  const yAt = (value) => bottom - (value - low) * (bottom - top) / (high - low);
  const unit = outputs.sweep_unit || (outputs.swept_parameter === 'voltage' ? 'V' : 'A');
  context.font = '11px "DM Mono", monospace'; context.textBaseline = 'middle';
  context.strokeStyle = '#2a322d'; context.fillStyle = '#94a096'; context.textAlign = 'right';
  for (let tick = 0; tick <= 4; tick += 1) {
    const fraction = tick / 4, y = top + (bottom - top) * fraction;
    context.beginPath(); context.moveTo(left, y); context.lineTo(right, y); context.stroke();
    context.fillText(`${(high - (high - low) * fraction).toPrecision(3)} V`, left - 9, y);
  }
  context.strokeStyle = '#627067'; context.fillStyle = '#a4aea5'; context.textAlign = 'center';
  for (let tick = 0; tick <= 4; tick += 1) {
    const index = Math.round((values.length - 1) * tick / 4), x = xAt(index);
    context.beginPath(); context.moveTo(x, top); context.lineTo(x, bottom); context.stroke();
    context.fillText(`${Number(values[index]).toPrecision(4)} ${unit}`, x, bottom + 20);
  }
  context.strokeStyle = '#b4f077'; context.lineWidth = 2; context.beginPath();
  trace.forEach((voltage, index) => { if (index === 0) context.moveTo(xAt(index), yAt(voltage)); else context.lineTo(xAt(index), yAt(voltage)); });
  context.stroke(); context.fillStyle = '#b9c3ba'; context.textAlign = 'left';
  context.fillText(`${label} · ${outputs.swept_element_reference} ${outputs.swept_parameter} (${outputs.sweep_scale})`, left, 11);
  const rows = values.slice(0, 100).map((value, index) => `<tr><td>${index}</td><td>${Number(value).toPrecision(7)} ${unit}</td><td>${Number(trace[index]).toPrecision(7)} V</td></tr>`).join('');
  $(tableSelector).innerHTML = `<p class="waveform-table-note">${values.length > 100 ? `Showing first 100 of ${values.length} points; ` : ''}full traces are saved in the report.</p><table><thead><tr><th>Point</th><th>${escapeHtml(outputs.swept_element_reference)} ${escapeHtml(outputs.swept_parameter)}</th><th>${escapeHtml(label)} voltage</th></tr></thead><tbody>${rows}</tbody></table>`;
}
function renderDcSweepNode() {
  if (!lastDcSweepOutputs) return;
  const node = $('#dc-sweep-node').value;
  drawDcSweepTrace(lastDcSweepOutputs, node, lastDcSweepOutputs.node_voltage_traces_v[node], '#dc-sweep-canvas', '#dc-sweep-table');
}
function renderGraphDcSweepNode() {
  if (!lastGraphDcSweepResult) return;
  const name = $('#graph-dc-sweep-node').value;
  const solverNode = lastGraphDcSweepResult.netMap[name];
  const trace = lastGraphDcSweepResult.outputs.node_voltage_traces_v[String(solverNode)];
  drawDcSweepTrace(lastGraphDcSweepResult.outputs, name, trace, '#graph-dc-sweep-canvas', '#graph-dc-sweep-table');
}
async function runDcSweep(event) {
  event.preventDefault();
  const feedback = $('#dc-sweep-feedback');
  const button = $('#dc-sweep-form button[type="submit"]');
  button.disabled = true;
  $('#dc-sweep-result').classList.add('hidden');
  lastDcSweepOutputs = null;
  try {
    const payload = JSON.parse($('#dc-sweep-netlist').value);
    const report = await (await request(`/api/projects/${encodeURIComponent(currentProject.project_id)}/simulations/dc-sweep`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)
    })).json();
    const outputs = report.calculation.outputs;
    const nodes = Object.keys(outputs.node_voltage_traces_v || {});
    if (!nodes.length) throw new Error('No non-ground node traces were returned.');
    const picker = $('#dc-sweep-node'); picker.replaceChildren();
    nodes.forEach((name) => { const option = document.createElement('option'); option.value = name; option.textContent = name; picker.append(option); });
    lastDcSweepOutputs = outputs; picker.value = nodes.includes('OUT') ? 'OUT' : nodes[0];
    renderDcSweepNode(); $('#dc-sweep-result').classList.remove('hidden');
    feedback.textContent = `${outputs.point_count} points · ${outputs.swept_element_reference} ${outputs.swept_parameter} · ${outputs.solver_engine} ${outputs.solver_version}. Maximum normalized residual ${Number(outputs.maximum_normalized_equation_residual).toExponential(3)}. Saved ${report.artifact}; netlist SHA-256 ${report.netlist_sha256.slice(0, 16)}….`;
    feedback.className = 'dialog-feedback ok';
    addSimulationArtifact(currentProject.project_id, report);
    await loadSimulationStatus(currentProject.project_id);
  } catch (error) {
    feedback.textContent = error instanceof SyntaxError ? `Invalid JSON: ${error.message}` : error.message;
    feedback.className = 'dialog-feedback error';
  } finally {
    button.disabled = false;
  }
}
function openGraphDcSweepDialog() {
  $('#graph-dc-sweep-feedback').textContent = 'Supply a complete mapping and exactly one source sweep. Every component must have an explicit type, ordered pins, values/parameters, or a reasoned exclusion. Saved graph connectivity does not supply electrical models.';
  $('#graph-dc-sweep-feedback').className = 'dialog-feedback';
  $('#graph-dc-sweep-result').classList.add('hidden');
  lastGraphDcSweepResult = null;
  $('#graph-dc-sweep-dialog').showModal();
}
async function runGraphDcSweep(event) {
  event.preventDefault();
  const feedback = $('#graph-dc-sweep-feedback');
  const button = $('#graph-dc-sweep-form button[type="submit"]');
  button.disabled = true;
  $('#graph-dc-sweep-result').classList.add('hidden');
  lastGraphDcSweepResult = null;
  try {
    const payload = JSON.parse($('#graph-dc-sweep-input').value);
    const projectId = currentProject.project_id;
    const report = await (await request(`/api/projects/${encodeURIComponent(projectId)}/simulations/dc-sweep/from-model`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)
    })).json();
    const outputs = report.calculation.outputs;
    const netMap = report.mapping_report.net_name_to_solver_node;
    const nodes = Object.entries(netMap).filter(([, solverNode]) => String(solverNode) !== '0' && outputs.node_voltage_traces_v[String(solverNode)]);
    if (!nodes.length) throw new Error('No non-ground node traces were returned.');
    const picker = $('#graph-dc-sweep-node'); picker.replaceChildren();
    nodes.forEach(([name]) => { const option = document.createElement('option'); option.value = name; option.textContent = name; picker.append(option); });
    lastGraphDcSweepResult = {outputs, netMap};
    picker.value = nodes.some(([name]) => name === 'OUT') ? 'OUT' : nodes[0][0];
    renderGraphDcSweepNode(); $('#graph-dc-sweep-result').classList.remove('hidden');
    const coverage = report.component_coverage;
    feedback.textContent = `${outputs.point_count} points · ${outputs.swept_element_reference} ${outputs.swept_parameter} · mapped ${coverage.mapped}; explicitly excluded ${coverage.excluded} of ${coverage.total}. Maximum normalized residual ${Number(outputs.maximum_normalized_equation_residual).toExponential(3)}. Saved ${report.artifact}. The model and mapping SHA-256 values are recorded in the report.`;
    feedback.className = coverage.has_exclusions ? 'dialog-feedback' : 'dialog-feedback ok';
    addSimulationArtifact(projectId, report);
    await loadProjects();
    currentProject = projectCache.find((project) => project.project_id === projectId) || currentProject;
    await loadSimulationStatus(projectId);
  } catch (error) {
    feedback.textContent = error instanceof SyntaxError ? `Invalid JSON: ${error.message}` : error.message;
    feedback.className = 'dialog-feedback error';
  } finally {
    button.disabled = false;
  }
}
function openAcSweepDialog() {
  const editor = $('#ac-sweep-netlist');
  if (!editor.value.trim()) editor.value = JSON.stringify(AC_SWEEP_EXAMPLE, null, 2);
  $('#ac-sweep-feedback').textContent = 'The starter is a 10 kΩ / 15.9 nF low-pass; values are example inputs, not values inferred from this project.';
  $('#ac-sweep-feedback').className = 'dialog-feedback';
  $('#ac-sweep-result').classList.add('hidden');
  lastAcSweepOutputs = null;
  $('#ac-sweep-dialog').showModal();
}
function formatFrequency(value) {
  if (value >= 1e6) return `${(value / 1e6).toPrecision(3)} MHz`;
  if (value >= 1e3) return `${(value / 1e3).toPrecision(3)} kHz`;
  return `${Number(value).toPrecision(3)} Hz`;
}
function drawAcTrace(canvas, frequencies, values, scale, unit, description, color) {
  if (!frequencies.length || frequencies.length !== values.length) throw new Error('The native engine returned an invalid AC trace.');
  const width = 1100, height = canvas.height, left = 102, right = 1070, top = 24, bottom = height - 44;
  canvas.width = width;
  canvas.setAttribute('aria-label', `${description} versus frequency, ${frequencies.length} points`);
  const context = canvas.getContext('2d');
  if (!context) throw new Error('Canvas rendering is unavailable in this browser.');
  context.clearRect(0, 0, width, height);
  context.fillStyle = '#111613'; context.fillRect(0, 0, width, height);
  let low = Math.min(...values), high = Math.max(...values);
  if (high - low < 1e-15) { const pad = Math.max(Math.abs(high) * 0.1, 1e-9); low -= pad; high += pad; }
  else { const pad = (high - low) * 0.08; low -= pad; high += pad; }
  const xAt = (index) => left + (right - left) * index / Math.max(1, frequencies.length - 1);
  const yAt = (value) => bottom - (value - low) * (bottom - top) / (high - low);
  context.font = '11px "DM Mono", monospace'; context.textBaseline = 'middle';
  context.strokeStyle = '#2a322d'; context.fillStyle = '#94a096'; context.lineWidth = 1;
  context.textAlign = 'right';
  for (let tick = 0; tick <= 4; tick += 1) {
    const fraction = tick / 4, y = top + (bottom - top) * fraction;
    const value = high - (high - low) * fraction;
    context.beginPath(); context.moveTo(left, y); context.lineTo(right, y); context.stroke();
    context.fillText(`${value.toPrecision(3)} ${unit}`, left - 9, y);
  }
  context.strokeStyle = '#627067'; context.fillStyle = '#a4aea5'; context.textAlign = 'center';
  for (let tick = 0; tick <= 4; tick += 1) {
    const index = Math.round((frequencies.length - 1) * tick / 4), x = xAt(index);
    context.beginPath(); context.moveTo(x, top); context.lineTo(x, bottom); context.stroke();
    context.fillText(formatFrequency(frequencies[index]), x, bottom + 20);
  }
  context.strokeStyle = color; context.lineWidth = 2; context.beginPath();
  values.forEach((value, index) => {
    const x = xAt(index), y = yAt(value);
    if (index === 0) context.moveTo(x, y); else context.lineTo(x, y);
  });
  context.stroke();
  context.fillStyle = '#b9c3ba'; context.textAlign = 'left'; context.fillText(description, left, 11);
  context.textAlign = 'right'; context.fillText(scale === 'log' ? 'log-spaced sweep' : 'linear-spaced sweep', right, 11);
}
function renderAcSweepNode() {
  if (!lastAcSweepOutputs) return;
  const node = $('#ac-sweep-node').value;
  const outputs = lastAcSweepOutputs;
  const frequencies = outputs.frequency_points_hz;
  const magnitudes = outputs.node_voltage_magnitude_traces_v[node];
  const phases = outputs.node_voltage_phase_traces_deg[node];
  drawAcTrace($('#ac-sweep-magnitude-canvas'), frequencies, magnitudes, outputs.scale,
              'V RMS', `${node} magnitude`, '#b4f077');
  drawAcTrace($('#ac-sweep-phase-canvas'), frequencies, phases, outputs.scale,
              'deg', `${node} phase (wrapped)`, '#78c7ff');
  const rows = frequencies.slice(0, 100).map((frequency, index) =>
    `<tr><td>${formatFrequency(frequency)}</td><td>${Number(magnitudes[index]).toPrecision(6)} V</td><td>${Number(phases[index]).toPrecision(6)}°</td></tr>`).join('');
  $('#ac-sweep-table').innerHTML = `<p class="waveform-table-note">${frequencies.length > 100 ? `Showing first 100 of ${frequencies.length} points; ` : ''}full phasor traces are saved in the report.</p><table><thead><tr><th>Frequency</th><th>${escapeHtml(node)} magnitude</th><th>${escapeHtml(node)} phase</th></tr></thead><tbody>${rows}</tbody></table>`;
}
async function runAcSweep(event) {
  event.preventDefault();
  const feedback = $('#ac-sweep-feedback');
  const button = $('#ac-sweep-form button[type="submit"]');
  button.disabled = true;
  $('#ac-sweep-result').classList.add('hidden');
  lastAcSweepOutputs = null;
  try {
    const netlist = JSON.parse($('#ac-sweep-netlist').value);
    const report = await (await request(`/api/projects/${encodeURIComponent(currentProject.project_id)}/simulations/ac-sweep`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({netlist})
    })).json();
    const outputs = report.calculation.outputs;
    const nodes = Object.keys(outputs.node_voltage_magnitude_traces_v).filter((node) => node !== netlist.ground);
    if (!nodes.length) throw new Error('No non-ground node traces were returned.');
    const picker = $('#ac-sweep-node'); picker.replaceChildren();
    nodes.forEach((node) => { const option = document.createElement('option'); option.value = node; option.textContent = node; picker.append(option); });
    lastAcSweepOutputs = outputs;
    picker.value = nodes.includes('OUT') ? 'OUT' : nodes[0];
    renderAcSweepNode();
    $('#ac-sweep-result').classList.remove('hidden');
    feedback.textContent = `${outputs.frequency_points_hz.length} points · ${outputs.element_count} elements · ${outputs.engine}. Maximum normalized residual ${Number(outputs.maximum_normalized_equation_residual).toExponential(3)}. Saved ${report.artifact}; input SHA-256 ${report.netlist_sha256.slice(0, 16)}….`;
    feedback.className = 'dialog-feedback ok';
    addSimulationArtifact(currentProject.project_id, report);
    await loadSimulationStatus(currentProject.project_id);
  } catch (error) {
    feedback.textContent = error instanceof SyntaxError ? `Invalid JSON: ${error.message}` : error.message;
    feedback.className = 'dialog-feedback error';
  } finally {
    button.disabled = false;
  }
}
function updateOperatingLimitsHelp() {
  const transient = $('#operating-limits-run').selectedOptions[0]?.dataset.mode === 'transient';
  $('#operating-limits-guidance').textContent = transient
    ? 'Transient mode compares peak absolute capacitor voltage (max_abs_voltage) or inductor current (max_abs_current) in saved samples. It cannot detect inter-sample peaks. Enter only references and bounds present in the selected run.'
    : 'DC mode compares absolute element voltage, current, and absorbed power in the saved operating-point report. Enter only references and metrics present in the selected run.';
  $('#operating-limits-input').placeholder = transient
    ? '[{"reference":"C1","max_abs_voltage":{"value":10,"unit":"V"}},{"reference":"L1","max_abs_current":{"value":100,"unit":"mA"}}]'
    : '[{"reference":"R1","max_abs_voltage":{"value":10,"unit":"V"},"max_abs_current":{"value":50,"unit":"mA"},"max_abs_power":{"value":0.25,"unit":"W"}}]';
}
async function openOperatingLimitsDialog() {
  if (!currentProject) return;
  const picker = $('#operating-limits-run');
  picker.replaceChildren();
  $('#operating-limits-feedback').textContent = 'Limits are explicit user-entered bounds only; the app will not look up or infer ratings.';
  $('#operating-limits-feedback').className = 'dialog-feedback';
  $('#operating-limits-result').classList.add('hidden');
  $('#operating-limits-form button[type="submit"]').disabled = true;
  $('#operating-limits-dialog').showModal();
  try {
    const data = await (await request(`/api/projects/${encodeURIComponent(currentProject.project_id)}/simulations`)).json();
    const accepted = new Set(['circuit_graph_bound_linear_dc_mna_operating_point', 'circuit_graph_bound_piecewise_linear_diode_dc_operating_point', 'circuit_graph_bound_shockley_diode_dc_operating_point', 'circuit_graph_bound_linear_rc_transient', 'circuit_graph_bound_linear_rlc_transient']);
    const runs = [...(data.simulations || [])].filter((run) => accepted.has(run.kind)).reverse();
    runs.forEach((run) => { const option = document.createElement('option'); option.value = run.artifact; option.dataset.mode = run.kind.includes('transient') ? 'transient' : 'dc'; option.textContent = `${run.kind.replaceAll('_',' ')} · ${new Date(run.created_at).toLocaleString()} · ${run.artifact}`; picker.append(option); });
    $('#operating-limits-form button[type="submit"]').disabled = runs.length === 0;
    if (runs.length) updateOperatingLimitsHelp();
    if (!runs.length) {
      $('#operating-limits-feedback').textContent = 'No saved graph-bound DC operating-point report is available. Run graph-bound DC MNA first.';
      $('#operating-limits-feedback').className = 'dialog-feedback';
    }
  } catch (error) {
    $('#operating-limits-feedback').textContent = `Simulation history unavailable: ${error.message}`;
    $('#operating-limits-feedback').className = 'dialog-feedback error';
  }
}
function renderOperatingLimitsResult(report) {
  const evaluations = report.assessment.evaluations || [];
  const checks = evaluations.flatMap((item) => item.checks
    ? item.checks.map((check) => ({reference: item.reference, ...check}))
    : [item]);
  const rows = checks.slice(0, 100).map((check) => {
    const measured = check.measured_absolute || check.measured_absolute_peak;
    return `<tr><td>${escapeHtml(check.reference)}</td><td>${escapeHtml(check.metric.replaceAll('_',' '))}</td><td>${Number(measured.value).toPrecision(6)} ${escapeHtml(measured.unit)}</td><td>${check.peak_time ? `${Number(check.peak_time.value).toPrecision(6)} s` : '—'}</td><td>${Number(check.user_supplied_maximum.value).toPrecision(6)} ${escapeHtml(check.user_supplied_maximum.unit)}</td><td class="limit-status ${check.status}">${escapeHtml(check.status)}</td></tr>`;
  }).join('');
  $('#operating-limits-table').innerHTML = `<p class="waveform-table-note">${checks.length > 100 ? `Showing first 100 of ${checks.length} comparisons. ` : ''}Only entered bounds and (for transient runs) saved sample peaks were checked. Limits were not verified against manufacturer data.</p><table><thead><tr><th>Reference</th><th>Metric</th><th>Measured magnitude</th><th>Peak time</th><th>User maximum</th><th>Status</th></tr></thead><tbody>${rows}</tbody></table>`;
  $('#operating-limits-result').classList.remove('hidden');
}
async function runOperatingLimits(event) {
  event.preventDefault();
  const feedback = $('#operating-limits-feedback');
  const button = $('#operating-limits-form button[type="submit"]');
  button.disabled = true;
  $('#operating-limits-result').classList.add('hidden');
  try {
    const limits = JSON.parse($('#operating-limits-input').value);
    const projectId = currentProject.project_id;
    const mode = $('#operating-limits-run').selectedOptions[0]?.dataset.mode || 'dc';
    const endpoint = mode === 'transient' ? 'transient-limits' : 'operating-limits';
    const response = await request(`/api/projects/${encodeURIComponent(projectId)}/simulations/${endpoint}`, {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({simulation_artifact: $('#operating-limits-run').value, limits})
    });
    const report = await response.json();
    renderOperatingLimitsResult(report);
    feedback.textContent = `${report.status.toUpperCase()} · ${report.assessment.check_count} bounds checked, ${report.assessment.failure_count} failed. Source run model status: ${report.model_status}. ${report.artifact} saved. ${report.warnings.join(' ')}`;
    feedback.className = report.status === 'fail' ? 'dialog-feedback error' : 'dialog-feedback ok';
    addProjectArtifact(projectId, report.artifact);
    await loadProjects();
    currentProject = projectCache.find((project) => project.project_id === projectId) || currentProject;
  } catch (error) {
    feedback.textContent = error instanceof SyntaxError ? `Invalid limits JSON: ${error.message}` : error.message;
    feedback.className = 'dialog-feedback error';
  } finally {
    button.disabled = false;
  }
}
function openGraphAcSweepDialog() {
  $('#graph-ac-sweep-feedback').textContent = 'The saved graph supplies connectivity only. Explicitly map every component, ordered pins, values, RMS source magnitudes/phases, and any exclusions; frequency limits and scale are also explicit.';
  $('#graph-ac-sweep-feedback').className = 'dialog-feedback';
  $('#graph-ac-sweep-result').classList.add('hidden');
  lastGraphAcSweepResult = null;
  $('#graph-ac-sweep-dialog').showModal();
}
function renderGraphAcSweepNode() {
  if (!lastGraphAcSweepResult) return;
  const {outputs, netMap} = lastGraphAcSweepResult;
  const node = $('#graph-ac-sweep-node').value;
  const solverNode = String(netMap[node]);
  const frequencies = outputs.frequency_points_hz;
  const magnitudes = outputs.node_voltage_magnitude_traces_v[solverNode];
  const phases = outputs.node_voltage_phase_traces_deg[solverNode];
  drawAcTrace($('#graph-ac-sweep-magnitude-canvas'), frequencies, magnitudes, outputs.scale,
              'V RMS', `${node} magnitude`, '#b4f077');
  drawAcTrace($('#graph-ac-sweep-phase-canvas'), frequencies, phases, outputs.scale,
              'deg', `${node} phase (wrapped)`, '#78c7ff');
  const rows = frequencies.slice(0, 100).map((frequency, index) =>
    `<tr><td>${formatFrequency(frequency)}</td><td>${Number(magnitudes[index]).toPrecision(6)} V</td><td>${Number(phases[index]).toPrecision(6)}°</td></tr>`).join('');
  $('#graph-ac-sweep-table').innerHTML = `<p class="waveform-table-note">${frequencies.length > 100 ? `Showing first 100 of ${frequencies.length} points; ` : ''}full phasor traces are saved in the report.</p><table><thead><tr><th>Frequency</th><th>${escapeHtml(node)} magnitude</th><th>${escapeHtml(node)} phase</th></tr></thead><tbody>${rows}</tbody></table>`;
}
async function runGraphAcSweep(event) {
  event.preventDefault();
  const feedback = $('#graph-ac-sweep-feedback');
  const button = $('#graph-ac-sweep-form button[type="submit"]');
  button.disabled = true;
  $('#graph-ac-sweep-result').classList.add('hidden');
  lastGraphAcSweepResult = null;
  try {
    const payload = JSON.parse($('#graph-ac-sweep-input').value);
    const projectId = currentProject.project_id;
    const report = await (await request(`/api/projects/${encodeURIComponent(projectId)}/simulations/ac-sweep/from-model`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)
    })).json();
    const outputs = report.calculation.outputs;
    const netMap = report.mapping_report.net_name_to_solver_node;
    const nodes = Object.entries(netMap).filter(([, solverNode]) => String(solverNode) !== '0' && outputs.node_voltage_magnitude_traces_v[String(solverNode)]);
    if (!nodes.length) throw new Error('No non-ground AC node traces were returned.');
    const picker = $('#graph-ac-sweep-node'); picker.replaceChildren();
    nodes.forEach(([name]) => { const option = document.createElement('option'); option.value = name; option.textContent = name; picker.append(option); });
    lastGraphAcSweepResult = {outputs, netMap};
    picker.value = nodes.some(([name]) => name === 'OUT') ? 'OUT' : nodes[0][0];
    renderGraphAcSweepNode(); $('#graph-ac-sweep-result').classList.remove('hidden');
    const coverage = report.component_coverage;
    feedback.textContent = `${outputs.frequency_points_hz.length} frequency points · ${outputs.element_count} elements · mapped ${coverage.mapped}; explicitly excluded ${coverage.excluded} of ${coverage.total}. Maximum normalized residual ${Number(outputs.maximum_normalized_equation_residual).toExponential(3)}. Saved ${report.artifact}; model and mapping hashes are recorded.`;
    feedback.className = coverage.has_exclusions ? 'dialog-feedback' : 'dialog-feedback ok';
    addSimulationArtifact(projectId, report);
    await loadProjects();
    currentProject = projectCache.find((project) => project.project_id === projectId) || currentProject;
    await loadSimulationStatus(projectId);
  } catch (error) {
    feedback.textContent = error instanceof SyntaxError ? `Invalid JSON: ${error.message}` : error.message;
    feedback.className = 'dialog-feedback error';
  } finally {
    button.disabled = false;
  }
}
const DIGITAL_TRUTH_EXAMPLE = {
  inputs: ['A', 'B'],
  outputs: ['SUM', 'CARRY'],
  gates: [
    {type: 'xor', inputs: ['A', 'B'], output: 'SUM'},
    {type: 'and', inputs: ['A', 'B'], output: 'CARRY'}
  ]
};
function openDigitalTruthTableDialog() {
  const editor = $('#digital-truth-netlist');
  if (!editor.value.trim()) editor.value = JSON.stringify(DIGITAL_TRUTH_EXAMPLE, null, 2);
  $('#digital-truth-feedback').textContent = 'Truth-table evaluation covers every primary-input combination in the explicit netlist.';
  $('#digital-truth-feedback').className = 'dialog-feedback';
  $('#digital-truth-result').classList.add('hidden');
  $('#digital-truth-dialog').showModal();
}
function renderDigitalTruthTable(outputs) {
  const rows = outputs.truth_table || [];
  const inputNames = outputs.primary_inputs || [];
  const outputNames = outputs.primary_outputs || [];
  if (!rows.length || !inputNames.length || !outputNames.length) throw new Error('The native engine returned an empty truth table.');
  const headers = [...inputNames, ...outputNames].map((name) => `<th>${escapeHtml(name)}</th>`).join('');
  const body = rows.map((row) => `<tr>${inputNames.map((name) => `<td>${Number(row.inputs[name])}</td>`).join('')}${outputNames.map((name) => `<td>${Number(row.outputs[name])}</td>`).join('')}</tr>`).join('');
  $('#digital-truth-table').innerHTML = `<p class="waveform-table-note">${rows.length} input combinations · ${outputs.gate_count} gates · outputs are ideal Boolean values.</p><table><thead><tr>${headers}</tr></thead><tbody>${body}</tbody></table>`;
  $('#digital-truth-result').classList.remove('hidden');
}
async function runDigitalTruthTable(event) {
  event.preventDefault();
  const feedback = $('#digital-truth-feedback');
  const button = $('#digital-truth-form button[type="submit"]');
  button.disabled = true;
  try {
    const netlist = JSON.parse($('#digital-truth-netlist').value);
    const report = await (await request(`/api/projects/${encodeURIComponent(currentProject.project_id)}/simulations/digital-truth-table`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({netlist})
    })).json();
    const outputs = report.calculation.outputs;
    renderDigitalTruthTable(outputs);
    feedback.textContent = `${outputs.row_count} rows · ${outputs.gate_count} gates · ${outputs.solver_engine}. Saved ${report.artifact}; input SHA-256 ${report.netlist_sha256.slice(0, 16)}…. Zero-delay logic only.`;
    feedback.className = 'dialog-feedback ok';
    addSimulationArtifact(currentProject.project_id, report);
    await loadSimulationStatus(currentProject.project_id);
  } catch (error) {
    feedback.textContent = error instanceof SyntaxError ? `Invalid JSON: ${error.message}` : error.message;
    feedback.className = 'dialog-feedback error';
  } finally {
    button.disabled = false;
  }
}
const DIGITAL_WAVEFORM_EXAMPLE = {
  inputs: ['CLK'],
  outputs: ['Q0', 'Q1'],
  gates: [
    {type: 'dff', inputs: ['D0', 'CLK'], output: 'Q0', initial_value: 0},
    {type: 'dff', inputs: ['D1', 'CLK'], output: 'Q1', initial_value: 0},
    {type: 'not', inputs: ['Q0'], output: 'D0'},
    {type: 'xor', inputs: ['Q1', 'Q0'], output: 'D1'}
  ],
  samples: [0, 1, 0, 1, 0, 1, 0, 1].map((CLK) => ({CLK})),
  sample_interval_s: 0.5
};
function openDigitalWaveformDialog() {
  const editor = $('#digital-waveform-netlist');
  if (!editor.value.trim()) editor.value = JSON.stringify(DIGITAL_WAVEFORM_EXAMPLE, null, 2);
  $('#digital-waveform-feedback').textContent = 'Inputs and gate connections are explicit; no logic is inferred from component names or the circuit graph.';
  $('#digital-waveform-feedback').className = 'dialog-feedback';
  $('#digital-waveform-result').classList.add('hidden');
  $('#digital-waveform-dialog').showModal();
}
function renderDigitalWaveform(outputs) {
  const frames = outputs.waveform || [];
  const names = outputs.primary_outputs || [];
  if (!frames.length || !names.length) throw new Error('The native engine returned an empty waveform.');
  const palette = ['#b4f077', '#78c7ff', '#ffb76b', '#d58cff', '#ff827a', '#69dbc4', '#f2df6e', '#a8b8ff'];
  const canvas = $('#digital-waveform-canvas');
  const rowHeight = 42, left = 128, right = 1072, top = 28;
  canvas.width = 1100;
  canvas.height = Math.max(210, top + names.length * rowHeight + 55);
  canvas.setAttribute('aria-label', `Digital waveform for outputs ${names.join(', ')}, ${frames.length} samples`);
  const context = canvas.getContext('2d');
  if (!context) throw new Error('Canvas rendering is unavailable in this browser.');
  context.clearRect(0, 0, canvas.width, canvas.height);
  context.fillStyle = '#111613'; context.fillRect(0, 0, canvas.width, canvas.height);
  context.font = '12px "DM Mono", monospace';
  context.textAlign = 'left'; context.textBaseline = 'middle';
  const sampleX = (index) => left + (right - left) * index / Math.max(1, frames.length - 1);
  names.forEach((name, row) => {
    const yTop = top + row * rowHeight;
    const yHigh = yTop + 8, yLow = yTop + 28;
    context.strokeStyle = '#2a322d'; context.lineWidth = 1;
    context.beginPath(); context.moveTo(left, yTop + 37); context.lineTo(right, yTop + 37); context.stroke();
    context.fillStyle = '#d9e2d9'; context.fillText(name, 12, yTop + 15);
    context.fillStyle = '#718074'; context.fillText('1', left - 17, yHigh);
    context.fillText('0', left - 17, yLow);
    context.strokeStyle = palette[row % palette.length]; context.lineWidth = 2;
    context.beginPath();
    frames.forEach((frame, index) => {
      const x = sampleX(index), y = Number(frame.outputs[name]) === 1 ? yHigh : yLow;
      if (index === 0) context.moveTo(x, y);
      else {
        const priorY = Number(frames[index - 1].outputs[name]) === 1 ? yHigh : yLow;
        context.lineTo(x, priorY); context.lineTo(x, y);
      }
    });
    context.stroke();
  });
  const axisY = top + names.length * rowHeight + 10;
  context.strokeStyle = '#627067'; context.fillStyle = '#a4aea5'; context.lineWidth = 1;
  context.textAlign = 'center';
  for (let tick = 0; tick <= 4; tick += 1) {
    const fraction = tick / 4, x = left + (right - left) * fraction;
    const index = Math.round((frames.length - 1) * fraction);
    context.beginPath(); context.moveTo(x, top - 7); context.lineTo(x, axisY); context.stroke();
    context.fillText(`${Number(frames[index].time_s).toPrecision(4)} s`, x, axisY + 20);
  }
  $('#digital-waveform-legend').innerHTML = names.map((name, index) =>
    `<span><i style="background:${palette[index % palette.length]}"></i>${escapeHtml(name)}</span>`).join('');
  const shownFrames = frames.slice(0, 100);
  const headings = names.map((name) => `<th>${escapeHtml(name)}</th>`).join('');
  const rows = shownFrames.map((frame, index) => `<tr><td>${index}</td><td>${Number(frame.time_s).toPrecision(6)}</td>${names.map((name) => `<td>${Number(frame.outputs[name])}</td>`).join('')}</tr>`).join('');
  $('#digital-waveform-table').innerHTML = `<p class="waveform-table-note">${shownFrames.length < frames.length ? `Showing first ${shownFrames.length} of ${frames.length} samples; ` : ''}full trace is saved with the report.</p><table><thead><tr><th>Sample</th><th>Time (s)</th>${headings}</tr></thead><tbody>${rows}</tbody></table>`;
  $('#digital-waveform-result').classList.remove('hidden');
}
async function runDigitalWaveform(event) {
  event.preventDefault();
  const feedback = $('#digital-waveform-feedback');
  const button = $('#digital-waveform-form button[type="submit"]');
  button.disabled = true;
  try {
    const netlist = JSON.parse($('#digital-waveform-netlist').value);
    const report = await (await request(`/api/projects/${encodeURIComponent(currentProject.project_id)}/simulations/digital-waveform`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({netlist})
    })).json();
    const outputs = report.calculation.outputs;
    renderDigitalWaveform(outputs);
    feedback.textContent = `${outputs.waveform.length} samples · ${outputs.gate_count} gates · ${outputs.solver_engine}. Saved ${report.artifact}; input SHA-256 ${report.netlist_sha256.slice(0, 16)}…. Boolean model only; not timing or electrical validation.`;
    feedback.className = 'dialog-feedback ok';
    addSimulationArtifact(currentProject.project_id, report);
    await loadSimulationStatus(currentProject.project_id);
  } catch (error) {
    feedback.textContent = error instanceof SyntaxError ? `Invalid JSON: ${error.message}` : error.message;
    feedback.className = 'dialog-feedback error';
  } finally {
    button.disabled = false;
  }
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
  $('#rc-transient-feedback').textContent = 'Choose RC or RLC explicitly. Enter a complete mapping plus fixed timestep and stop time; all R/L/C/source values, waveforms, ordered pins, capacitor initial voltages, and (for RLC) inductor initial currents must be explicit.';
  $('#rc-transient-feedback').className = 'dialog-feedback';
  $('#rc-transient-result').classList.add('hidden');
  lastTransientResult = null;
  $('#rc-transient-dialog').showModal();
}
function updateTransientTargets() {
  if (!lastTransientResult) return;
  const {outputs, netMap, kind} = lastTransientResult;
  const quantity = $('#rc-transient-quantity');
  const currentTraces = outputs.inductor_current_traces_a || {};
  quantity.querySelector('option[value="inductor-current"]').disabled = kind !== 'rlc' || !Object.keys(currentTraces).length;
  if (quantity.value === 'inductor-current' && quantity.querySelector('option[value="inductor-current"]').disabled) quantity.value = 'node-voltage';
  const picker = $('#rc-transient-node');
  const targetEntries = quantity.value === 'inductor-current'
    ? Object.keys(currentTraces).map((reference) => [reference, reference])
    : Object.entries(netMap).filter(([, node]) => String(node) !== '0' && outputs.node_voltage_traces_v[String(node)]);
  picker.replaceChildren();
  targetEntries.forEach(([name]) => { const option = document.createElement('option'); option.value = name; option.textContent = name; picker.append(option); });
  if (targetEntries.some(([name]) => name === 'OUT')) picker.value = 'OUT';
  renderRcTransientTrace();
}
function renderRcTransientTrace() {
  if (!lastTransientResult) return;
  const {outputs, netMap} = lastTransientResult;
  const name = $('#rc-transient-node').value;
  const quantity = $('#rc-transient-quantity').value;
  const currentTrace = quantity === 'inductor-current';
  const values = currentTrace ? (outputs.inductor_current_traces_a || {})[name]
    : outputs.node_voltage_traces_v[String(netMap[name])];
  const unit = currentTrace ? 'A' : 'V';
  const signalLabel = currentTrace ? `${name} inductor current` : `${name} voltage`;
  const times = outputs.time_points_s;
  if (!times?.length || !values || times.length !== values.length) throw new Error('The native engine returned an invalid transient trace.');
  const canvas = $('#rc-transient-canvas');
  const width = 1100, height = 300, left = 100, right = 1070, top = 26, bottom = 250;
  canvas.width = width; canvas.height = height;
  canvas.setAttribute('aria-label', `${signalLabel} versus time, ${times.length} samples`);
  const context = canvas.getContext('2d');
  if (!context) throw new Error('Canvas rendering is unavailable in this browser.');
  context.clearRect(0, 0, width, height); context.fillStyle = '#111613'; context.fillRect(0, 0, width, height);
  let low = Math.min(...values), high = Math.max(...values);
  if (high - low < 1e-15) { const padding = Math.max(Math.abs(high) * 0.1, 1e-9); low -= padding; high += padding; }
  else { const padding = (high - low) * 0.08; low -= padding; high += padding; }
  const xAt = (index) => left + (right - left) * index / Math.max(1, times.length - 1);
  const yAt = (value) => bottom - (value - low) * (bottom - top) / (high - low);
  context.font = '11px "DM Mono", monospace'; context.textBaseline = 'middle';
  context.strokeStyle = '#2a322d'; context.fillStyle = '#94a096'; context.textAlign = 'right';
  for (let tick = 0; tick <= 4; tick += 1) {
    const fraction = tick / 4, y = top + (bottom - top) * fraction;
    context.beginPath(); context.moveTo(left, y); context.lineTo(right, y); context.stroke();
    context.fillText(`${(high - (high - low) * fraction).toPrecision(3)} ${unit}`, left - 9, y);
  }
  context.strokeStyle = '#627067'; context.fillStyle = '#a4aea5'; context.textAlign = 'center';
  for (let tick = 0; tick <= 4; tick += 1) {
    const index = Math.round((times.length - 1) * tick / 4), x = xAt(index);
    context.beginPath(); context.moveTo(x, top); context.lineTo(x, bottom); context.stroke();
    context.fillText(`${Number(times[index]).toPrecision(4)} s`, x, bottom + 20);
  }
  context.strokeStyle = '#b4f077'; context.lineWidth = 2; context.beginPath();
  values.forEach((value, index) => { if (index === 0) context.moveTo(xAt(index), yAt(value)); else context.lineTo(xAt(index), yAt(value)); });
  context.stroke(); context.fillStyle = '#b9c3ba'; context.textAlign = 'left'; context.fillText(`${signalLabel} · backward Euler`, left, 12);
  const rows = times.slice(0, 100).map((time, index) => `<tr><td>${index}</td><td>${Number(time).toPrecision(6)} s</td><td>${Number(values[index]).toPrecision(7)} ${unit}</td></tr>`).join('');
  $('#rc-transient-table').innerHTML = `<p class="waveform-table-note">${times.length > 100 ? `Showing first 100 of ${times.length} samples; ` : ''}full traces are saved in the report.</p><table><thead><tr><th>Sample</th><th>Time</th><th>${escapeHtml(signalLabel)}</th></tr></thead><tbody>${rows}</tbody></table>`;
}
async function runGraphTransient(event) {
  event.preventDefault();
  const feedback = $('#rc-transient-feedback');
  const button = $('#rc-transient-form button[type="submit"]');
  button.disabled = true;
  $('#rc-transient-result').classList.add('hidden');
  lastTransientResult = null;
  try {
    const mapping = JSON.parse($('#rc-transient-mapping').value);
    const projectId = currentProject.project_id;
    const kind = $('#rc-transient-kind').value;
    if (!['rc', 'rlc'].includes(kind)) throw new Error('Select either RC or RLC before running.');
    const payload = {mapping,
      time_step:{value:optionalNumber('rc-transient-step'), unit:'s'},
      stop_time:{value:optionalNumber('rc-transient-stop'), unit:'s'}};
    const response = await fetch(`/api/projects/${encodeURIComponent(projectId)}/simulations/transient-${kind}/from-model`, {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)
    });
    const report = await response.json();
    if (!response.ok) throw new Error(report.error || `RC transient solve failed (${response.status})`);
    const out = report.calculation.outputs;
    const netMap = report.mapping_report.net_name_to_solver_node;
    const traceNodes = Object.entries(netMap).filter(([, node]) => String(node) !== '0' && out.node_voltage_traces_v[String(node)]);
    if (!traceNodes.length) throw new Error('No non-ground node traces were returned.');
    lastTransientResult = {outputs: out, netMap, kind};
    $('#rc-transient-quantity').value = 'node-voltage';
    updateTransientTargets();
    $('#rc-transient-result').classList.remove('hidden');
    const finalNodes = Object.entries(netMap).map(([name,node]) => `${name}: ${Number(out.node_voltage_traces_v[node].at(-1)).toPrecision(7)} V`).join('\n');
    const finalCaps = Object.entries(out.capacitor_voltage_traces_v).map(([ref,trace]) => `${ref}: ${Number(trace.at(-1)).toPrecision(7)} V`).join('\n');
    const finalInductors = Object.entries(out.inductor_current_traces_a || {}).map(([ref,trace]) => `${ref}: ${Number(trace.at(-1)).toPrecision(7)} A`).join('\n');
    const coverage = report.component_coverage;
    feedback.textContent = `Backward Euler linear-${kind.toUpperCase()} time-domain run · ${out.sample_count} samples through ${Number(out.stop_time.value).toPrecision(6)} s.\nFinal node voltages:\n${finalNodes}\nFinal capacitor voltages:\n${finalCaps || 'none'}${kind === 'rlc' ? `\nFinal inductor currents:\n${finalInductors || 'none'}` : ''}\nComponents mapped: ${coverage.mapped}; explicitly excluded: ${coverage.excluded} of ${coverage.total}.\n${report.calculation.warnings.join(' ')}\nModel SHA-256 ${report.circuit_model_sha256.slice(0,16)}…; mapping SHA-256 ${report.mapping_sha256.slice(0,16)}…; full traces saved in ${report.artifact}.`;
    feedback.className = coverage.has_exclusions ? 'dialog-feedback' : 'dialog-feedback ok';
    await loadProjects();
    currentProject = projectCache.find((project) => project.project_id === projectId) || currentProject;
    await loadSimulationStatus(projectId);
    addSimulationArtifact(projectId, report);
  } catch (error) {
    feedback.textContent = error.message; feedback.className = 'dialog-feedback error';
  } finally {
    button.disabled = false;
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

function renderCircuitConnectivity() {
  const status = $('#circuit-map-status');
  const host = $('#circuit-map');
  try {
    const model = JSON.parse($('#circuit-json').value);
    if (!Array.isArray(model.components) || !Array.isArray(model.nets)) throw new Error('Model must contain component and net arrays.');
    const components = model.components;
    const nets = model.nets;
    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg');
    const componentRows = new Map();
    let yCursor = 38;
    components.forEach((component) => {
      const pins = Array.isArray(component.pins) ? component.pins : [];
      const height = Math.max(66, 42 + pins.length * 22);
      const pinY = new Map(pins.map((pin, index) => [String(pin.number), yCursor + 55 + index * 22]));
      componentRows.set(component.reference, {y: yCursor, height, pins, pinY});
      yCursor += height + 20;
    });
    const netY = new Map(nets.map((net, index) => [net.name, 52 + index * 72]));
    const mapHeight = Math.max(yCursor + 10, 90 + nets.length * 72);
    const width = 1160;
    svg.setAttribute('viewBox', `0 0 ${width} ${mapHeight}`);
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', `Connectivity map with ${components.length} components and ${nets.length} nets`);
    svg.setAttribute('class', 'circuit-map-svg');
    const addText = (parent, x, y, text, className) => {
      const element = document.createElementNS(ns, 'text');
      element.setAttribute('x', x); element.setAttribute('y', y); element.setAttribute('class', className);
      element.textContent = String(text ?? ''); parent.append(element); return element;
    };
    const wireLayer = document.createElementNS(ns, 'g'); wireLayer.setAttribute('class', 'circuit-map-wires');
    let endpointCount = 0;
    nets.forEach((net, netIndex) => {
      (Array.isArray(net.terminals) ? net.terminals : []).forEach((terminal) => {
        const row = componentRows.get(terminal.reference);
        if (!row || !netY.has(net.name)) return;
        const pin = String(terminal.pin);
        const startY = row.pinY.get(pin) ?? (row.y + row.height / 2);
        const endY = netY.get(net.name);
        const path = document.createElementNS(ns, 'path');
        path.setAttribute('d', `M 260 ${startY} C 450 ${startY}, 650 ${endY}, 870 ${endY}`);
        path.setAttribute('class', 'circuit-map-wire');
        path.dataset.reference = terminal.reference;
        path.dataset.pin = pin;
        path.dataset.net = net.name;
        path.setAttribute('stroke', ['#b4f077','#78c7ff','#ffbd73','#c5a2ff','#68d5c7'][netIndex % 5]);
        const title = document.createElementNS(ns, 'title'); title.textContent = `${terminal.reference} pin ${pin} → ${net.name}`; path.append(title);
        wireLayer.append(path); endpointCount += 1;
      });
    });
    svg.append(wireLayer);
    const componentLayer = document.createElementNS(ns, 'g');
    components.forEach((component) => {
      const row = componentRows.get(component.reference);
      const group = document.createElementNS(ns, 'g');
      group.setAttribute('class', 'circuit-map-component'); group.dataset.reference = component.reference;
      const rect = document.createElementNS(ns, 'rect');
      rect.setAttribute('x', '30'); rect.setAttribute('y', row.y); rect.setAttribute('width', '230'); rect.setAttribute('height', row.height); rect.setAttribute('rx', '7');
      group.append(rect);
      addText(group, 46, row.y + 24, component.reference, 'circuit-map-reference');
      addText(group, 46, row.y + 42, `${row.pins.length} declared pins`, 'circuit-map-subtitle');
      row.pins.forEach((pin, index) => {
        const py = row.y + 55 + index * 22;
        addText(group, 48, py, `${pin.number ?? '?'} · ${pin.name ?? 'unnamed'}`, 'circuit-map-pin');
      });
      componentLayer.append(group);
    });
    nets.forEach((net) => {
      const y = netY.get(net.name);
      const group = document.createElementNS(ns, 'g');
      group.setAttribute('class', 'circuit-map-net'); group.dataset.net = net.name;
      const rect = document.createElementNS(ns, 'rect');
      rect.setAttribute('x', '870'); rect.setAttribute('y', String(y - 19)); rect.setAttribute('width', '255'); rect.setAttribute('height', '38'); rect.setAttribute('rx', '7');
      group.append(rect);
      addText(group, 884, y + 4, net.name, 'circuit-map-net-name');
      addText(group, 1110, y + 4, `${Array.isArray(net.terminals) ? net.terminals.length : 0}`, 'circuit-map-count');
      componentLayer.append(group);
    });
    svg.append(componentLayer);
    host.replaceChildren(svg);
    status.textContent = `Graph-only view · ${components.length} components · ${nets.length} nets · ${endpointCount} resolvable links. Click a component or net to highlight its connections. No electrical behavior or symbol geometry is represented.`;
    svg.addEventListener('click', (event) => {
      const target = event.target.closest('[data-reference],[data-net]');
      if (!target) return;
      const reference = target.dataset.reference;
      const netName = target.dataset.net;
      svg.querySelectorAll('.circuit-map-wire').forEach((wire) => {
        const active = reference ? wire.dataset.reference === reference : wire.dataset.net === netName;
        wire.classList.toggle('is-dimmed', !active);
        wire.classList.toggle('is-selected', active);
      });
      svg.querySelectorAll('.circuit-map-component,.circuit-map-net').forEach((node) => {
        const active = reference ? node.dataset.reference === reference : node.dataset.net === netName;
        node.classList.toggle('is-selected', active);
        node.classList.toggle('is-dimmed', !active);
      });
      status.textContent = reference ? `Selected ${reference}; highlighted all declared pin-to-net connections.` : `Selected net ${netName}; highlighted its declared component endpoints.`;
    });
  } catch (error) {
    host.replaceChildren();
    status.textContent = error instanceof SyntaxError ? `Connectivity preview unavailable: invalid JSON (${error.message})` : `Connectivity preview unavailable: ${error.message}`;
  }
}
function updateCircuitEditorControls() {
  $('#circuit-undo').disabled = circuitUndoStack.length === 0;
  $('#circuit-redo').disabled = circuitRedoStack.length === 0;
  $('#circuit-restore-draft').hidden = !circuitDraftAvailable;
  $('#circuit-discard-draft').hidden = !circuitDraftAvailable;
}
function queueCircuitDraftSave() {
  clearTimeout(circuitDraftTimer);
  $('#circuit-draft-status').textContent = 'Unsaved edits · browser-local autosave pending…';
  circuitDraftTimer = setTimeout(() => {
    try {
      if (!circuitDraftKey) return;
      localStorage.setItem(circuitDraftKey, JSON.stringify({version: 1, base_text: circuitBaseText, text: $('#circuit-json').value, updated_at: new Date().toISOString()}));
      circuitDraftAvailable = true;
      $('#circuit-draft-status').textContent = 'Draft autosaved in this browser only; it is not on the server.';
      updateCircuitEditorControls();
    } catch (_) {
      $('#circuit-draft-status').textContent = 'Browser storage unavailable; edits remain only in this open dialog.';
    }
  }, 450);
}
function handleCircuitEditorInput() {
  if ($('#circuit-map-details').open) {
    clearTimeout(circuitMapTimer);
    circuitMapTimer = setTimeout(renderCircuitConnectivity, 250);
  }
  if (!circuitUndoGroupTimer) {
    circuitUndoStack.push(circuitLastCommittedText);
    if (circuitUndoStack.length > 50) circuitUndoStack.shift();
    circuitRedoStack = [];
    updateCircuitEditorControls();
  }
  clearTimeout(circuitUndoGroupTimer);
  circuitUndoGroupTimer = setTimeout(() => {
    circuitLastCommittedText = $('#circuit-json').value;
    circuitUndoGroupTimer = null;
  }, 700);
  queueCircuitDraftSave();
}
function undoCircuitEdit() {
  if (!circuitUndoStack.length) return;
  clearTimeout(circuitUndoGroupTimer); circuitUndoGroupTimer = null;
  circuitRedoStack.push($('#circuit-json').value);
  $('#circuit-json').value = circuitUndoStack.pop();
  circuitLastCommittedText = $('#circuit-json').value;
  updateCircuitEditorControls(); queueCircuitDraftSave();
}
function redoCircuitEdit() {
  if (!circuitRedoStack.length) return;
  clearTimeout(circuitUndoGroupTimer); circuitUndoGroupTimer = null;
  circuitUndoStack.push($('#circuit-json').value);
  if (circuitUndoStack.length > 50) circuitUndoStack.shift();
  $('#circuit-json').value = circuitRedoStack.pop();
  circuitLastCommittedText = $('#circuit-json').value;
  updateCircuitEditorControls(); queueCircuitDraftSave();
}
function restoreCircuitDraft() {
  try {
    const draft = JSON.parse(localStorage.getItem(circuitDraftKey) || 'null');
    if (!draft || draft.version !== 1 || typeof draft.text !== 'string') throw new Error('Invalid local draft');
    if (draft.base_text !== circuitBaseText && !window.confirm('This browser draft was based on a different saved circuit revision. Restoring it may replace newer model edits in the editor. Continue?')) return;
    circuitUndoStack.push($('#circuit-json').value);
    $('#circuit-json').value = draft.text;
    circuitLastCommittedText = draft.text;
    circuitRedoStack = [];
    circuitDraftAvailable = false;
    $('#circuit-draft-status').textContent = 'Restored the browser-local draft. Save revision to update the project on the server.';
    updateCircuitEditorControls();
  } catch (_) {
    $('#circuit-draft-status').textContent = 'Could not restore the browser-local draft.';
  }
}
function discardCircuitDraft() {
  clearTimeout(circuitDraftTimer); circuitDraftTimer = null;
  try { if (circuitDraftKey) localStorage.removeItem(circuitDraftKey); } catch (_) {}
  circuitDraftAvailable = false;
  $('#circuit-draft-status').textContent = 'Saved browser draft discarded; current editor text is unchanged.';
  updateCircuitEditorControls();
}
async function openCircuitEditor() {
  if (!currentProject) return;
  try {
    const projectId = currentProject.project_id;
    const data = await (await request(`/api/projects/${encodeURIComponent(projectId)}/circuit`)).json();
    const baseText = JSON.stringify(data.model, null, 2);
    circuitBaseText = baseText;
    $('#circuit-json').value = baseText;
    circuitUndoStack = []; circuitRedoStack = []; circuitLastCommittedText = baseText;
    clearTimeout(circuitUndoGroupTimer); clearTimeout(circuitDraftTimer);
    circuitUndoGroupTimer = null; circuitDraftTimer = null;
    circuitDraftKey = `circuitmind:circuit-draft:${projectId}`;
    try {
      const draft = JSON.parse(localStorage.getItem(circuitDraftKey) || 'null');
      circuitDraftAvailable = Boolean(draft && draft.version === 1 && typeof draft.text === 'string' && draft.text !== baseText);
      circuitDraftBaseMatches = draft?.base_text === baseText;
    } catch (_) { circuitDraftAvailable = false; circuitDraftBaseMatches = true; }
    $('#circuit-draft-status').textContent = circuitDraftAvailable ? (circuitDraftBaseMatches ? 'An unsaved browser-local draft is available to restore.' : 'A draft from an older saved revision is available; restore will ask for confirmation.') : 'Edits autosave to this browser only until you save a server revision.';
    updateCircuitEditorControls();
    circuitFeedback('Graph checks do not establish electrical correctness.');
    $('#circuit-dialog').showModal();
    if ($('#circuit-map-details').open) renderCircuitConnectivity();
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

async function loadAgentProviderConfiguration() {
  try {
    const provider = (await (await request('/api/agent/providers')).json()).provider;
    if (provider.status === 'not-configured') {
      $('#agent-provider-status').textContent = 'No local provider configured. Set CIRCUITMIND_AGENT_PROVIDER on the server to opt in.';
    } else if (provider.status === 'configured') {
      $('#agent-provider-status').textContent = `${provider.provider} · ${provider.model || 'no model selected'} · API key ${provider.api_key_configured ? 'configured' : 'not configured'} · no probe run`;
    } else {
      $('#agent-provider-status').textContent = `Configuration needs attention: ${provider.error}`;
    }
  } catch (error) { $('#agent-provider-status').textContent = `Provider configuration check failed: ${error.message}`; }
}
async function probeAgentProvider() {
  const button = $('#probe-agent-provider'); button.disabled = true;
  $('#agent-provider-result').textContent = 'Probing configured local model-list endpoint…';
  try {
    const result = await (await request('/api/agent/providers/probe', {
      method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'
    })).json();
    if (result.status === 'available') {
      const models = result.models.join(', ') || 'No models reported';
      $('#agent-provider-result').textContent = `${result.provider} available · ${models} · generation not probed`;
    } else {
      $('#agent-provider-result').textContent = `Provider status: ${result.status}${result.http_status ? ` (HTTP ${result.http_status})` : ''}${result.error ? ` · ${result.error}` : ''}`;
    }
  } catch (error) { $('#agent-provider-result').textContent = error.message; }
  finally { button.disabled = false; }
}
async function loadTools() {
  loadAgentProviderConfiguration();
  try {
    const data = await (await request('/api/integrations')).json();
    const tools = data.integrations || [];
    const found = tools.filter((tool) => tool.status === 'detected').length;
    $('#integration-summary').innerHTML = `<span class="status-dot"></span> ${found} of ${tools.length} toolchains detected · probe only`;
    $('#tools-list').innerHTML = tools.map((tool) => `<article class="tool-row"><div class="project-glyph">⌘</div><div class="tool-info"><b>${escapeHtml(tool.name)}</b><small>${escapeHtml(tool.version || tool.detail)}${tool.executable ? ` · ${escapeHtml(tool.executable)}` : ''}</small></div><span class="tool-status ${escapeHtml(tool.status)}">${escapeHtml(tool.status)}</span></article>`).join('');
  } catch (error) { $('#integration-summary').textContent = `Toolchain check failed: ${error.message}`; }
}

$('#probe-agent-provider').addEventListener('click', probeAgentProvider);
$('#agent-plan-form').addEventListener('submit', generateAgentPlan);
$('#execute-agent-plan').addEventListener('click', executeAgentPlan);
$('#summarize-agent-result').addEventListener('click', summarizeAgentExecution);
$('#load-agent-summaries').addEventListener('click', loadSavedAgentSummaries);
$('#refresh-agent-executions').addEventListener('click', refreshAgentExecutionHistory);
$('#agent-execution-history').addEventListener('change', () => { $('#load-agent-execution').disabled = !$('#agent-execution-history').value; });
$('#load-agent-execution').addEventListener('click', loadSelectedAgentExecution);
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
$('#import-project-archive').addEventListener('click', () => $('#project-archive-file').click());
$('#project-archive-file').addEventListener('change', async (event) => {
  const file = event.target.files?.[0]; event.target.value = '';
  if (!file) return;
  if (!file.name.toLowerCase().endsWith('.zip')) { toast('Choose a project backup ZIP.'); return; }
  if (file.size > 64_000_000) { toast('Backup ZIP exceeds the 64 MB upload limit.'); return; }
  try {
    const response = await fetch('/api/projects/import', {method:'POST', headers:{'Content-Type':'application/zip'}, body:file});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `Import failed (${response.status})`);
    await loadProjects();
    const project = projectCache.find((item) => item.project_id === result.project.project_id);
    if (project) renderResult(project);
    toast('Project backup validated and imported as a new project.');
  } catch (error) { toast(error.message); }
});
$('#dialog-close').addEventListener('click', () => $('#artifact-dialog').close());
$('#artifact-dialog').addEventListener('click', (event) => { if (event.target === $('#artifact-dialog')) $('#artifact-dialog').close(); });
document.querySelectorAll('[data-close]').forEach((button) => button.addEventListener('click', () => $(`#${button.dataset.close}`).close()));
$('#circuit-json').addEventListener('input', handleCircuitEditorInput);
$('#circuit-map-details').addEventListener('toggle', () => { if ($('#circuit-map-details').open) renderCircuitConnectivity(); });
$('#circuit-undo').addEventListener('click', undoCircuitEdit);
$('#circuit-redo').addEventListener('click', redoCircuitEdit);
$('#circuit-restore-draft').addEventListener('click', restoreCircuitDraft);
$('#circuit-discard-draft').addEventListener('click', discardCircuitDraft);
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
$('#dc-sweep-form').addEventListener('submit', runDcSweep);
$('#dc-sweep-node').addEventListener('change', renderDcSweepNode);
$('#graph-dc-sweep-form').addEventListener('submit', runGraphDcSweep);
$('#graph-dc-sweep-node').addEventListener('change', renderGraphDcSweepNode);
$('#graph-mna-form').addEventListener('submit', runGraphMna);
$('#operating-limits-form').addEventListener('submit', runOperatingLimits);
$('#operating-limits-run').addEventListener('change', updateOperatingLimitsHelp);
$('#ac-sweep-form').addEventListener('submit', runAcSweep);
$('#ac-sweep-node').addEventListener('change', renderAcSweepNode);
$('#graph-ac-sweep-form').addEventListener('submit', runGraphAcSweep);
$('#graph-ac-sweep-node').addEventListener('change', renderGraphAcSweepNode);
$('#rc-transient-form').addEventListener('submit', runGraphTransient);
$('#rc-transient-node').addEventListener('change', renderRcTransientTrace);
$('#rc-transient-quantity').addEventListener('change', updateTransientTargets);
$('#digital-truth-form').addEventListener('submit', runDigitalTruthTable);
$('#digital-waveform-form').addEventListener('submit', runDigitalWaveform);
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
    clearTimeout(circuitDraftTimer); clearTimeout(circuitUndoGroupTimer);
    circuitDraftTimer = null; circuitUndoGroupTimer = null;
    try { if (circuitDraftKey) localStorage.removeItem(circuitDraftKey); } catch (_) {}
    circuitDraftAvailable = false;
    $('#circuit-dialog').close();
    await refreshProject(currentProject.project_id);
    const exportState = body.kicad_export?.status || 'updated';
    toast(`Circuit model saved. ${body.snapshots.count} snapshot(s). Netlist: ${exportState}.`);
  } catch (error) { circuitFeedback(`Invalid JSON: ${error.message}`, 'error'); }
});
loadProjects().catch(() => {}); loadTools();
