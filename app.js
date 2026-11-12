const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const state = { user: null, currentView: 'overview', selectedFile: null, cameraStream: null, incidentFilter: 'All', overview: null, model: null };

const escapeHTML = (value = '') => String(value).replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const fmtDate = value => {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
};
const fmtShortDate = value => {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString([], { month: 'short', day: 'numeric' });
};
const severityClass = severity => ({Critical:'severity-critical',High:'severity-high',Medium:'severity-medium',Low:'severity-low'}[severity] || 'severity-medium');
const statusClass = status => ({'New':'status-new','Under Review':'status-review','Verified':'status-verified','False Positive':'status-false','Resolved':'status-resolved'}[status] || 'status-new');

async function api(path, options = {}) {
  const response = await fetch(path, { credentials: 'same-origin', ...options });
  const contentType = response.headers.get('content-type') || '';
  const payload = contentType.includes('application/json') ? await response.json() : await response.text();
  if (!response.ok) {
    const error = typeof payload === 'object' ? (payload.detail || payload.message || `Request failed (${response.status})`) : payload;
    const err = new Error(error);
    err.status = response.status;
    if (response.status === 401 && !path.includes('/api/auth/')) showLogin();
    throw err;
  }
  return payload;
}
function toast(message, type = 'success') {
  const item = document.createElement('div');
  item.className = `toast ${type === 'error' ? 'error' : ''}`;
  item.textContent = message;
  $('#toastStack').append(item);
  setTimeout(() => item.remove(), 3600);
}
function showLogin() {
  $('#appShell').classList.add('hidden');
  $('#loginScreen').classList.remove('hidden');
  state.user = null;
}
function showApp(username) {
  state.user = username || 'Administrator';
  $('#currentUser').textContent = state.user;
  $('#settingsUser').textContent = state.user;
  $('#loginScreen').classList.add('hidden');
  $('#appShell').classList.remove('hidden');
  navigate('overview');
  refreshAll();
}
function showSplashThen(callback) {
  const splash = $('#splash');
  const finish = () => { splash.classList.add('done'); setTimeout(callback, 550); };
  window.setTimeout(finish, 2100);
  window.setTimeout(() => splash.remove(), 3100);
}

function navigate(view) {
  if (!$(`#view-${view}`)) view = 'overview';
  state.currentView = view;
  $$('.page-view').forEach(el => el.classList.toggle('active', el.id === `view-${view}`));
  $$('.nav-item[data-view]').forEach(el => el.classList.toggle('active', el.dataset.view === view));
  $('#breadcrumbCurrent').textContent = ({overview:'Overview',scanner:'AI Scanner',incidents:'Incident register',alerts:'Alerts center',analytics:'Analytics','model':'AI Model Center',settings:'Settings'})[view] || 'Overview';
  $('#sidebar').classList.remove('open');
  if (view === 'overview') loadOverview();
  if (view === 'scanner') { loadModelStatus(); loadScanHistory(); }
  if (view === 'incidents') loadIncidents();
  if (view === 'alerts') loadAlerts();
  if (view === 'analytics') loadAnalytics();
  if (view === 'model') loadModelStatus();
}

async function refreshAll() {
  await Promise.allSettled([loadOverview(), loadModelStatus(), loadIncidents(), loadAlerts()]);
  const me = await api('/api/auth/me').catch(() => null);
  if (me?.username) state.user = me.username;
  $('#currentUser').textContent = state.user || 'Administrator';
}

async function loadOverview() {
  try {
    const [data, health] = await Promise.all([api('/api/overview'), api('/api/health')]);
    state.overview = data;
    state.model = data.model;
    $('#metricScans').textContent = data.total_scans;
    $('#metricScansWeek').textContent = `${data.scans_last_7_days} in the last 7 days`;
    $('#metricIncidents').textContent = data.open_incidents;
    $('#metricCritical').textContent = data.critical_open;
    $('#metricAlerts').textContent = data.pending_alerts;
    $('#navIncidentCount').textContent = data.open_incidents;
    $('#navAlertCount').textContent = data.pending_alerts;
    $('#databaseStatus').textContent = health.database === 'connected' ? 'Connected' : 'Unavailable';
    $('#inferenceStatus').textContent = data.model.ready ? 'Ready' : 'Not configured';
    $('#inferenceStatus').className = data.model.ready ? 'check-ok' : 'check-warn';
    $('#sidebarModelStatus').textContent = data.model.ready ? 'AI model ready' : 'AI model not configured';
    $('#sidebarStatusDot').style.background = data.model.ready ? 'var(--green)' : 'var(--amber)';
    $('#healthScore').textContent = data.model.ready ? 'READY' : '—';
    $('#healthScore').style.fontSize = data.model.ready ? '13px' : '20px';
    $('#healthRing').style.background = data.model.ready ? 'conic-gradient(var(--green) 0 100%, #1b3049 0)' : 'conic-gradient(var(--amber) 0 22%, #1b3049 22% 100%)';
    $('#healthTitle').textContent = data.model.ready ? 'Inference engine ready' : 'AI model not configured';
    $('#healthDescription').textContent = data.model.ready ? 'Configured model can process supported images.' : 'Connect trained weights to enable inference.';
    $('#modelNotice').classList.toggle('hidden', data.model.ready);
    renderRecent(data.latest_incidents || []);
  } catch (error) { toast(error.message, 'error'); }
}

function renderRecent(items) {
  const host = $('#recentIncidents');
  if (!items.length) {
    host.innerHTML = '<div class="empty-state compact"><span>▣</span><b>No incidents yet</b><p>Scans with configured hazard detections will appear here.</p></div>';
    return;
  }
  host.innerHTML = items.map(item => `<div class="recent-item"><div class="recent-thumb">⌁</div><div class="recent-info"><b>${escapeHTML(item.category)}</b><small>${escapeHTML(item.id.slice(0,8))} · ${escapeHTML(fmtDate(item.created_at))}</small></div><span class="severity-pill ${severityClass(item.severity)}">${escapeHTML(item.severity)}</span></div>`).join('');
}

async function loadModelStatus() {
  try {
    const model = await api('/api/model/status');
    state.model = model;
    const readyText = model.ready ? 'Model ready' : 'Model not configured';
    $('#scannerModelChip').innerHTML = `<i></i> ${readyText}`;
    $('#scannerModelChip').style.color = model.ready ? 'var(--green)' : 'var(--amber)';
    $('#modelStateChip').innerHTML = `<i></i> ${readyText}`;
    $('#modelName').textContent = model.name;
    $('#modelVersion').textContent = `VERSION ${model.version}`;
    $('#modelMessage').textContent = model.message;
    $('#modelStateBadge').textContent = model.ready ? 'ACTIVE' : 'NOT CONFIGURED';
    $('#modelStateBadge').style.color = model.ready ? 'var(--green)' : 'var(--amber)';
    const rows = [
      ['Weights path', model.weights_path], ['Inference device', model.device],
      ['Confidence threshold', `${Math.round(model.confidence_threshold * 100)}%`],
      ['Hazard allow-list', (model.configured_hazard_labels || []).join(', ') || 'Not set'],
      ['Model classes', (model.model_classes || []).join(', ') || 'Unavailable']
    ];
    $('#modelConfig').innerHTML = rows.map(([label,value]) => `<div class="config-row"><span>${escapeHTML(label)}</span><b>${escapeHTML(value)}</b></div>`).join('');
  } catch (error) { toast(error.message, 'error'); }
}

let allIncidents = [];
async function loadIncidents() {
  try {
    allIncidents = await api('/api/incidents?limit=300');
    renderIncidents();
  } catch (error) { toast(error.message, 'error'); }
}
function renderIncidents() {
  const filtered = state.incidentFilter === 'All' ? allIncidents : allIncidents.filter(i => i.status === state.incidentFilter);
  $('#incidentCount').textContent = `${filtered.length} record${filtered.length === 1 ? '' : 's'}`;
  $('#navIncidentCount').textContent = allIncidents.filter(i => !['Resolved','False Positive'].includes(i.status)).length;
  const table = $('#incidentTable');
  if (!filtered.length) {
    table.innerHTML = '<tr><td colspan="7"><div class="empty-state compact"><span>▣</span><b>No matching incidents</b><p>AI detections will be recorded here when the model is connected.</p></div></td></tr>';
    return;
  }
  table.innerHTML = filtered.map(item => `<tr>
    <td><span class="incident-id">${escapeHTML(item.id.slice(0,8).toUpperCase())}</span><span class="incident-sub">${escapeHTML(item.original_name || 'Image scan')}</span></td>
    <td>${escapeHTML(item.category)}</td><td><span class="severity-pill ${severityClass(item.severity)}">${escapeHTML(item.severity)}</span></td>
    <td>${Math.round(item.confidence * 100)}%</td><td>${escapeHTML(fmtDate(item.created_at))}</td>
    <td><select class="status-select" data-incident-status="${escapeHTML(item.id)}">${['New','Under Review','Verified','False Positive','Resolved'].map(s => `<option ${s===item.status?'selected':''}>${s}</option>`).join('')}</select></td>
    <td><button class="table-action" data-incident-detail="${escapeHTML(item.id)}">Details</button></td></tr>`).join('');
}

async function loadAlerts() {
  try {
    const alerts = await api('/api/alerts?limit=300');
    $('#navAlertCount').textContent = alerts.filter(a => a.status === 'Pending').length;
    const host = $('#alertsList');
    if (!alerts.length) {
      host.innerHTML = '<div class="empty-state"><span>♧</span><b>All quiet here</b><p>Internal alerts from configured detections will appear here.</p></div>';
      return;
    }
    host.innerHTML = alerts.map(alert => `<article class="alert-card"><div class="alert-symbol">♧</div><div class="alert-main"><b>${escapeHTML(alert.message)}</b><small>${escapeHTML(fmtDate(alert.created_at))} · ${escapeHTML(alert.channel)} notification</small></div><span class="alert-status">${escapeHTML(alert.status)}</span>${alert.status === 'Pending' ? `<button class="btn btn-soft" data-alert-ack="${escapeHTML(alert.id)}">Acknowledge</button>` : ''}</article>`).join('');
  } catch (error) { toast(error.message, 'error'); }
}

async function loadAnalytics() {
  try {
    const data = await api('/api/analytics');
    $('#analyticsTotal').textContent = data.total_incidents;
    $('#analyticsVerified').textContent = data.verified_or_resolved;
    $('#analyticsFalse').textContent = data.false_positives;
    const categories = data.categories || [];
    const max = Math.max(1, ...categories.map(c => c.count));
    $('#categoryChart').innerHTML = categories.length ? categories.map(c => `<div class="bar-row"><span class="bar-label">${escapeHTML(c.category)}</span><div class="bar-track"><div class="bar-fill" style="width:${(c.count/max)*100}%"></div></div><span class="bar-value">${c.count}</span></div>`).join('') : '<div class="empty-state compact">No category data available.</div>';
    const statuses = data.statuses || [];
    const statusTotal = Math.max(1, statuses.reduce((sum,x) => sum+x.count,0));
    $('#statusChart').innerHTML = statuses.length ? statuses.map(s => `<div><div class="status-line"><span>${escapeHTML(s.status)}</span><span>${s.count}</span></div><div class="status-track"><span style="width:${s.count/statusTotal*100}%"></span></div></div>`).join('') : '<div class="empty-state compact">No status data available.</div>';
    const days = data.daily_scans || [];
    const dailyMax = Math.max(1, ...days.map(d => d.count));
    const normalized = [];
    for (let i=13;i>=0;i--) {
      const date = new Date(); date.setDate(date.getDate()-i);
      const key = date.toISOString().slice(0,10);
      normalized.push({day:key,count:days.find(d=>d.day===key)?.count || 0});
    }
    $('#dailyChart').innerHTML = normalized.map(d => `<div class="day-col" title="${d.day}: ${d.count} scans"><div class="day-bar" style="height:${Math.max(3,d.count/dailyMax*100)}%"></div><small>${new Date(`${d.day}T00:00:00`).toLocaleDateString([], {day:'numeric'})}</small></div>`).join('');
  } catch (error) { toast(error.message, 'error'); }
}

async function loadScanHistory() {
  try {
    const scans = await api('/api/scans?limit=8');
    $('#scanHistory').innerHTML = scans.length ? scans.map(scan => `<div class="scan-history-item"><div class="scan-file-icon">▧</div><div><b>${escapeHTML(scan.original_name)}</b><small>${escapeHTML(fmtDate(scan.created_at))} · ${escapeHTML(scan.status)}</small></div><span class="severity-pill ${scan.status==='completed'?'severity-medium':'severity-high'}">${escapeHTML(scan.risk_level)}</span></div>`).join('') : '<div class="empty-state compact">No scans have been submitted.</div>';
  } catch (error) { toast(error.message, 'error'); }
}

function setSelectedFile(file) {
  if (!file) return;
  const allowed = ['image/jpeg','image/png','image/webp'];
  if (!allowed.includes(file.type)) { toast('Please select a JPG, PNG or WEBP image.', 'error'); return; }
  if (file.size > 8 * 1024 * 1024) { toast('Image is larger than the 8 MB limit.', 'error'); return; }
  state.selectedFile = file;
  const url = URL.createObjectURL(file);
  $('#imagePreview').src = url;
  $('#previewLabel').textContent = `${file.name} · ${(file.size/1024/1024).toFixed(2)} MB`;
  $('#uploadPrompt').classList.add('hidden');
  $('#previewWrap').classList.remove('hidden');
  $('#scanBtn').disabled = false;
  $('#scanResult').innerHTML = '<div class="result-empty"><div class="result-graphic"><div class="result-radar"></div><span>⌗</span></div><b>Image ready for review</b><p>Run the scan to submit this image to the configured AI engine.</p></div>';
}
function clearSelectedFile() {
  if ($('#imagePreview').src.startsWith('blob:')) URL.revokeObjectURL($('#imagePreview').src);
  state.selectedFile = null;
  $('#imageInput').value = '';
  $('#imagePreview').removeAttribute('src');
  $('#previewWrap').classList.add('hidden');
  $('#uploadPrompt').classList.remove('hidden');
  $('#scanBtn').disabled = true;
}
async function runScan() {
  if (!state.selectedFile) return;
  const form = new FormData(); form.append('file', state.selectedFile, state.selectedFile.name || 'camera-capture.jpg');
  $('#scanBtn').disabled = true; $('#scanProgress').classList.remove('hidden');
  try {
    const data = await api('/api/scans', { method:'POST', body:form });
    renderScanResult(data);
    await Promise.all([loadScanHistory(), loadOverview(), loadIncidents(), loadAlerts()]);
    toast(data.scan.status === 'completed' ? 'Image analysis completed.' : 'Image saved; AI model needs configuration.', data.scan.status === 'completed' ? 'success' : 'error');
  } catch (error) { toast(error.message, 'error'); }
  finally { $('#scanProgress').classList.add('hidden'); $('#scanBtn').disabled = !state.selectedFile; }
}
function renderScanResult(data) {
  const scan = data.scan;
  const detections = scan.detections || [];
  const imageUrl = scan.annotated_url || scan.image_url;
  let html = `<div class="scan-result-content"><div class="result-summary"><b>${scan.status==='model_unavailable'?'AI model not connected':scan.risk_level==='No configured hazard detected'?'No configured hazards detected':`${detections.length} detection(s) recorded`}</b><p>${escapeHTML(scan.summary)}</p></div><img class="result-image" src="${escapeHTML(imageUrl)}?t=${Date.now()}" alt="${scan.annotated_url?'Annotated scan evidence':'Uploaded scan evidence'}">`;
  if (scan.status === 'model_unavailable') {
    html += `<div class="model-warning"><b>Inference was not performed</b><p>${escapeHTML(data.model?.message || 'Configure the trained model weights and restart the application.')}</p><button class="btn btn-soft" style="margin-top:8px" data-go="model">Open Model Center →</button></div>`;
  } else if (detections.length) {
    html += detections.map(d => `<div class="detection-item"><div><b>${escapeHTML(d.label)}</b><small>AI observation · human verification required</small></div><span class="confidence">${Math.round(d.confidence*100)}%</span></div>`).join('');
    html += `<p class="disclaimer-line">Confidence is a model score, not a guarantee of accuracy. Verify the image and context before action.</p>`;
  } else {
    html += '<p class="disclaimer-line">No configured class was detected above the threshold. This result does not establish that the scene is safe.</p>';
  }
  html += `<div class="disclaimer-line">Scan ${escapeHTML(scan.id.slice(0,8))} · ${escapeHTML(fmtDate(scan.created_at))} · ${escapeHTML(String(scan.inference_ms))} ms</div></div>`;
  $('#scanResult').innerHTML = html;
}

function stopCamera() {
  if (state.cameraStream) state.cameraStream.getTracks().forEach(track => track.stop());
  state.cameraStream = null;
  $('#cameraVideo').srcObject = null;
  $('#cameraWrap').classList.add('hidden');
  $('#cameraBtn').textContent = '▣ Use camera';
}
async function startCamera() {
  if (state.cameraStream) { stopCamera(); return; }
  if (!navigator.mediaDevices?.getUserMedia) { toast('Camera access requires HTTPS or localhost and a supported browser.', 'error'); return; }
  try {
    state.cameraStream = await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:'environment'}},audio:false});
    $('#cameraVideo').srcObject = state.cameraStream;
    $('#cameraWrap').classList.remove('hidden');
    $('#cameraBtn').textContent = 'Camera active';
  } catch (error) { toast(`Camera could not start: ${error.message}`, 'error'); }
}
function captureFrame() {
  const video = $('#cameraVideo');
  if (!video.videoWidth) { toast('Camera is not ready yet.', 'error'); return; }
  const canvas = document.createElement('canvas'); canvas.width = video.videoWidth; canvas.height = video.videoHeight;
  canvas.getContext('2d').drawImage(video,0,0);
  canvas.toBlob(blob => { if (blob) { setSelectedFile(new File([blob], `camera-${Date.now()}.jpg`, {type:'image/jpeg'})); stopCamera(); } }, 'image/jpeg', .9);
}

function openIncidentDetails(id) {
  const item = allIncidents.find(x => x.id === id);
  if (!item) return;
  $('#modalContent').innerHTML = `<div class="eyebrow">INCIDENT RECORD</div><h2 class="modal-detail-title">${escapeHTML(item.category)}</h2><img class="modal-detail-image" src="${escapeHTML(item.image_url)}?t=${Date.now()}" alt="Incident evidence"><div class="modal-meta"><div><small>Incident ID</small><b>${escapeHTML(item.id.slice(0,12))}</b></div><div><small>Severity</small><b>${escapeHTML(item.severity)}</b></div><div><small>Confidence</small><b>${Math.round(item.confidence*100)}%</b></div><div><small>Status</small><b>${escapeHTML(item.status)}</b></div><div><small>Recorded</small><b>${escapeHTML(fmtDate(item.created_at))}</b></div><div><small>Source file</small><b>${escapeHTML(item.original_name || 'Image')}</b></div></div><p style="font-size:11px;color:var(--muted)">${escapeHTML(item.description)}</p><label style="display:block;font-size:10px;margin:14px 0 6px">Update review status</label><select id="modalStatus" class="status-select" style="max-width:100%;width:100%;padding:9px">${['New','Under Review','Verified','False Positive','Resolved'].map(s=>`<option ${s===item.status?'selected':''}>${s}</option>`).join('')}</select><button class="btn btn-primary full" style="margin-top:12px" id="saveModalStatus">Save status</button>`;
  $('#detailModal').classList.remove('hidden');
  $('#saveModalStatus').addEventListener('click', async () => {
    try { await updateIncident(id, $('#modalStatus').value); $('#detailModal').classList.add('hidden'); }
    catch (error) { toast(error.message, 'error'); }
  });
}
async function updateIncident(id, status) {
  await api(`/api/incidents/${encodeURIComponent(id)}`, {method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({status})});
  toast(`Incident marked ${status.toLowerCase()}.`);
  await Promise.all([loadIncidents(), loadOverview(), loadAnalytics(), loadAlerts()]);
}

// Main event wiring
$$('.nav-item[data-view]').forEach(button => button.addEventListener('click', () => navigate(button.dataset.view)));
document.addEventListener('click', event => {
  const go = event.target.closest('[data-go]');
  if (go) navigate(go.dataset.go);
});
$('#mobileMenu').addEventListener('click', () => $('#sidebar').classList.toggle('open'));
$('#themeToggle').addEventListener('click', () => {
  document.body.classList.toggle('light-mode');
  localStorage.setItem('aegis-theme', document.body.classList.contains('light-mode') ? 'light' : 'dark');
});
if (localStorage.getItem('aegis-theme') === 'light') document.body.classList.add('light-mode');
$('#yearNow').textContent = new Date().getFullYear();
$('#todayLabel').textContent = new Date().toLocaleDateString([], {weekday:'long',month:'long',day:'numeric',year:'numeric'});
$('#loginForm').addEventListener('submit', async event => {
  event.preventDefault(); $('#loginError').textContent = '';
  try {
    const result = await api('/api/auth/login', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:$('#loginUsername').value,password:$('#loginPassword').value})});
    $('#loginPassword').value = ''; showApp(result.username);
  } catch (error) { $('#loginError').textContent = error.message; }
});
$('#logoutBtn').addEventListener('click', async () => {
  stopCamera(); await api('/api/auth/logout',{method:'POST'}).catch(()=>{}); showLogin(); toast('You have signed out.');
});
$('#browseBtn').addEventListener('click', () => $('#imageInput').click());
$('#imageInput').addEventListener('change', event => setSelectedFile(event.target.files[0]));
$('#removeImage').addEventListener('click', clearSelectedFile);
$('#scanBtn').addEventListener('click', runScan);
$('#cameraBtn').addEventListener('click', startCamera);
$('#captureBtn').addEventListener('click', captureFrame);
$('#closeCameraBtn').addEventListener('click', stopCamera);
$('#refreshScans').addEventListener('click', loadScanHistory);
$('#refreshAnalytics').addEventListener('click', loadAnalytics);
$('#dropZone').addEventListener('dragover', event => {event.preventDefault();$('#dropZone').classList.add('drag-over');});
$('#dropZone').addEventListener('dragleave', () => $('#dropZone').classList.remove('drag-over'));
$('#dropZone').addEventListener('drop', event => {event.preventDefault();$('#dropZone').classList.remove('drag-over');setSelectedFile(event.dataTransfer.files[0]);});
$('#incidentFilters').addEventListener('click', event => {
  const button = event.target.closest('[data-filter]'); if (!button) return;
  state.incidentFilter = button.dataset.filter;
  $$('.filter-tab').forEach(tab => tab.classList.toggle('active', tab === button));
  renderIncidents();
});
$('#incidentTable').addEventListener('change', async event => {
  const id = event.target.dataset.incidentStatus; if (id) await updateIncident(id,event.target.value).catch(error=>toast(error.message,'error'));
});
$('#incidentTable').addEventListener('click', event => {
  const button = event.target.closest('[data-incident-detail]'); if (button) openIncidentDetails(button.dataset.incidentDetail);
});
$('#alertsList').addEventListener('click', async event => {
  const button = event.target.closest('[data-alert-ack]'); if (!button) return;
  try { await api(`/api/alerts/${encodeURIComponent(button.dataset.alertAck)}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:'Acknowledged'})}); toast('Alert acknowledged.'); await Promise.all([loadAlerts(),loadOverview()]); }
  catch (error) { toast(error.message,'error'); }
});
$('#closeModal').addEventListener('click', () => $('#detailModal').classList.add('hidden'));
$('#detailModal').addEventListener('click', event => {if(event.target.id==='detailModal') $('#detailModal').classList.add('hidden');});
window.addEventListener('beforeunload', stopCamera);

showSplashThen(async () => {
  try {
    const me = await api('/api/auth/me');
    if (me.authenticated) showApp(me.username);
    else showLogin();
  } catch { showLogin(); }
});
