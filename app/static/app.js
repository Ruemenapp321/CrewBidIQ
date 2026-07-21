const $ = id => document.getElementById(id);
const analysisStateKey = 'crewbidiqAnalysisJob';
const analysisSessionKey = 'crewbidiqAnalysisSession';
function readStoredJson(key, fallback = null) { try { return JSON.parse(localStorage.getItem(key) || 'null') ?? fallback; } catch (_) { return fallback; } }
function browserSessionId() {
  let value = localStorage.getItem(analysisSessionKey);
  if (!value) { value = globalThis.crypto?.randomUUID?.() || `session-${Date.now()}-${Math.random().toString(16).slice(2)}`; safeLocalStorageSetItem(analysisSessionKey, value); }
  return value;
}
function isQuotaExceededError(error) {
  return Boolean(error && (error.name === 'QuotaExceededError' || error.code === 22 || error.code === 1014));
}
function safeLocalStorageSetItem(key, value) {
  try { localStorage.setItem(key, value); return true; }
  catch (error) {
    if (isQuotaExceededError(error)) {
      console.warn(`Optional browser state was not saved due to storage quota limits for ${key}.`);
      return false;
    }
    throw error;
  }
}
function safeLocalStorageRemoveItem(key) {
  try { localStorage.removeItem(key); } catch (_) {}
}
let analysisState = readStoredJson(analysisStateKey, {}) || {};
let activeJob = analysisState.job_id || localStorage.getItem('crewbidiqActiveJob');
let latestJob = localStorage.getItem('crewbidiqLatestJob');
const locallyActivePackageId = localStorage.getItem('crewbidiqActivePackage');
let activePackageId = locallyActivePackageId || analysisState.package_id;
let statusRequestController = null;
if (analysisState.package_id && locallyActivePackageId && analysisState.package_id !== locallyActivePackageId) {
  activeJob = null; analysisState = {}; safeLocalStorageRemoveItem('crewbidiqActiveJob'); safeLocalStorageRemoveItem(analysisStateKey);
}
let pollTimer = null;
let pollFailures = 0;
let pollInFlight = false;
let resumeInFlight = false;
let uploadInFlight = false;
let lastConfirmedProgress = Number(analysisState.progress_percent || 0);
let latestStatusCode = analysisState.latest_status_code || null;
const MAX_POLL_RETRIES = 6;
const ACTIVE_ANALYSIS_STATES = new Set(['queued', 'parsing', 'normalizing', 'ranking', 'reconnecting']);
function lightweightAnalysisState(value = {}) {
  const keys = ['job_id', 'package_id', 'filename', 'airline', 'status', 'state', 'current_stage', 'stage_label', 'progress', 'progress_percent', 'message', 'user_message', 'error', 'error_code', 'created_at', 'updated_at', 'last_successful_poll_at', 'retry_count', 'recoverable', 'package_persisted', 'latest_status_code'];
  return Object.fromEntries(keys.filter(key => value[key] !== undefined).map(key => [key, value[key]]));
}
function persistAnalysisState(patch = {}) {
  analysisState = { ...lightweightAnalysisState(analysisState), ...lightweightAnalysisState(patch), package_id: activePackageId || patch.package_id || null, job_id: activeJob || patch.job_id || null };
  safeLocalStorageSetItem(analysisStateKey, JSON.stringify(analysisState));
  updateAnalysisDebug();
}
function clearActiveAnalysis({ clearPackage = false } = {}) {
  clearTimeout(pollTimer); pollTimer = null; statusRequestController?.abort(); statusRequestController = null; pollInFlight = false; resumeInFlight = false; pollFailures = 0;
  safeLocalStorageRemoveItem('crewbidiqActiveJob'); safeLocalStorageRemoveItem(analysisStateKey); activeJob = null; analysisState = {}; lastConfirmedProgress = 0;
  if (clearPackage) { safeLocalStorageRemoveItem('crewbidiqActivePackage'); activePackageId = null; }
  updateAnalysisDebug();
}
function analysisHeaders() { return { Accept: 'application/json', 'X-CrewBidIQ-Session': browserSessionId() }; }
function errorDetail(body, fallback) { const detail = body?.detail || body || {}; return typeof detail === 'string' ? { user_message: detail } : { ...detail, user_message: detail.user_message || fallback }; }
function updateAnalysisDebug() {
  const panel = $('analysisDebugPanel'); if (!panel) return;
  panel.classList.toggle('hidden', window.CREWBIDIQ_ANALYSIS_DEBUG_ENABLED !== true);
  const values = { package: activePackageId || '—', job: activeJob || '—', state: analysisState.state || 'idle', progress: `${lastConfirmedProgress}%`, poll: analysisState.last_successful_poll_at || '—', status: latestStatusCode ?? '—', retries: analysisState.retry_count || pollFailures || 0 };
  Object.entries(values).forEach(([key, value]) => { const target = panel.querySelector(`[data-debug="${key}"]`); if (target) target.textContent = value; });
}
let allResults = [];
let bidSynopsis = null;
let diagnosticPairingId = null;
let airlineTerminology = { generic: { singular: 'Pairing', plural: 'Pairings', recommended: 'Recommended pairings', details: 'Pairing details', view_original: 'View original pairing', analyzed: 'Pairings analyzed' } };

const csv = value => value.split(',').map(x => x.trim().toUpperCase()).filter(Boolean);
const packageStateKeys = ['crewbidiqShortlist', 'crewbidiqComparison', 'crewbidiqPbsPool', 'crewbidiqCommuteAssessments', 'crewbidiqExports'];
function clearPackageDependentState() {
  packageStateKeys.forEach(key => safeLocalStorageRemoveItem(key));
  allResults = []; bidSynopsis = null; diagnosticPairingId = null;
}
function acceptPackagePayload(body) {
  const incoming = body?.package_id || body?.package?.package_id;
  if (!incoming) throw new Error('The server response did not identify its source package.');
  if (activePackageId && incoming !== activePackageId) throw new Error('Results from a replaced bid package were rejected. Reload the active package.');
  const mismatched = (body.results || []).filter(item => item.package_id !== incoming);
  if (mismatched.length) throw new Error('Mixed-package results were rejected.');
  return incoming;
}
const num = id => $(id) && $(id).value !== '' ? Number($(id).value) : null;
function mins(value, fallback) { if (!value) return fallback; const [h, m] = value.split(':').map(Number); return h * 60 + m; }
function profile() {
  return {
    elite_cities: csv($('eliteCities').value), secondary_cities: csv($('secondaryCities').value), small_cities: [], penalty_cities: csv($('penaltyCities').value),
    base_airport: $('baseAirport').value.trim().toUpperCase(), bid_fleets: $('airlineChoice').value === 'american' ? csv($('bidFleets').value) : [], preferred_start_airports: csv($('preferredStartAirports').value), avoid_start_airports: csv($('avoidStartAirports').value),
    preferred_aircraft: csv($('preferredAircraft').value), trip_length_priority: csv($('preferredTripLengths').value), preferred_trip_lengths: csv($('preferredTripLengths').value), max_deadheads: num('maxDeadheads'), max_transfers: num('maxTransfers'),
    max_legs_per_day: num('maxLegsPerDay'), max_first_day_legs: num('maxFirstDayLegs'), max_last_day_legs: num('maxLastDayLegs'), min_layover_hours: num('minLayoverHours'), max_legs_after_redeye: num('maxLegsAfterRedeye'),
    required_days_off: csv($('requiredDaysOff').value), preferred_days_off: csv($('preferredDaysOff').value), holiday_dates: csv($('holidayDates').value), preferred_weekdays: csv($('preferredWeekdays').value),
    earliest_report_minutes: mins($('earliestReport').value, null), latest_release_minutes: mins($('latestRelease').value, null), prefer_weekends_off: $('preferWeekendsOff').checked,
    avoid_holidays: $('avoidHolidays').checked, work_holidays: $('workHolidays').checked, allow_productive_redeye: $('allowMidRotationRedeye').checked, allow_redeye_start: $('allowRedeyeStart').checked,
    avoid_final_redeye: $('avoidFinalRedeye').checked, avoid_reserve: false, prefer_operate: false,
    pay_priority: $('payPriorityField').classList.contains('hidden') ? '' : $('payPriority').value,
    weights: { elite: num('wElite'), secondary: num('wSecondary'), small: num('wSmall'), penalty: num('wPenalty'), aircraft: num('wAircraft'), pure: num('wPure'), transfer: num('wTransfer'), deadhead: num('wDeadhead'), start_preferred: num('wStartPreferred'), start_avoid: num('wStartAvoid'), required_conflict: num('wRequiredConflict'), preferred_conflict: num('wPreferredConflict'), holiday_conflict: num('wHolidayConflict'), early_report: num('wEarlyReport'), late_release: num('wLateRelease') }
  };
}
function applySaved() { try { const p = JSON.parse(localStorage.getItem('crewbidiqProfile') || 'null'); if (!p) return; const map = { eliteCities: p.elite_cities, secondaryCities: p.secondary_cities, penaltyCities: p.penalty_cities, preferredAircraft: p.preferred_aircraft, preferredTripLengths: p.trip_length_priority || p.preferred_trip_lengths, baseAirport: p.base_airport, bidFleets: p.bid_fleets, preferredStartAirports: p.preferred_start_airports, avoidStartAirports: p.avoid_start_airports, requiredDaysOff: p.required_days_off, preferredDaysOff: p.preferred_days_off, holidayDates: p.holiday_dates, preferredWeekdays: p.preferred_weekdays, payPriority: p.pay_priority }; Object.entries(map).forEach(([id, value]) => { if ($(id) && value && (id !== 'payPriority' || Array.from($(id).options).some(option => option.value === value))) $(id).value = Array.isArray(value) ? value.join(',') : value; }); } catch (_) {} }
function setJob(show, status = '', progress = lastConfirmedProgress, message = '') { const confirmed = Math.max(0, Math.min(100, Number(progress) || 0)); $('jobPanel').classList.toggle('hidden', !show); $('jobStatus').textContent = status; $('jobPercent').textContent = `${confirmed}%`; $('progressFill').style.width = `${confirmed}%`; $('jobMessage').textContent = message; updateAnalysisDebug(); }
function showError(message) { $('errorBox').textContent = message; $('errorBox').classList.remove('hidden'); }
function clearError() { $('errorBox').classList.add('hidden'); }
function setLabsContinuation(show) { const link = $('continueLabs'); if (link) link.classList.toggle('hidden', !show); }
function terminology() { const configured = airlineTerminology[$('airlineChoice').value] || airlineTerminology.generic; return { single: configured.singular, plural: configured.plural, title: configured.recommended, details: configured.details, viewOriginal: configured.view_original, analyzed: configured.analyzed }; }
async function loadAirlineTerminology() { try { const response = await fetch('/api/airlines/terminology', { headers: { Accept: 'application/json' } }); if (response.ok) airlineTerminology = await response.json(); } catch (_) {} updateAirlineUI(); if (allResults.length) render(); }
function uploadIsReady() {
  const airline = $('airlineChoice').value;
  if (!airline) return false;
  if (airline === 'southwest') return Boolean($('southwestZip').files[0] || ($('southwestPairingsFile').files[0] && $('southwestLinesFile').files[0]));
  return Boolean($('pdfFile').files[0]);
}
function updateAnalyzeAvailability() {
  const button = $('analyzeBtn');
  if (activeJob && button.textContent === 'Resume analysis') { button.disabled = false; return; }
  if (button.textContent === 'Uploading…') return;
  button.disabled = !uploadIsReady();
  $('cancelAnalysisBtn').disabled = !activeJob;
  $('startOverBtn').disabled = !activePackageId;
}
function clearUploadSelections() {
  ['pdfFile', 'southwestZip', 'southwestPairingsFile', 'southwestLinesFile', 'southwestSeniorityFile', 'southwestCoverFile'].forEach(id => { if ($(id)) $(id).value = ''; });
  syncChosenFile('pdfFile', 'pdfFileName'); syncChosenFile('southwestZip', 'southwestZipName');
}
function updatePayPriority(airline) {
  const field = $('payPriorityField'), select = $('payPriority'), previous = select.value;
  let saved = ''; try { saved = JSON.parse(localStorage.getItem('crewbidiqProfile') || '{}').pay_priority || ''; } catch (_) {}
  const options = airline === 'southwest' ? [
    ['', 'Quality of life first'], ['monthly_tfp', 'High TFP'], ['tfp_per_duty_period', 'TFP per duty period'], ['tfp_per_day_away', 'TFP efficiency']
  ] : airline === 'delta' ? [
    ['', 'Quality of life first'], ['trip_credit', 'Trip Credit'], ['total_pay', 'Total Pay'], ['additional_pay', 'Additional Pay'], ['credit_per_duty_day', 'Credit per duty day'], ['total_pay_per_duty_day', 'Total pay per duty day']
  ] : airline === 'american' ? [
    ['', 'Quality of life first'], ['total_pay', 'Total Pay'], ['total_pay_per_duty_day', 'Total pay per duty day']
  ] : [];
  field.classList.toggle('hidden', !options.length);
  select.innerHTML = options.map(([value, label]) => `<option value="${value}">${label}</option>`).join('');
  const desired = previous || saved;
  if (options.some(([value]) => value === desired)) select.value = desired;
}
function updateAirlineUI() { const airline = $('airlineChoice').value, chosen = Boolean(airline), southwest = airline === 'southwest'; $('uploadLocked').classList.toggle('hidden', chosen); $('pdfUploads').classList.toggle('hidden', !chosen || southwest); $('southwestUploads').classList.toggle('hidden', !chosen || !southwest); $('bidFleetField').classList.toggle('hidden', airline !== 'american'); updatePayPriority(airline); $('resultsTitle').textContent = terminology().title; updateAnalyzeAvailability(); }
function syncChosenFile(inputId, labelId) { const input = $(inputId), label = $(labelId); if (!input || !label) return false; const file = input.files && input.files[0]; label.textContent = file && file.name ? file.name : (label.dataset.emptyText || 'No file selected'); label.classList.toggle('has-file', Boolean(file)); return Boolean(file); }
function bindChosenFile(inputId, labelId) { const input = $(inputId); if (!input) return; const sync = () => { syncChosenFile(inputId, labelId); updateAnalyzeAvailability(); setTimeout(() => { syncChosenFile(inputId, labelId); updateAnalyzeAvailability(); }, 0); setTimeout(() => { syncChosenFile(inputId, labelId); updateAnalyzeAvailability(); }, 250); }; input.addEventListener('change', sync); input.addEventListener('input', sync); window.addEventListener('pageshow', sync); }

document.documentElement.dataset.theme = 'dark';
safeLocalStorageRemoveItem('crewbidiqTheme');
$('airlineChoice').addEventListener('change', () => { clearUploadSelections(); clearError(); updateAirlineUI(); });
updateAirlineUI(); loadAirlineTerminology(); applySaved();
bindChosenFile('pdfFile', 'pdfFileName'); bindChosenFile('southwestZip', 'southwestZipName');
['southwestPairingsFile', 'southwestLinesFile', 'southwestSeniorityFile', 'southwestCoverFile'].forEach(id => { $(id).addEventListener('change', updateAnalyzeAvailability); $(id).addEventListener('input', updateAnalyzeAvailability); });
$('cancelAnalysisBtn').addEventListener('click', async () => {
  if (!activeJob || !activePackageId) return;
  try {
    const form = new FormData();
    form.append('package_id', activePackageId);
    const response = await fetch(`/api/jobs/${encodeURIComponent(activeJob)}/cancel`, { method: 'POST', body: form, headers: analysisHeaders() });
    const body = await response.json();
    if (!response.ok) throw new Error(errorDetail(body, 'Could not cancel analysis.').user_message);
    clearTimeout(pollTimer);
    showError('Analysis cancelled.');
    updateAnalyzeAvailability();
  } catch (error) {
    showError(error.message || 'Could not cancel analysis.');
  }
});
$('startOverBtn').addEventListener('click', async () => {
  if (!activePackageId) return;
  try {
    const response = await fetch(`/api/packages/${encodeURIComponent(activePackageId)}/reset`, { method: 'POST', headers: analysisHeaders() });
    const body = await response.json();
    if (!response.ok) throw new Error(errorDetail(body, 'Could not reset package.').user_message);
    clearActiveAnalysis({ clearPackage: true });
    clearPackageDependentState();
    safeLocalStorageRemoveItem('crewbidiqLatestJob');
    setJob(false);
    $('analyzeBtn').textContent = 'Analyze bid package';
    clearError();
    updateAnalyzeAvailability();
  } catch (error) {
    showError(error.message || 'Could not reset package.');
  }
});

$('analyzeBtn').addEventListener('click', async () => {
  clearError();
  if (activeJob && $('analyzeBtn').textContent === 'Resume analysis') { await resumeAnalysis(); return; }
  if (uploadInFlight) return;
  syncChosenFile('pdfFile', 'pdfFileName'); syncChosenFile('southwestZip', 'southwestZipName');
  const airline = $('airlineChoice').value, data = new FormData();
  if (!airline) return showError('Select an airline before choosing a bid package.');
  data.append('airline', airline); data.append('context', airline); data.append('profile_json', JSON.stringify(profile()));
  if (airline === 'southwest') {
    const z = $('southwestZip').files[0], p = $('southwestPairingsFile').files[0], l = $('southwestLinesFile').files[0], s = $('southwestSeniorityFile').files[0], c = $('southwestCoverFile').files[0];
    if (z && (p || l || s || c)) return showError('Choose either the Southwest ZIP or individual text files, not both.');
    if (z) data.append('file', z); else if (p && l) { data.append('pairings_file', p); data.append('lines_file', l); if (s) data.append('seniority_file', s); if (c) data.append('cover_file', c); } else return showError('Choose the Southwest ZIP, or at least the Pairings and Lines text files.');
  } else { const file = $('pdfFile').files[0]; if (!file) return showError('Choose a bid-package PDF.'); data.append('file', file); }
  const button = $('analyzeBtn'); button.disabled = true; button.textContent = 'Uploading…'; setJob(true, 'Uploading', 1, 'Sending files securely to CrewBidIQ');
  uploadInFlight = true;
  try {
    data.append('session_id', browserSessionId());
    const response = await fetch('/api/jobs', { method: 'POST', body: data, headers: { Accept: 'application/json' } });
    const text = await response.text(); let body = {}; try { body = text ? JSON.parse(text) : {}; } catch (_) { throw new Error(`Upload failed (${response.status}). The server returned an invalid response.`); }
    if (!response.ok) { const detail = errorDetail(body, `Upload failed (${response.status})`); throw new Error(detail.user_message); }
    if (!body.package_id || body.package_persisted !== true) throw new Error('The upload was not saved. Please upload the bid package again.');
    if (!body.job_id) throw new Error('Upload completed, but no analysis job was created.');
    clearPackageDependentState();
    activePackageId = body.package_id; safeLocalStorageSetItem('crewbidiqActivePackage', activePackageId);
    safeLocalStorageRemoveItem('crewbidiqLatestJob'); latestJob = null;
    activeJob = body.job_id; safeLocalStorageSetItem('crewbidiqActiveJob', activeJob);
    lastConfirmedProgress = Number(body.progress_percent ?? body.progress ?? 1); pollFailures = 0; clearTimeout(pollTimer);
    persistAnalysisState({ ...body, state: body.state || 'queued', progress_percent: lastConfirmedProgress, package_persisted: true, retry_count: 0 });
    setJob(true, 'Queued', lastConfirmedProgress, body.user_message || body.message || 'Upload saved. Analysis is queued.');
    await pollJob();
  } catch (error) { showError(error.message || 'Upload failed'); setJob(false); button.textContent = 'Analyze bid package'; }
  finally { uploadInFlight = false; updateAnalyzeAvailability(); }
});

function schedulePoll(delay = 1500) { clearTimeout(pollTimer); pollTimer = setTimeout(() => pollJob(), delay); }
function inferredStageFromProgress(progress = 0) {
  const value = Number(progress) || 0;
  if (value >= 85) return 'building_recommendations';
  if (value >= 72) return 'normalizing';
  if (value >= 67) return 'parsing_details';
  if (value >= 65) return 'identifying_records';
  if (value >= 15) return 'extracting_text';
  if (value >= 1) return 'queued';
  return 'detecting_package';
}
function stateFromJob(body) {
  if (body.state) return body.state;
  return body.status === 'complete' ? 'completed' : body.status === 'failed' ? 'failed' : body.status === 'queued' ? 'queued' : 'parsing';
}
async function fetchJobStatus() {
  if (!activeJob || !activePackageId) throw Object.assign(new Error('No recoverable analysis reference exists.'), { status: 400, detail: { error_code: 'PACKAGE_NOT_PERSISTED' } });
  statusRequestController?.abort();
  const controller = new AbortController(); statusRequestController = controller; const timeout = setTimeout(() => controller.abort(), 12000);
  try {
    const response = await fetch(`/api/jobs/${encodeURIComponent(activeJob)}?package_id=${encodeURIComponent(activePackageId)}`, { headers: analysisHeaders(), signal: controller.signal });
    latestStatusCode = response.status;
    const text = await response.text(); let body = {}; try { body = text ? JSON.parse(text) : {}; } catch (_) {}
    if (!response.ok) { const detail = errorDetail(body, 'Could not read analysis status.'); throw Object.assign(new Error(detail.user_message), { status: response.status, detail }); }
    if (body.job_id !== activeJob || body.package_id !== activePackageId) throw Object.assign(new Error('The returned analysis does not match the active bid package.'), { status: 409, detail: { error_code: 'JOB_PACKAGE_MISMATCH' } });
    return body;
  } finally { clearTimeout(timeout); if (statusRequestController === controller) statusRequestController = null; }
}
function finishCompletedJob(body) {
  const completedJob = activeJob;
  clearTimeout(pollTimer); safeLocalStorageRemoveItem('crewbidiqActiveJob'); safeLocalStorageRemoveItem(analysisStateKey);
  activeJob = null; analysisState = {}; pollFailures = 0;
  applyCompletedJob(completedJob, body); $('analyzeBtn').textContent = 'Analyze bid package'; updateAnalyzeAvailability(); updateAnalysisDebug();
}
function applyJobStatus(body) {
  const state = stateFromJob(body);
  lastConfirmedProgress = Math.max(lastConfirmedProgress, Number(body.progress_percent ?? body.progress ?? 0));
  pollFailures = 0;
  persistAnalysisState({ ...body, state, progress_percent: lastConfirmedProgress, latest_status_code: latestStatusCode, retry_count: body.retry_count || 0 });
  setJob(true, body.stage_label || state.replace(/_/g, ' '), lastConfirmedProgress, body.user_message || body.message || '');
  if (state === 'completed' || body.status === 'complete') { finishCompletedJob(body); return; }
  if (state === 'failed' || state === 'expired' || state === 'cancelled' || body.status === 'failed') {
    clearTimeout(pollTimer); $('analyzeBtn').disabled = false; $('analyzeBtn').textContent = 'Resume analysis';
    showError(body.user_message || body.error || 'Analysis stopped. Resume analysis or upload the package again.'); return;
  }
  if (ACTIVE_ANALYSIS_STATES.has(state)) schedulePoll(1500);
}
function handlePollFailure(error) {
  const status = Number(error.status || 0), code = error.detail?.error_code || (error.name === 'AbortError' ? 'POLLING_NETWORK_ERROR' : 'UNKNOWN_ANALYSIS_ERROR');
  latestStatusCode = status || 'network';
  if (code === 'PACKAGE_NOT_PERSISTED') {
    clearActiveAnalysis({ clearPackage: true }); setJob(false); showError('The upload was not saved. Please upload the bid package again.'); $('analyzeBtn').textContent = 'Analyze bid package'; updateAnalyzeAvailability(); return;
  }
  if (status === 404 || status === 410) {
    const state = status === 410 ? 'expired' : 'stale';
    persistAnalysisState({ state, error_code: code, latest_status_code: status, recoverable: error.detail?.recoverable !== false });
    setJob(true, state === 'expired' ? 'Expired' : 'Analysis not found', lastConfirmedProgress, error.message);
    clearTimeout(pollTimer); $('analyzeBtn').disabled = false; $('analyzeBtn').textContent = 'Resume analysis'; showError(error.message); return;
  }
  if (status === 401 || status === 403 || status === 409) {
    const message = error.message || (status === 409 ? 'This analysis belongs to another package.' : 'This browser session expired.');
    clearActiveAnalysis({ clearPackage: true }); setJob(false); showError(`${message} Please upload the bid package again.`); $('analyzeBtn').textContent = 'Analyze bid package'; updateAnalyzeAvailability(); return;
  }
  if (status && status < 500 && status !== 429) {
    persistAnalysisState({ state: 'failed', error_code: code, latest_status_code: status });
    clearTimeout(pollTimer); setJob(true, 'Failed', lastConfirmedProgress, error.message); showError(error.message); $('analyzeBtn').disabled = false; $('analyzeBtn').textContent = 'Resume analysis'; return;
  }
  pollFailures += 1;
  const safe = analysisState.package_persisted === true ? 'Your saved upload is recoverable. ' : '';
  const message = `${safe}Connection interrupted. Last confirmed progress: ${lastConfirmedProgress}%.`;
  persistAnalysisState({
    state: 'reconnecting',
    current_stage: analysisState.current_stage || inferredStageFromProgress(lastConfirmedProgress),
    stage_label: analysisState.stage_label || analysisState.message || 'Building recommendation data',
    error_code: status === 429 ? 'RATE_LIMITED' : 'POLLING_NETWORK_ERROR',
    retry_count: pollFailures,
    latest_status_code: latestStatusCode,
  });
  setJob(true, 'Reconnecting', lastConfirmedProgress, message);
  if (pollFailures <= MAX_POLL_RETRIES) schedulePoll(Math.min(1500 * (2 ** (pollFailures - 1)), 15000));
  else { clearTimeout(pollTimer); $('analyzeBtn').disabled = false; $('analyzeBtn').textContent = 'Resume analysis'; showError('The connection could not be restored automatically. Tap Resume analysis to check the saved job now.'); }
}
async function pollJob() {
  if (pollInFlight || !activeJob || !activePackageId) return;
  pollInFlight = true;
  try { applyJobStatus(await fetchJobStatus()); }
  catch (error) { if (error.name !== 'AbortError') handlePollFailure(error); }
  finally { pollInFlight = false; }
}
async function restartPersistedAnalysis() {
  if (!activePackageId) throw new Error('The upload was not saved. Please upload the bid package again.');
  const data = new FormData(); data.append('session_id', browserSessionId());
  const response = await fetch(`/api/packages/${encodeURIComponent(activePackageId)}/analysis-jobs`, { method: 'POST', body: data, headers: { Accept: 'application/json' } });
  latestStatusCode = response.status;
  const text = await response.text(); let body = {}; try { body = text ? JSON.parse(text) : {}; } catch (_) {}
  if (!response.ok) { const detail = errorDetail(body, 'The saved upload could not be restarted.'); throw Object.assign(new Error(detail.user_message), { status: response.status, detail }); }
  if (!body.job_id || body.package_id !== activePackageId) throw Object.assign(new Error('The replacement analysis did not match the active package.'), { status: 409, detail: { error_code: 'JOB_PACKAGE_MISMATCH' } });
  activeJob = body.job_id; safeLocalStorageSetItem('crewbidiqActiveJob', activeJob); safeLocalStorageRemoveItem('crewbidiqLatestJob'); latestJob = null;
  lastConfirmedProgress = Number(body.progress_percent ?? body.progress ?? 1); pollFailures = 0;
  persistAnalysisState({ ...body, state: stateFromJob(body), progress_percent: lastConfirmedProgress, package_persisted: true, latest_status_code: response.status });
  applyJobStatus(body);
}
async function resumeAnalysis() {
  if (resumeInFlight) return;
  if (!activeJob || !activePackageId) { clearActiveAnalysis({ clearPackage: true }); showError('The upload was not saved. Please upload the bid package again.'); return; }
  resumeInFlight = true; const button = $('analyzeBtn'); button.disabled = true; button.textContent = 'Resuming…'; clearError();
  try {
    const body = await fetchJobStatus();
    const state = stateFromJob(body);
    if ((state === 'failed' || state === 'expired') && body.package_persisted && body.recoverable) await restartPersistedAnalysis();
    else applyJobStatus(body);
  } catch (error) {
    if (error.name === 'AbortError') return;
    if (error.status === 404 || error.status === 410) {
      try { await restartPersistedAnalysis(); }
      catch (restartError) { handlePollFailure(restartError); }
    } else handlePollFailure(error);
  } finally {
    resumeInFlight = false;
    if (activeJob && !ACTIVE_ANALYSIS_STATES.has(analysisState.state)) { button.disabled = false; button.textContent = 'Resume analysis'; }
  }
}

function applyCompletedJob(jobId, body) {
  activePackageId = acceptPackagePayload(body);
  safeLocalStorageSetItem('crewbidiqActivePackage', activePackageId);
  latestJob = jobId;
  safeLocalStorageSetItem('crewbidiqLatestJob', latestJob);
  if (body.airline && Array.from($('airlineChoice').options).some(option => option.value === body.airline)) {
    $('airlineChoice').value = body.airline;
    updateAirlineUI();
  }
  $('runPreferencesBtn').disabled = false;
  allResults = body.results || [];
  bidSynopsis = body.synopsis || null;
  renderSynopsis();
  render();
  $('csvLink').href = `/api/jobs/${latestJob}/report.pdf?package_id=${encodeURIComponent(activePackageId)}`;
  $('csvLink').classList.remove('disabled');
  setLabsContinuation(true);
}

async function loadLatestJob() {
  if (!latestJob || activeJob) {
    requestAnimationFrame(() => $('resultsPanel').scrollIntoView({ block: 'start' }));
    return;
  }
  try {
    const response = await fetch(`/api/jobs/${latestJob}?package_id=${encodeURIComponent(activePackageId || '')}`, { headers: analysisHeaders() });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || 'Could not load the saved analysis');
    acceptPackagePayload(body);
    if (body.status === 'complete') applyCompletedJob(latestJob, body);
  } catch (error) {
    safeLocalStorageRemoveItem('crewbidiqLatestJob');
    safeLocalStorageRemoveItem('crewbidiqActivePackage'); activePackageId = null;
    latestJob = null;
    setLabsContinuation(false);
    showError(error.message || 'Could not load the saved analysis');
  } finally {
    requestAnimationFrame(() => $('resultsPanel').scrollIntoView({ block: 'start' }));
  }
}

function esc(value) { return String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
function tripModel(item) { return item?.canonical_trip || null; }
function tripLegs(item) {
  const normalized = tripModel(item)?.ordered_legs || item?.ordered_legs;
  if (normalized?.length) return normalized;
  return (item?.legs || []).map((leg, index) => ({ sequence_index: index + 1, duty_day_index: leg.duty_day_index, origin: leg.departure, destination: leg.arrival, operating_or_deadhead: leg.deadhead ? 'deadhead' : 'operating', flight_number: leg.flight, equipment: leg.aircraft, local_departure_time: leg.departure_time, local_arrival_time: leg.arrival_time }));
}
function tripLayovers(item) { return tripModel(item)?.layovers || item?.layovers || []; }
function tripOperatingCities(item) { return tripModel(item)?.operating_cities || []; }
function tripLength(item) { return tripModel(item)?.trip_length_days ?? item?.trip_length_days ?? item?.trip_length; }
function tripOperatingDates(item) { return tripModel(item)?.operating_dates || item?.operating_dates || item?.dates || []; }
function tripPay(item) { return tripModel(item)?.pay_breakdown || item?.pay_breakdown || { trip_credit: item?.trip_credit ?? item?.credit, edp: item?.edp, hol: item?.hol, sit: item?.sit, additional_pay: item?.additional_pay, total_pay: item?.total_pay, raw_pay_tokens: item?.raw_pay_tokens || [], unresolved_pay_tokens: item?.unresolved_pay_tokens || [] }; }
function tripTfp(item) { return tripModel(item)?.tfp || item?.tfp || { pairing_tfp: item?.pairing_tfp, line_tfp: item?.line_tfp, monthly_tfp: item?.monthly_tfp, carry_out_tfp: item?.carry_out_tfp, tfp_per_duty_period: item?.tfp_per_duty_period, tfp_per_day_away: item?.tfp_per_day_away }; }
function rating(item) { if (item.match_label) { const classes = { exact: 'excellent', strong: 'strong', partial: 'fair', near: 'low' }; return [item.match_label, classes[item.match_class] || 'fair']; } const level = item.match_level || 'fair', labels = { excellent: '★★★★★ Excellent', strong: '★★★★ Strong', good: '★★★ Good', fair: '★★ Fair', low: '★ Low' }; return [labels[level] || labels.fair, level]; }
function fatigueRisk(item) { const fatigue = item.fatigue_index; if (!fatigue) return ['Fatigue Index · Insufficient Data', 'neutral']; const classes = { Low: 'neutral', Moderate: 'good', High: 'fair', 'Very High': 'low', 'Insufficient Data': 'neutral' }; return [`Fatigue Index · ${fatigue.level}`, classes[fatigue.level] || 'neutral']; }
function fatigueDetails(item) { const fatigue = item.fatigue_index; if (!fatigue) return ''; const factors = (fatigue.contributing_factors || []).map(esc).join('; ') || 'No elevated schedule factors detected'; const mitigating = (fatigue.mitigating_factors || []).map(esc).join('; ') || 'None identified'; return `<p><strong>Fatigue Index:</strong> ${esc(fatigue.level)} (${esc(fatigue.confidence)} confidence)</p><p><strong>Contributing factors:</strong> ${factors}</p><p><strong>Mitigating factors:</strong> ${mitigating}</p>${fatigue.missing_data_warning ? `<p><strong>Missing data:</strong> ${esc(fatigue.missing_data_warning)}</p>` : ''}<p><strong>Legality:</strong> ${esc(fatigue.legality_assessment)}</p>`; }
function holdDetails(item) { const outlook = item.hold_outlook; if (!outlook) return ''; const seniority = item.seniority_context; const factors = (outlook.factors || outlook.evidence || []).map(esc).join('; ') || 'No trip-specific factors available'; return `${seniority ? `<p><strong>Seniority context:</strong> ${seniority.wording.map(esc).join(' ')}</p>` : ''}<p><strong>Desirability:</strong> ${esc(outlook.desirability || 'Insufficient Data')}</p><p><strong>Likelihood of Holding:</strong> ${esc(outlook.likelihood || outlook.outlook || 'Insufficient Data')} (${esc(outlook.confidence)} confidence)</p><p><strong>Factors used:</strong> ${factors}</p><p><strong>Estimate basis:</strong> ${esc(outlook.estimate_basis)}</p>${outlook.missing_data_warning ? `<p><strong>Missing data:</strong> ${esc(outlook.missing_data_warning)}</p>` : ''}`; }
function scheduleConflictDetails(item) { const analysis = item.schedule_conflict_analysis; if (!analysis) return ''; return `<p><strong>${esc(analysis.display_label)}:</strong> ${esc(analysis.conflict_value)}</p><p><strong>Detected schedule overlaps:</strong> ${esc((analysis.overlaps || []).map(value => `${value.event_type}: ${value.dates.join(', ')}`).join('; ') || 'None')}</p>`; }
function redeyeSummary(item) { const count = (item.redeye_legs || []).length; return count ? `${count} WOCL departure${count === 1 ? '' : 's'} (02:00–05:59 local)` : 'None'; }
function resultAirline(item) { return item.airline || $('airlineChoice').value || 'generic'; }
function payPresentation(item) {
  const airline = resultAirline(item), legs = (item.duty_legs || []).join(' · ') || '—', payBreakdown = tripPay(item), tfp = tripTfp(item);
  item = { ...item, total_pay: payBreakdown.total_pay ?? item.total_pay, additional_pay: payBreakdown.additional_pay ?? item.additional_pay };
  if (airline === 'southwest') {
    const line = item.item_type === 'line';
    const pairingTfp = tfp.pairing_tfp ?? item.pairing_tfp;
    return {
      snapshotLabel: line ? 'Line TFP' : 'Pairing TFP', snapshotValue: line ? item.line_tfp : pairingTfp,
      metrics: [[line ? 'Line TFP' : 'Pairing TFP', line ? item.line_tfp : pairingTfp], ['Carry-out TFP', line ? item.carry_out_tfp : null], ['TFP / duty period', tfp.tfp_per_duty_period ?? item.tfp_per_duty_period], ['TFP / day away', tfp.tfp_per_day_away ?? item.tfp_per_day_away]],
      detail: `<p><strong>${line ? 'Monthly TFP' : 'Pairing TFP'}:</strong> ${esc((line ? item.monthly_tfp : pairingTfp) || 'N/A')}</p>${line ? `<p><strong>Carry-out TFP:</strong> ${esc(item.carry_out_tfp ?? 'N/A')}</p>` : ''}<p><strong>TFP per duty period:</strong> ${esc(tfp.tfp_per_duty_period ?? item.tfp_per_duty_period ?? 'N/A')}</p><p><strong>TFP per day away:</strong> ${esc(tfp.tfp_per_day_away ?? item.tfp_per_day_away ?? 'N/A')}</p>`
    };
  }
  if (airline === 'delta') {
    const components = { EDP: payBreakdown.edp, HOL: payBreakdown.hol, SIT: payBreakdown.sit };
    const rows = ['EDP', 'HOL', 'SIT'].filter(label => components[label] != null).map(label => `<p><strong>${label}:</strong> ${esc(components[label])}</p>`).join('');
    const unknown = (payBreakdown.unresolved_pay_tokens || item.unresolved_pay_tokens || []).join(', ') || Object.entries(item.unknown_pay_components || {}).map(([label, value]) => `${label} ${value}`).join(', ');
    return {
      snapshotLabel: 'Total Pay', snapshotValue: payBreakdown.total_pay,
      metrics: [['Total Pay', item.total_pay], ['Trip Credit', payBreakdown.trip_credit], ['Additional Pay', item.additional_pay], ['Total pay / duty day', item.total_pay_per_duty_day]],
      detail: `<p><strong>Trip Credit:</strong> ${esc(payBreakdown.trip_credit || 'N/A')}</p><p><strong>Additional Pay:</strong> ${esc(payBreakdown.additional_pay ?? 'N/A')}</p>${rows}<p><strong>Total Pay:</strong> ${esc(payBreakdown.total_pay ?? 'N/A')}</p>${unknown ? `<p><strong>Unmapped source pay:</strong> ${esc(unknown)}</p>` : ''}`
    };
  }
  if (airline === 'american') {
    return {
      snapshotLabel: 'Total Pay', snapshotValue: payBreakdown.total_pay,
      metrics: [['Total Pay', payBreakdown.total_pay], ['TAFB', tripModel(item)?.tafb ?? item.tafb], ['Legs by duty day', legs], ['Total pay / duty day', item.total_pay_per_duty_day]],
      detail: `<p><strong>Total Pay:</strong> ${esc(payBreakdown.total_pay ?? 'N/A')}</p>`
    };
  }
  return { snapshotLabel: 'Credit', snapshotValue: payBreakdown.trip_credit, metrics: [['Credit', payBreakdown.trip_credit], ['TAFB', tripModel(item)?.tafb ?? item.tafb], ['Legs by duty day', legs], ['First / Last', `${item.first_day_legs ?? '—'} / ${item.last_day_legs ?? '—'}`]], detail: `<p><strong>Credit:</strong> ${esc(payBreakdown.trip_credit || 'N/A')}</p>` };
}
function metricStrip(metrics) { return `<div class="metric-strip">${metrics.map(([label, value]) => `<div><span>${esc(label)}</span><strong>${esc(value ?? '—')}</strong></div>`).join('')}</div>`; }
function timeline(item) { const legs = tripLegs(item); if (!legs.length) return '<p class="muted">Detailed legs are not available for this item.</p>'; return `<div class="timeline">${legs.map((leg, i) => { const sourceLeg = (item.legs || [])[i] || {}; const equipmentName = sourceLeg.aircraft_display_name || (leg.equipment ? (item.equipment_mapping_status === 'raw_unmapped' ? `AA EQ ${leg.equipment}` : leg.equipment) : ''); const equipment = equipmentName ? ` · ${esc(equipmentName)}` : ''; const wocl = sourceLeg.wocl_departure ? ' · WOCL departure' : ''; return `<div class="timeline-leg"><span>${leg.sequence_index || i + 1}</span><div><strong>${esc(leg.origin)} ${esc(leg.local_departure_time)} → ${esc(leg.destination)} ${esc(leg.local_arrival_time)}</strong><small>${leg.operating_or_deadhead === 'deadhead' ? 'Deadhead' : 'Operating'}${leg.flight_number ? ` · Flight ${esc(leg.flight_number)}` : ''}${equipment}${wocl}</small></div></div>`; }).join('')}</div>`; }
function renderBreakdown(id, rows, key, suffix = '') {
  const target = $(id); const values = rows || [];
  if (!values.length) { target.innerHTML = '<p class="muted">Not supplied in this package.</p>'; return; }
  target.innerHTML = values.map(row => `<div class="breakdown-row"><div><strong>${esc(row[key])}${suffix}</strong><span>${esc(row.count)} (${esc(row.percent)}%)</span></div><div class="breakdown-track"><i style="width:${Math.min(Number(row.percent) || 0, 100)}%"></i></div></div>`).join('');
}
function renderSynopsis() {
  const panel = $('synopsisPanel');
  if (!bidSynopsis) { panel.classList.add('hidden'); return; }
  panel.classList.remove('hidden');
  const redeyeCount = bidSynopsis.redeye?.count || 0, deadheadCount = bidSynopsis.deadhead?.count || 0;
  $('synopsisMetrics').innerHTML = `<article><span>Unique trips</span><strong>${esc(bidSynopsis.total || 0)}</strong><small>Repeated operating dates count once</small></article><article><span>Depart during WOCL</span><strong>${esc(bidSynopsis.redeye?.percent || 0)}%</strong><small>${esc(redeyeCount)} trips · 02:00–05:59 local</small></article><article><span>Contain deadheads</span><strong>${esc(bidSynopsis.deadhead?.percent || 0)}%</strong><small>${esc(deadheadCount)} ${deadheadCount === 1 ? 'trip' : 'trips'}</small></article><article><span>Overnight cities</span><strong>${esc(bidSynopsis.overnight_city_count || 0)}</strong><small>Distinct layover destinations</small></article>`;
  renderBreakdown('synopsisLengths', bidSynopsis.trip_lengths, 'days', '-day');
  renderBreakdown('synopsisStarts', bidSynopsis.start_airports, 'airport');
  renderBreakdown('synopsisFleets', bidSynopsis.fleets, 'fleet');
  renderBreakdown('synopsisLayovers', bidSynopsis.layover_cities, 'city');
}
function explanationList(title, values, fallback = '') {
  const rows = (values || []).filter(Boolean);
  if (!rows.length && !fallback) return '';
  return `<div class="explanation-group"><h5>${esc(title)}</h5><ul>${(rows.length ? rows : [fallback]).map(value => `<li>${esc(value)}</li>`).join('')}</ul></div>`;
}
function appendResultCards(items, wrap, term) {
  wrap.innerHTML = '';
  items.forEach((item, index) => {
    const model = tripModel(item), normalizedLayovers = tripLayovers(item);
    item = {
      ...item,
      layovers: normalizedLayovers,
      cities: normalizedLayovers.map(value => value.airport || value.city).filter(Boolean),
      touched_cities: tripOperatingCities(item),
      trip_length: tripLength(item),
      operating_dates: tripOperatingDates(item),
      tafb: model?.tafb ?? item.tafb,
    };
    const [label, cls] = rating(item), [rec, recCls] = fatigueRisk(item), layovers = (item.layovers || []).map(x => `${x.city}${x.duration ? ` ${x.duration}` : ''}`).join(', ') || 'No overnights', conflicts = item.calendar_conflicts || [], pay = payPresentation(item);
    const matched = item.matched_preferences || (item.reasons || []).slice(0, 6);
    const explanations = `${explanationList(item.eligible === false ? 'Near Match status' : 'Why it qualified', item.qualification_reasons, item.eligible === false ? 'Shown only as a Near Match because a hard requirement was not met.' : 'No hard requirement was violated.')}${explanationList('Matched preferences', matched)}${explanationList('Compromises', item.compromises)}${explanationList('Requirements not met', item.eligibility_violations)}${explanationList('Trip facts', item.neutral_attributes)}${item.pay_explanation ? explanationList('Pay ranking', [item.pay_explanation]) : ''}`;
    const card = document.createElement('article'); card.className = `result-card${item.eligible === false ? ' near-result-card' : ''}`;
    card.innerHTML = `<div class="rank-badge">${index + 1}</div><div class="result-main"><div class="result-top"><div><span class="item-label">${esc(item.display_label || term.single)}</span><h3>${esc(item.pairing)}</h3></div><span class="match-pill ${cls}">${esc(label)}</span></div>${metricStrip(pay.metrics)}<div class="status-row"><span class="status ${recCls}">${esc(rec)}</span><span class="status neutral">Overnights: ${esc(layovers)}</span>${conflicts.length ? `<span class="status low">${conflicts.length} conflict${conflicts.length > 1 ? 's' : ''}</span>` : '<span class="status excellent">No conflicts</span>'}</div><details><summary>${esc(term.details)}</summary><div class="detail-grid"><div><h4>${item.eligible === false ? 'What would need to change' : 'Why it matched'}</h4>${explanations}</div><div><h4>Summary</h4><p><strong>Trip length:</strong> ${esc(item.trip_length ? `${item.trip_length} days` : 'N/A')}</p><p><strong>Duty periods:</strong> ${esc((item.duty_legs || []).length || 'N/A')}</p><p><strong>Legs by duty day:</strong> ${esc((item.duty_legs || []).join(' · ') || 'N/A')}</p><p><strong>Layovers:</strong> ${esc((item.cities || []).join(', ') || 'None')}</p>${item.equipment_codes?.length ? `<p><strong>Equipment:</strong> ${esc((item.aircraft_display_names?.length ? item.aircraft_display_names : item.equipment_codes).join(', '))}</p>` : ''}<p><strong>Deadheads:</strong> ${esc(item.deadheads || 0)}</p><p><strong>Redeyes:</strong> ${esc(redeyeSummary(item))}</p>${fatigueDetails(item)}<p><strong>Operating dates:</strong> ${esc((item.operating_dates || item.dates || []).join(', ') || 'Not available')}</p><p><strong>TAFB:</strong> ${esc(item.tafb || 'N/A')}</p>${pay.detail}${holdDetails(item)}${scheduleConflictDetails(item)}<p><strong>Conflicts:</strong> ${esc(conflicts.join('; ') || 'None')}</p></div></div><details class="timeline-details"><summary>Timeline and duty legs</summary>${timeline(item)}</details><details class="operating-cities"><summary>All operating cities</summary><p>${esc((item.touched_cities || []).join(', ') || 'Not available')}</p></details><details class="original-display"><summary>${esc(term.viewOriginal)}</summary><pre>${esc(item.original_display || 'Not available')}</pre></details></details></div>`;
    if (!(item.operating_dates || item.dates || []).length) {
      Array.from(card.querySelectorAll('p')).find(row => row.querySelector('strong')?.textContent === 'Operating dates:')?.remove();
    }
    const statusRow = card.querySelector('.status-row');
    if (item.start_airport) statusRow.insertAdjacentHTML('beforeend', `<span class="status neutral">Starts ${esc(item.start_airport)}</span>`);
    if (item.fleet) statusRow.insertAdjacentHTML('beforeend', `<span class="status neutral">Fleet ${esc(item.fleet)}</span>`);
    if (item.data_quality === 'incomplete') statusRow.insertAdjacentHTML('beforeend', '<span class="status low">Incomplete parser data</span>');
    const reportButton = document.createElement('button');
    reportButton.className = 'diagnostic-button text-button'; reportButton.type = 'button'; reportButton.textContent = 'Report a result problem'; reportButton.disabled = !latestJob;
    reportButton.addEventListener('click', () => openDiagnostic(item.pairing));
    card.querySelector('.result-main').appendChild(reportButton);
    wrap.appendChild(card);
  });
}
function render() {
  const limit = $('resultLimit').value, count = limit === 'all' ? Number.MAX_SAFE_INTEGER : Number(limit), term = terminology();
  const eligible = allResults.filter(item => item.eligible === true), near = allResults.filter(item => item.eligible === false);
  const shown = eligible.slice(0, count), nearShown = near.slice(0, Math.min(count, 25));
  appendResultCards(shown, $('results'), term);
  if (!shown.length) $('results').innerHTML = `<div class="empty-state">${near.length ? 'No trips met every hard requirement. Review Near Matches below.' : 'Your ranked results will appear here.'}</div>`;
  $('nearMatchesPanel').classList.toggle('hidden', !near.length);
  appendResultCards(nearShown, $('nearResults'), term);
  const top = shown[0] || nearShown[0];
  if (top) { const topPay = payPresentation(top), [topRating] = rating(top), [topFatigue] = fatigueRisk(top); $('summary').textContent = `${term.analyzed}: ${allResults.length} · ${eligible.length} eligible · ${near.length} near matches.`; $('snapshotMatch').textContent = topRating; $('snapshotPayLabel').textContent = topPay.snapshotLabel; $('snapshotCredit').textContent = topPay.snapshotValue || '—'; $('snapshotLength').textContent = top.trip_length ? `${top.trip_length}-day` : (top.duty_legs && top.duty_legs.length ? `${top.duty_legs.length}-day` : '—'); $('snapshotFatigue').textContent = topFatigue; }
}

$('resultLimit').addEventListener('change', render);
$('saveProfileBtn').addEventListener('click', () => { safeLocalStorageSetItem('crewbidiqProfile', JSON.stringify(profile())); $('saveProfileBtn').textContent = 'Saved'; setTimeout(() => $('saveProfileBtn').textContent = 'Save on this device', 1200); });
$('runPreferencesBtn').addEventListener('click', async () => {
  clearError();
  if (!latestJob) return showError('Upload and analyze a bid package first.');
  const button = $('runPreferencesBtn'), data = new FormData();
  data.append('profile_json', JSON.stringify(profile()));
  data.append('package_id', activePackageId || '');
  button.disabled = true; button.textContent = 'Reranking…'; setJob(true, 'Updating', 85, 'Applying your preferences to the parsed bid package');
  try {
    const response = await fetch(`/api/jobs/${latestJob}/rescore`, { method: 'POST', body: data, headers: { Accept: 'application/json' } });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || 'Could not rerun preferences');
    acceptPackagePayload(body); allResults = body.results || []; bidSynopsis = body.synopsis || bidSynopsis; safeLocalStorageSetItem('crewbidiqProfile', JSON.stringify(profile())); renderSynopsis(); render();
    setJob(true, 'Complete', 100, body.message || 'Recommendations updated');
    $('csvLink').href = `/api/jobs/${latestJob}/report.pdf?package_id=${encodeURIComponent(activePackageId)}`; $('csvLink').classList.remove('disabled');
    $('resultsPanel').scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (error) { showError(error.message || 'Could not rerun preferences'); setJob(false); }
  finally { button.disabled = false; button.textContent = 'Run preferences'; }
});
function openDiagnostic(pairingId) {
  if (!latestJob) return showError('Analyze a bid package before creating a parser diagnostic.');
  diagnosticPairingId = pairingId; $('diagnosticNotes').value = ''; $('diagnosticCategory').value = 'missing_data';
  $('diagnosticTitle').textContent = `Report a problem with ${terminology().single} ${pairingId}`;
  $('diagnosticModal').classList.remove('hidden'); $('diagnosticCategory').focus();
}
function closeDiagnostic() { diagnosticPairingId = null; $('diagnosticModal').classList.add('hidden'); }
function downloadDiagnostic() {
  if (!latestJob || !diagnosticPairingId) return;
  clearError();
  const form = document.createElement('form');
  form.method = 'POST'; form.action = `/api/jobs/${latestJob}/diagnostic.json`; form.target = '_blank'; form.hidden = true;
  const fields = { pairing_id: diagnosticPairingId, category: $('diagnosticCategory').value, notes: $('diagnosticNotes').value.trim(), package_id: activePackageId || '' };
  Object.entries(fields).forEach(([name, value]) => { const input = document.createElement('input'); input.type = 'hidden'; input.name = name; input.value = value; form.appendChild(input); });
  document.body.appendChild(form); form.submit(); form.remove();
  closeDiagnostic(); setJob(true, 'Diagnostic download started', 100, 'Check your browser downloads for the JSON problem report.');
}
function toggleGuide(show) { $('guide').classList.toggle('hidden', !show); if (show) $('guide').scrollIntoView({ behavior: 'smooth' }); }
$('guideBtn').addEventListener('click', () => toggleGuide(true)); $('closeGuideBtn').addEventListener('click', () => toggleGuide(false));
$('closeDiagnosticBtn').addEventListener('click', closeDiagnostic); $('cancelDiagnosticBtn').addEventListener('click', closeDiagnostic); $('downloadDiagnosticBtn').addEventListener('click', downloadDiagnostic);
$('diagnosticModal').addEventListener('click', event => { if (event.target === $('diagnosticModal')) closeDiagnostic(); });
$('avoidHolidays').addEventListener('change', () => { if ($('avoidHolidays').checked) $('workHolidays').checked = false; }); $('workHolidays').addEventListener('change', () => { if ($('workHolidays').checked) $('avoidHolidays').checked = false; });
$('demoBtn').addEventListener('click', () => { allResults = [{ pairing: '2478', display_label: 'Rotation', match_level: 'excellent', credit: '21:35', tafb: '72:10', start_airport: 'ATL', fleet: '320', layovers: [{ city: 'SAN', duration: '16:00' }], cities: ['SAN'], touched_cities: ['ATL', 'MCO', 'SAN'], redeye: 'none', redeye_legs: [], deadheads: 0, duty_legs: [2, 3, 1], first_day_legs: 2, last_day_legs: 1, calendar_conflicts: [], reasons: ['SAN is a highest-priority overnight', 'Matches your preferred trip length', 'No required-day conflicts'], legs: [{ departure: 'ATL', departure_time: '0830', arrival: 'SAN', arrival_time: '1035', flight: '1234', aircraft: '321', deadhead: false }], original_display: '#2478 ATL 0830 SAN 1035' }, { pairing: '1884', display_label: 'Rotation', match_level: 'strong', credit: '19:50', tafb: '67:20', start_airport: 'ATL', fleet: '320', layovers: [{ city: 'BOS', duration: '14:20' }], cities: ['BOS'], touched_cities: ['ATL', 'BOS'], redeye: 'WOCL departure', redeye_legs: [{ departure: 'BOS', departure_time: '0230' }], deadheads: 1, duty_legs: [1, 3, 2], first_day_legs: 1, last_day_legs: 2, calendar_conflicts: ['Preferred off: 2026-08-11'], reasons: ['Departs during WOCL (02:00–05:59 local): BOS 0230', 'One deadhead', 'Touches a preferred day off'] }]; bidSynopsis = { total: 2, complete: 2, incomplete: 0, count_basis: 'unique_trip_id', redeye: { count: 1, percent: 50 }, deadhead: { count: 1, percent: 50 }, overnight_city_count: 2, trip_lengths: [{ days: '3', count: 2, percent: 100 }], start_airports: [{ airport: 'ATL', count: 2, percent: 100 }], fleets: [{ fleet: '320', count: 2, percent: 100 }], layover_cities: [{ city: 'SAN', count: 1, percent: 50 }, { city: 'BOS', count: 1, percent: 50 }] }; renderSynopsis(); render(); });
if ((activeJob && !activePackageId) || (!activeJob && analysisState.job_id)) clearActiveAnalysis({ clearPackage: !activePackageId });
if (activeJob && activePackageId) {
  lastConfirmedProgress = Number(analysisState.progress_percent || 0);
  setJob(true, 'Restoring analysis', lastConfirmedProgress, `Checking the saved job. Last confirmed progress: ${lastConfirmedProgress}%.`);
  resumeAnalysis();
}
window.addEventListener('online', () => { if (activeJob && !pollInFlight) resumeAnalysis(); });
window.addEventListener('pageshow', () => { if (activeJob && !pollInFlight) resumeAnalysis(); });
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible' && activeJob && !pollInFlight) resumeAnalysis(); });
window.addEventListener('pagehide', event => {
  if (!event.persisted) return;
  clearTimeout(pollTimer); statusRequestController?.abort(); statusRequestController = null;
  allResults = []; bidSynopsis = null;
  $('results')?.replaceChildren(); $('nearResults')?.replaceChildren(); $('synopsisMetrics')?.replaceChildren();
});
window.addEventListener('pageshow', event => {
  if (event.persisted && document.body.dataset.classicPage === 'results' && latestJob && activePackageId && !activeJob) loadLatestJob();
});
// Demo fixtures are isolated in an explicit, non-persistent package namespace.
$('demoBtn').addEventListener('click', () => { clearActiveAnalysis(); packageStateKeys.forEach(key => safeLocalStorageRemoveItem(key)); activePackageId = 'demo:explicit'; safeLocalStorageSetItem('crewbidiqActivePackage', activePackageId); latestJob = null; allResults = allResults.map(item => ({ ...item, package_id: activePackageId, demo_mode: true })); $('runPreferencesBtn').disabled = true; $('csvLink').classList.add('disabled'); setLabsContinuation(false); render(); });
if (latestJob && activePackageId) { $('runPreferencesBtn').disabled = false; $('csvLink').href = `/api/jobs/${latestJob}/report.pdf?package_id=${encodeURIComponent(activePackageId)}`; $('csvLink').classList.remove('disabled'); setLabsContinuation(true); }
if (document.body.dataset.classicPage === 'results') loadLatestJob();
