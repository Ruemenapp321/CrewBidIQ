const $ = id => document.getElementById(id);
let activeJob = localStorage.getItem('crewbidiqActiveJob');
let latestJob = localStorage.getItem('crewbidiqLatestJob');
let pollTimer = null;
let pollFailures = 0;
let allResults = [];
let bidSynopsis = null;
let diagnosticPairingId = null;
let airlineTerminology = { generic: { singular: 'Pairing', plural: 'Pairings', recommended: 'Recommended pairings', details: 'Pairing details', view_original: 'View original pairing', analyzed: 'Pairings analyzed' } };

const csv = value => value.split(',').map(x => x.trim().toUpperCase()).filter(Boolean);
const num = id => $(id) && $(id).value !== '' ? Number($(id).value) : null;
function mins(value, fallback) { if (!value) return fallback; const [h, m] = value.split(':').map(Number); return h * 60 + m; }
function profile() {
  return {
    elite_cities: csv($('eliteCities').value), secondary_cities: csv($('secondaryCities').value), small_cities: [], penalty_cities: csv($('penaltyCities').value),
    base_airport: $('baseAirport').value.trim().toUpperCase(), bid_fleets: $('airlineChoice').value === 'american' ? csv($('bidFleets').value) : [], preferred_start_airports: csv($('preferredStartAirports').value), avoid_start_airports: csv($('avoidStartAirports').value),
    preferred_aircraft: csv($('preferredAircraft').value), preferred_trip_lengths: csv($('preferredTripLengths').value), max_deadheads: num('maxDeadheads'), max_transfers: num('maxTransfers'),
    max_legs_per_day: num('maxLegsPerDay'), max_first_day_legs: num('maxFirstDayLegs'), max_last_day_legs: num('maxLastDayLegs'), min_layover_hours: num('minLayoverHours'), max_legs_after_redeye: num('maxLegsAfterRedeye'),
    required_days_off: csv($('requiredDaysOff').value), preferred_days_off: csv($('preferredDaysOff').value), holiday_dates: csv($('holidayDates').value), preferred_weekdays: csv($('preferredWeekdays').value),
    earliest_report_minutes: mins($('earliestReport').value, null), latest_release_minutes: mins($('latestRelease').value, null), prefer_weekends_off: $('preferWeekendsOff').checked,
    avoid_holidays: $('avoidHolidays').checked, work_holidays: $('workHolidays').checked, allow_productive_redeye: $('allowMidRotationRedeye').checked, allow_redeye_start: $('allowRedeyeStart').checked,
    avoid_final_redeye: $('avoidFinalRedeye').checked, avoid_reserve: false, prefer_operate: false,
    weights: { elite: num('wElite'), secondary: num('wSecondary'), small: num('wSmall'), penalty: num('wPenalty'), aircraft: num('wAircraft'), pure: num('wPure'), transfer: num('wTransfer'), deadhead: num('wDeadhead'), start_preferred: num('wStartPreferred'), start_avoid: num('wStartAvoid'), required_conflict: num('wRequiredConflict'), preferred_conflict: num('wPreferredConflict'), holiday_conflict: num('wHolidayConflict'), early_report: num('wEarlyReport'), late_release: num('wLateRelease') }
  };
}
function applySaved() { try { const p = JSON.parse(localStorage.getItem('crewbidiqProfile') || 'null'); if (!p) return; const map = { eliteCities: p.elite_cities, secondaryCities: p.secondary_cities, penaltyCities: p.penalty_cities, preferredAircraft: p.preferred_aircraft, preferredTripLengths: p.preferred_trip_lengths, baseAirport: p.base_airport, bidFleets: p.bid_fleets, preferredStartAirports: p.preferred_start_airports, avoidStartAirports: p.avoid_start_airports, requiredDaysOff: p.required_days_off, preferredDaysOff: p.preferred_days_off, holidayDates: p.holiday_dates, preferredWeekdays: p.preferred_weekdays }; Object.entries(map).forEach(([id, value]) => { if ($(id) && value) $(id).value = Array.isArray(value) ? value.join(',') : value; }); } catch (_) {} }
function setJob(show, status = '', progress = 0, message = '') { $('jobPanel').classList.toggle('hidden', !show); $('jobStatus').textContent = status; $('jobPercent').textContent = `${progress}%`; $('progressFill').style.width = `${progress}%`; $('jobMessage').textContent = message; }
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
}
function clearUploadSelections() {
  ['pdfFile', 'southwestZip', 'southwestPairingsFile', 'southwestLinesFile', 'southwestSeniorityFile', 'southwestCoverFile'].forEach(id => { if ($(id)) $(id).value = ''; });
  syncChosenFile('pdfFile', 'pdfFileName'); syncChosenFile('southwestZip', 'southwestZipName');
}
function updateAirlineUI() { const airline = $('airlineChoice').value, chosen = Boolean(airline), southwest = airline === 'southwest'; $('uploadLocked').classList.toggle('hidden', chosen); $('pdfUploads').classList.toggle('hidden', !chosen || southwest); $('southwestUploads').classList.toggle('hidden', !chosen || !southwest); $('bidFleetField').classList.toggle('hidden', airline !== 'american'); $('resultsTitle').textContent = terminology().title; updateAnalyzeAvailability(); }
function syncChosenFile(inputId, labelId) { const input = $(inputId), label = $(labelId); if (!input || !label) return false; const file = input.files && input.files[0]; label.textContent = file && file.name ? file.name : (label.dataset.emptyText || 'No file selected'); label.classList.toggle('has-file', Boolean(file)); return Boolean(file); }
function bindChosenFile(inputId, labelId) { const input = $(inputId); if (!input) return; const sync = () => { syncChosenFile(inputId, labelId); updateAnalyzeAvailability(); setTimeout(() => { syncChosenFile(inputId, labelId); updateAnalyzeAvailability(); }, 0); setTimeout(() => { syncChosenFile(inputId, labelId); updateAnalyzeAvailability(); }, 250); }; input.addEventListener('change', sync); input.addEventListener('input', sync); window.addEventListener('pageshow', sync); }

document.documentElement.dataset.theme = 'dark';
localStorage.removeItem('crewbidiqTheme');
$('airlineChoice').addEventListener('change', () => { clearUploadSelections(); clearError(); updateAirlineUI(); });
updateAirlineUI(); loadAirlineTerminology(); applySaved();
bindChosenFile('pdfFile', 'pdfFileName'); bindChosenFile('southwestZip', 'southwestZipName');
['southwestPairingsFile', 'southwestLinesFile', 'southwestSeniorityFile', 'southwestCoverFile'].forEach(id => { $(id).addEventListener('change', updateAnalyzeAvailability); $(id).addEventListener('input', updateAnalyzeAvailability); });

$('analyzeBtn').addEventListener('click', async () => {
  clearError();
  if (activeJob && $('analyzeBtn').textContent === 'Resume analysis') { pollFailures = 0; pollTimer = setInterval(pollJob, 1500); await pollJob(); return; }
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
  try {
    const response = await fetch('/api/jobs', { method: 'POST', body: data, headers: { Accept: 'application/json' } });
    const text = await response.text(); let body = {}; try { body = text ? JSON.parse(text) : {}; } catch (_) { throw new Error(`Upload failed (${response.status}). The server returned an invalid response.`); }
    if (!response.ok) throw new Error(body.detail || body.error || `Upload failed (${response.status})`); if (!body.job_id) throw new Error('Upload completed, but no analysis job was created.');
    activeJob = body.job_id; localStorage.setItem('crewbidiqActiveJob', activeJob); pollFailures = 0; clearInterval(pollTimer); pollTimer = setInterval(pollJob, 1500); await pollJob();
  } catch (error) { showError(error.message || 'Upload failed'); setJob(false); button.textContent = 'Analyze bid package'; updateAnalyzeAvailability(); }
});

async function pollJob() {
  if (!activeJob) return;
  try {
    const response = await fetch(`/api/jobs/${activeJob}`, { headers: { Accept: 'application/json' } }); const body = await response.json(); if (!response.ok) throw new Error(body.detail || 'Could not read job status');
    pollFailures = 0; setJob(true, body.status, body.progress, body.message || '');
    if (body.status === 'complete') { const completedJob = activeJob; clearInterval(pollTimer); localStorage.removeItem('crewbidiqActiveJob'); activeJob = null; applyCompletedJob(completedJob, body); $('analyzeBtn').textContent = 'Analyze bid package'; updateAnalyzeAvailability(); }
    else if (body.status === 'failed') { clearInterval(pollTimer); localStorage.removeItem('crewbidiqActiveJob'); activeJob = null; $('analyzeBtn').textContent = 'Analyze bid package'; updateAnalyzeAvailability(); showError(body.error || 'Analysis failed'); }
  } catch (error) { pollFailures += 1; setJob(true, 'Reconnecting', 1, 'Your upload is safe. Reconnecting to the analysis…'); if (pollFailures >= 8) { clearInterval(pollTimer); $('analyzeBtn').disabled = false; $('analyzeBtn').textContent = 'Resume analysis'; showError('The connection is taking longer than expected. Tap Resume analysis to try again.'); } }
}

function applyCompletedJob(jobId, body) {
  latestJob = jobId;
  localStorage.setItem('crewbidiqLatestJob', latestJob);
  if (body.airline && Array.from($('airlineChoice').options).some(option => option.value === body.airline)) {
    $('airlineChoice').value = body.airline;
    updateAirlineUI();
  }
  $('runPreferencesBtn').disabled = false;
  allResults = body.results || [];
  bidSynopsis = body.synopsis || null;
  renderSynopsis();
  render();
  $('csvLink').href = `/api/jobs/${latestJob}/report.pdf`;
  $('csvLink').classList.remove('disabled');
  setLabsContinuation(true);
}

async function loadLatestJob() {
  if (!latestJob || activeJob) {
    requestAnimationFrame(() => $('resultsPanel').scrollIntoView({ block: 'start' }));
    return;
  }
  try {
    const response = await fetch(`/api/jobs/${latestJob}`, { headers: { Accept: 'application/json' } });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || 'Could not load the saved analysis');
    if (body.status === 'complete') applyCompletedJob(latestJob, body);
  } catch (error) {
    localStorage.removeItem('crewbidiqLatestJob');
    latestJob = null;
    setLabsContinuation(false);
    showError(error.message || 'Could not load the saved analysis');
  } finally {
    requestAnimationFrame(() => $('resultsPanel').scrollIntoView({ block: 'start' }));
  }
}

function esc(value) { return String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
function rating(item) { const level = item.match_level || 'fair', labels = { excellent: '★★★★★ Excellent', strong: '★★★★ Strong', good: '★★★ Good', fair: '★★ Fair', low: '★ Low' }; return [labels[level] || labels.fair, level]; }
function recovery(item) { if (!item.redeye || item.redeye === 'none') return ['No redeye', 'neutral']; const max = Math.max(...(item.duty_legs || []), 0); if (max <= 1) return ['Easy recovery', 'excellent']; if (max === 2) return ['Moderate recovery', 'strong']; if (max === 3) return ['Heavy recovery', 'fair']; return ['Demanding recovery', 'low']; }
function timeline(item) { const legs = item.legs || []; if (!legs.length) return '<p class="muted">Detailed legs are not available for this item.</p>'; return `<div class="timeline">${legs.map((leg, i) => { const equipmentName = leg.aircraft_display_name || (leg.aircraft ? (item.equipment_mapping_status === 'raw_unmapped' ? `AA EQ ${leg.aircraft}` : leg.aircraft) : ''); const equipment = equipmentName ? ` · ${esc(equipmentName)}` : ''; return `<div class="timeline-leg"><span>${i + 1}</span><div><strong>${esc(leg.departure)} ${esc(leg.departure_time)} → ${esc(leg.arrival)} ${esc(leg.arrival_time)}</strong><small>${leg.deadhead ? 'Deadhead' : 'Operating'}${leg.flight ? ` · Flight ${esc(leg.flight)}` : ''}${equipment}</small></div></div>`; }).join('')}</div>`; }
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
  $('synopsisMetrics').innerHTML = `<article><span>Total trips</span><strong>${esc(bidSynopsis.total || 0)}</strong></article><article><span>Contain redeyes</span><strong>${esc(bidSynopsis.redeye?.percent || 0)}%</strong><small>${esc(redeyeCount)} ${redeyeCount === 1 ? 'trip' : 'trips'}</small></article><article><span>Contain deadheads</span><strong>${esc(bidSynopsis.deadhead?.percent || 0)}%</strong><small>${esc(deadheadCount)} ${deadheadCount === 1 ? 'trip' : 'trips'}</small></article><article class="${bidSynopsis.incomplete ? 'quality-warning' : ''}"><span>Incomplete parses</span><strong>${esc(bidSynopsis.incomplete || 0)}</strong><small>${bidSynopsis.incomplete ? 'Excluded from the top ranks' : 'No missing duty data detected'}</small></article>`;
  renderBreakdown('synopsisLengths', bidSynopsis.trip_lengths, 'days', '-day');
  renderBreakdown('synopsisStarts', bidSynopsis.start_airports, 'airport');
  renderBreakdown('synopsisFleets', bidSynopsis.fleets, 'fleet');
  renderBreakdown('synopsisLayovers', bidSynopsis.layover_cities, 'city');
}
function render() {
  const limit = $('resultLimit').value, shown = limit === 'all' ? allResults : allResults.slice(0, Number(limit)), wrap = $('results'), term = terminology(); wrap.innerHTML = '';
  shown.forEach((item, index) => {
    const [label, cls] = rating(item), [rec, recCls] = recovery(item), legs = (item.duty_legs || []).join(' · ') || '—', layovers = (item.layovers || []).map(x => `${x.city}${x.duration ? ` ${x.duration}` : ''}`).join(', ') || 'No overnights', reasons = (item.reasons || []).slice(0, 6), conflicts = item.calendar_conflicts || [], soft = item.item_type === 'line' ? 'N/A' : (item.soft_credit || ($('airlineChoice').value === 'delta' ? '—' : 'N/A'));
    const card = document.createElement('article'); card.className = 'result-card';
    card.innerHTML = `<div class="rank-badge">${index + 1}</div><div class="result-main"><div class="result-top"><div><span class="item-label">${esc(item.display_label || term.single)}</span><h3>${esc(item.pairing)}</h3></div><span class="match-pill ${cls}">${label}</span></div><div class="metric-strip"><div><span>Credit</span><strong>${esc(item.credit || '—')}</strong></div><div><span>TAFB</span><strong>${esc(item.tafb || '—')}</strong></div><div><span>Legs by duty day</span><strong>${esc(legs)}</strong></div><div><span>First / Last</span><strong>${esc(item.first_day_legs ?? '—')} / ${esc(item.last_day_legs ?? '—')}</strong></div></div><div class="status-row"><span class="status ${recCls}">${esc(rec)}</span><span class="status neutral">Overnights: ${esc(layovers)}</span>${conflicts.length ? `<span class="status low">${conflicts.length} conflict${conflicts.length > 1 ? 's' : ''}</span>` : '<span class="status excellent">No conflicts</span>'}</div><details><summary>${esc(term.details)}</summary><div class="detail-grid"><div><h4>Why it matched</h4><ul>${(reasons.length ? reasons : ['No strong preference signals were detected.']).map(x => `<li>${esc(x)}</li>`).join('')}</ul></div><div><h4>Summary</h4><p><strong>Layovers:</strong> ${esc((item.cities || []).join(', ') || 'None')}</p>${item.equipment_codes?.length ? `<p><strong>Equipment:</strong> ${esc((item.aircraft_display_names?.length ? item.aircraft_display_names : item.equipment_codes).join(', '))}</p>` : ''}<p><strong>Deadheads:</strong> ${esc(item.deadheads || 0)}</p><p><strong>Redeyes:</strong> ${esc(item.redeye || 'none')}</p><p><strong>Soft credit:</strong> ${esc(soft)}</p><p><strong>Conflicts:</strong> ${esc(conflicts.join('; ') || 'None')}</p></div></div><h4>Timeline and duty legs</h4>${timeline(item)}<details class="operating-cities"><summary>All operating cities</summary><p>${esc((item.touched_cities || []).join(', ') || 'Not available')}</p></details><details class="original-display"><summary>${esc(term.viewOriginal)}</summary><pre>${esc(item.original_display || 'Not available')}</pre></details></details></div>`;
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
  if (!shown.length) wrap.innerHTML = '<div class="empty-state">Your ranked results will appear here.</div>';
  if (allResults.length) { const top = allResults[0], [topRating] = rating(top), [topRecovery] = recovery(top); $('summary').textContent = `${term.analyzed}: ${allResults.length} · showing ${shown.length}. Ratings reflect your selected preferences.`; $('snapshotMatch').textContent = topRating; $('snapshotCredit').textContent = top.credit || '—'; $('snapshotLength').textContent = top.duty_legs && top.duty_legs.length ? `${top.duty_legs.length}-day` : '—'; $('snapshotRecovery').textContent = topRecovery.replace(' recovery', ''); }
}

$('resultLimit').addEventListener('change', render);
$('saveProfileBtn').addEventListener('click', () => { localStorage.setItem('crewbidiqProfile', JSON.stringify(profile())); $('saveProfileBtn').textContent = 'Saved'; setTimeout(() => $('saveProfileBtn').textContent = 'Save on this device', 1200); });
$('runPreferencesBtn').addEventListener('click', async () => {
  clearError();
  if (!latestJob) return showError('Upload and analyze a bid package first.');
  const button = $('runPreferencesBtn'), data = new FormData();
  data.append('profile_json', JSON.stringify(profile()));
  button.disabled = true; button.textContent = 'Reranking…'; setJob(true, 'Updating', 85, 'Applying your preferences to the parsed bid package');
  try {
    const response = await fetch(`/api/jobs/${latestJob}/rescore`, { method: 'POST', body: data, headers: { Accept: 'application/json' } });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || 'Could not rerun preferences');
    allResults = body.results || []; bidSynopsis = body.synopsis || bidSynopsis; localStorage.setItem('crewbidiqProfile', JSON.stringify(profile())); renderSynopsis(); render();
    setJob(true, 'Complete', 100, body.message || 'Recommendations updated');
    $('csvLink').href = `/api/jobs/${latestJob}/report.pdf`; $('csvLink').classList.remove('disabled');
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
async function downloadDiagnostic() {
  if (!latestJob || !diagnosticPairingId) return;
  const button = $('downloadDiagnosticBtn'), data = new FormData();
  data.append('pairing_id', diagnosticPairingId); data.append('category', $('diagnosticCategory').value); data.append('notes', $('diagnosticNotes').value.trim());
  button.disabled = true; button.textContent = 'Creating…'; clearError();
  try {
    const response = await fetch(`/api/jobs/${latestJob}/diagnostic.json`, { method: 'POST', body: data });
    if (!response.ok) { let message = 'Could not create diagnostic file'; try { message = (await response.json()).detail || message; } catch (_) {} throw new Error(message); }
    const blob = await response.blob(), disposition = response.headers.get('content-disposition') || '';
    const name = disposition.match(/filename="?([^";]+)"?/i)?.[1] || `crewbidiq-diagnostic-${diagnosticPairingId}.json`;
    const url = URL.createObjectURL(blob), anchor = document.createElement('a'); anchor.href = url; anchor.download = name; document.body.appendChild(anchor); anchor.click(); anchor.remove(); setTimeout(() => URL.revokeObjectURL(url), 1500);
    closeDiagnostic(); setJob(true, 'Diagnostic ready', 100, 'Attach the downloaded JSON file in Codex or send it to support.');
  } catch (error) { showError(error.message || 'Could not create diagnostic file'); }
  finally { button.disabled = false; button.textContent = 'Create diagnostic file'; }
}
function toggleGuide(show) { $('guide').classList.toggle('hidden', !show); if (show) $('guide').scrollIntoView({ behavior: 'smooth' }); }
$('guideBtn').addEventListener('click', () => toggleGuide(true)); $('closeGuideBtn').addEventListener('click', () => toggleGuide(false));
$('closeDiagnosticBtn').addEventListener('click', closeDiagnostic); $('cancelDiagnosticBtn').addEventListener('click', closeDiagnostic); $('downloadDiagnosticBtn').addEventListener('click', downloadDiagnostic);
$('diagnosticModal').addEventListener('click', event => { if (event.target === $('diagnosticModal')) closeDiagnostic(); });
$('avoidHolidays').addEventListener('change', () => { if ($('avoidHolidays').checked) $('workHolidays').checked = false; }); $('workHolidays').addEventListener('change', () => { if ($('workHolidays').checked) $('avoidHolidays').checked = false; });
$('demoBtn').addEventListener('click', () => { allResults = [{ pairing: '2478', display_label: 'Rotation', match_level: 'excellent', credit: '21:35', tafb: '72:10', start_airport: 'ATL', fleet: '320', layovers: [{ city: 'SAN', duration: '16:00' }], cities: ['SAN'], touched_cities: ['ATL', 'MCO', 'SAN'], redeye: 'none', deadheads: 0, duty_legs: [2, 3, 1], first_day_legs: 2, last_day_legs: 1, calendar_conflicts: [], reasons: ['SAN is a highest-priority overnight', 'Matches your preferred trip length', 'No required-day conflicts'], legs: [{ departure: 'ATL', departure_time: '0830', arrival: 'SAN', arrival_time: '1035', flight: '1234', aircraft: '321', deadhead: false }], original_display: '#2478 ATL 0830 SAN 1035' }, { pairing: '1884', display_label: 'Rotation', match_level: 'strong', credit: '19:50', tafb: '67:20', start_airport: 'ATL', fleet: '320', layovers: [{ city: 'BOS', duration: '14:20' }], cities: ['BOS'], touched_cities: ['ATL', 'BOS'], redeye: 'possible', deadheads: 1, duty_legs: [1, 3, 2], first_day_legs: 1, last_day_legs: 2, calendar_conflicts: ['Preferred off: 2026-08-11'], reasons: ['BOS is a preferred overnight', 'One deadhead', 'Touches a preferred day off'] }]; bidSynopsis = { total: 2, complete: 2, incomplete: 0, redeye: { count: 1, percent: 50 }, deadhead: { count: 1, percent: 50 }, trip_lengths: [{ days: '3', count: 2, percent: 100 }], start_airports: [{ airport: 'ATL', count: 2, percent: 100 }], fleets: [{ fleet: '320', count: 2, percent: 100 }], layover_cities: [{ city: 'SAN', count: 1, percent: 50 }, { city: 'BOS', count: 1, percent: 50 }] }; renderSynopsis(); render(); });
if (activeJob) { setJob(true, 'Resuming', 1, 'Reconnecting to your analysis…'); pollTimer = setInterval(pollJob, 1500); pollJob(); }
if (latestJob) { $('runPreferencesBtn').disabled = false; $('csvLink').href = `/api/jobs/${latestJob}/report.pdf`; $('csvLink').classList.remove('disabled'); setLabsContinuation(true); }
if (document.body.dataset.classicPage === 'results') loadLatestJob();
