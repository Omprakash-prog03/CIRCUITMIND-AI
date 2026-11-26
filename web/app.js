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
  const artifactButtons = (project.artifacts || []).map((name) => `<button class="artifact-button" data-artifact="${escapeHtml(name)}">↗ &nbsp;${escapeHtml(name)}</button>`).join('');
  $('#view-result').innerHTML = `<div class="result-top"><div class="eyebrow"><span class="eyebrow-line"></span> ENGINEERING INTAKE COMPLETE</div><h1>${escapeHtml(project.name)}</h1><p>${escapeHtml(project.architecture)} architecture · Created ${escapeHtml(new Date(project.created_at).toLocaleString())}</p><div class="result-actions"><a class="primary-button" href="/api/projects/${encodeURIComponent(project.project_id)}/export"><span>Download project ZIP</span><span class="button-arrow">↓</span></a><button class="small-button" id="back-to-brief">← New analysis</button></div></div>
  <div class="status-banner">⚠ &nbsp; Incomplete — this is requirements and feasibility analysis, not a completed electrical design.</div>
  <div class="result-grid"><div><section class="result-panel"><h2>Feasibility findings <span class="optional">${findings.length} ITEMS</span></h2>${findings.map((finding) => `<div class="finding"><span class="finding-icon">!</span><span>${escapeHtml(finding)}</span></div>`).join('')}</section>
  <section class="result-panel"><h2>Supplied component inventory</h2>${inventoryRows ? `<table class="inventory-table"><thead><tr><th>PART / DESCRIPTION</th><th>QTY</th><th>VERIFICATION</th></tr></thead><tbody>${inventoryRows}</tbody></table>` : '<div class="empty-state">No inventory provided.</div>'}</section></div>
  <div><section class="result-panel"><h2>Validation ledger</h2>${statusLines}</section><section class="result-panel"><h2>Generated artifacts <span class="optional">${(project.artifacts||[]).length}</span></h2><div class="artifact-list">${artifactButtons}</div></section></div></div>`;
  $('#view-result').querySelectorAll('[data-artifact]').forEach((button) => button.addEventListener('click', () => openArtifact(project.project_id, button.dataset.artifact)));
  $('#back-to-brief').addEventListener('click', () => showView('new'));
  showView('result');
}

async function openArtifact(projectId, filename) {
  try {
    const artifact = await (await request(`/api/projects/${encodeURIComponent(projectId)}/artifact/${encodeURIComponent(filename)}`)).json();
    $('#artifact-title').textContent = artifact.name; $('#artifact-content').textContent = artifact.content;
    $('#artifact-dialog').showModal();
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
loadProjects().catch(() => {}); loadTools();
