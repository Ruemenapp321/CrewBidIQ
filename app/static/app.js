const $ = id => document.getElementById(id);
let activeJob = localStorage.getItem('crewbidiqActiveJob');
let latestJob = localStorage.getItem('crewbidiqLatestJob');
let pollTimer = null;
let pollFailures = 0;
let allResults = [];
let airlineTerminology = { generic: { singular: 'Pairing', plural: 'Pairings', recommended: 'Recommended pairings', details: 'Pairing details', view_original: 'View original pairing', analyzed: 'Pairings analyzed' } };

const csv = value => value.split(',').map(x => x.trim().toUpperCase()).filter(Boolean);
const num = id => $(id) && $(id).value !== '' ? Number($(id).value) : null;
function mins(value, fallback) { if (!value) return fallback; const [h, m] = value.split(':').map(Number); return h * 60 + m; }
function profile() {
  return {
    elite_cities: csv($('eliteCities').value), secondary_cities: csv($('secondaryCities').value), small_cities: [], penalty_cities: csv($('penaltyCities').value),
    preferred_aircraft: csv($('preferredAircraft').value), preferred_trip_lengths: csv($('preferredTripLengths').value), max_deadheads: num('maxDeadheads'), max_transfers: num('maxTransfers'),
    max_legs_per_day: num('maxLegsPerDay'), max_first_day_legs: num('maxFirstDayLegs'), max_last_day_legs: num('maxLastDayLegs'), min_layover_hours: num('minLayoverHours'), max_legs_after_redeye: num('maxLegsAfterRedeye'),
    required_days_off: csv($('requiredDaysOff').value), preferred_days_off: csv($('preferredDaysOff').value), holiday_dates: csv($('holidayDates').value), preferred_weekdays: csv($('preferredWeekdays').value),
    earliest_report_minutes: mins($('earliestReport').value, 360), latest_release_minutes: mins($('latestRelease').value, 1320), prefer_weekends_off: $('preferWeekendsOff').checked,
    avoid_holidays: $('avoidHolidays').checked, work_holidays: $('workHolidays').checked, allow_productive_redeye: $('allowMidRotationRedeye').checked, allow_redeye_start: $('allowRedeyeStart').checked,
    avoid_final_redeye: $('avoidFinalRedeye').checked, avoid_reserve: false, prefer_operate: false,
    weights: { elite: num('wElite'), secondary: num('wSecondary'), small: num('wSmall'), penalty: num('wPenalty'), aircraft: num('wAircraft'), pure: num('wPure'), transfer: num('wTransfer'), deadhead: num('wDeadhead'), required_conflict: num('wRequiredConflict'), preferred_conflict: num('wPreferredConflict'), holiday_conflict: num('wHolidayConflict'), early_report: num('wEarlyReport'), late_release: num('wLateRelease') }
  };
}
function applySaved() { try { const p = JSON.parse(localStorage.getItem('crewbidiqProfile') || 'null'); if (!p) return; const map = { eliteCities: p.elite_cities, secondaryCities: p.secondary_cities, penaltyCities: p.penalty_cities, preferredAircraft: p.preferred_aircraft, preferredTripLengths: p.preferred_trip_lengths, requiredDaysOff: p.required_days_off, preferredDaysOff: p.preferred_days_off, holidayDates: p.holiday_dates, preferredWeekdays: p.preferred_weekdays }; Object.entries(map).forEach(([id, value]) => { if ($(id) && value) $(id).value = Array.isArray(value) ? value.join(',') : value; }); } catch (_) {} }
function setJob(show, status = '', progress = 0, message = '') { $('jobPanel').classList.toggle('hidden', !show); $('jobStatus').textContent = status; $('jobPercent').textContent = `${progress}%`; $('progressFill').style.width = `${progress}%`; $('jobMessage').textContent = message; }
function showError(message) { $('errorBox').textContent = message; $('errorBox').classList.remove('hidden'); }
function clearError() { $('errorBox').classList.add('hidden'); }
function terminology() { const configured = airlineTerminology[$('airlineChoice').value] || airlineTerminology.generic; return { single: configured.singular, plural: configured.plural, title: configured.recommended, details: configured.details, viewOriginal: configured.view_original, analyzed: configured.analyzed }; }
async function loadAirlineTerminology() { try { const response = await fetch('/api/airlines/terminology', { headers: { Accept: 'application/json' } }); if (response.ok) airlineTerminology = await response.json(); } catch (_) {} updateAirlineUI(); }
function updateAirlineUI() { const southwest = $('airlineChoice').value === 'southwest'; $('pdfUploads').classList.toggle('hidden', southwest); $('southwestUploads').classList.toggle('hidden', !southwest); $('resultsTitle').textContent = terminology().title; }
function setTheme(value) { document.documentElement.dataset.theme = value; localStorage.setItem('crewbidiqTheme', value); $('themeChoice').value = value; }
function syncChosenFile(inputId, labelId) { const input = $(inputId), label = $(labelId); if (!input || !label) return false; const file = input.files && input.files[0]; label.textContent = file && file.name ? file.name : 'No file selected'; label.classList.toggle('has-file', Boolean(file)); return Boolean(file); }
function bindChosenFile(inputId, labelId) { const input = $(inputId); if (!input) return; const sync = () => { syncChosenFile(inputId, labelId); setTimeout(() => syncChosenFile(inputId, labelId), 0); setTimeout(() => syncChosenFile(inputId, labelId), 250); }; input.addEventListener('change', sync); input.addEventListener('input', sync); window.addEventListener('pageshow', sync); }

$('themeChoice').addEventListener('change', event => setTheme(event.target.value));
setTheme(localStorage.getItem('crewbidiqTheme') || 'system');
$('airlineChoice').addEventListener('change', updateAirlineUI);
updateAirlineUI(); loadAirlineTerminology(); applySaved();
bindChosenFile('pdfFile', 'pdfFileName'); bindChosenFile('southwestZip', 'southwestZipName');

$('analyzeBtn').addEventListener('click', async () => {
  clearError();
  if (activeJob && $('analyzeBtn').textContent === 'Resume analysis') { pollFailures = 0; pollTimer = setInterval(pollJob, 1500); await pollJob(); return; }
  syncChosenFile('pdfFile', 'pdfFileName'); syncChosenFile('southwestZip', 'southwestZipName');
  const airline = $('airlineChoice').value, data = new FormData();
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
  } catch (error) { showError(error.message || 'Upload failed'); setJob(false); button.disabled = false; button.textContent = 'Analyze bid package'; }
});

async function pollJob() {
  if (!activeJob) return;
  try {
    const response = await fetch(`/api/jobs/${activeJob}`, { headers: { Accept: 'application/json' } }); const body = await response.json(); if (!response.ok) throw new Error(body.detail || 'Could not read job status');
    pollFailures = 0; setJob(true, body.status, body.progress, body.message || '');
    if (body.status === 'complete') { clearInterval(pollTimer); localStorage.removeItem('crewbidiqActiveJob'); latestJob = activeJob; localStorage.setItem('crewbidiqLatestJob', latestJob); $('analyzeBtn').disabled = false; $('analyzeBtn').textContent = 'Analyze bid package'; $('runPreferencesBtn').disabled = false; allResults = body.results || []; render(); $('csvLink').href = `/api/jobs/${latestJob}/report.pdf`; $('csvLink').classList.remove('disabled'); }
    else if (body.status === 'failed') { clearInterval(pollTimer); localStorage.removeItem('crewbidiqActiveJob'); activeJob = null; $('analyzeBtn').disabled = false; $('analyzeBtn').textContent = 'Analyze bid package'; showError(body.error || 'Analysis failed'); }
  } catch (error) { pollFailures += 1; setJob(true, 'Reconnecting', 1, 'Your upload is safe. Reconnecting to the analysis…'); if (pollFailures >= 8) { clearInterval(pollTimer); $('analyzeBtn').disabled = false; $('analyzeBtn').textContent = 'Resume analysis'; showError('The connection is taking longer than expected. Tap Resume analysis to try again.'); } }
}

function esc(value) { return String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
function rating(item) { const level = item.match_level || 'fair', labels = { excellent: '★★★★★ Excellent', strong: '★★★★ Strong', good: '★★★ Good', fair: '★★ Fair', low: '★ Low' }; return [labels[level] || labels.fair, level]; }
function recovery(item) { if (!item.redeye || item.redeye === 'none') return ['No redeye', 'neutral']; const max = Math.max(...(item.duty_legs || []), 0); if (max <= 1) return ['Easy recovery', 'excellent']; if (max === 2) return ['Moderate recovery', 'strong']; if (max === 3) return ['Heavy recovery', 'fair']; return ['Demanding recovery', 'low']; }
function timeline(item) { const legs = item.legs || []; if (!legs.length) return '<p class="muted">Detailed legs are not available for this item.</p>'; return `<div class="timeline">${legs.map((leg, i) => { const equipmentName = leg.aircraft_display_name || (leg.aircraft ? (item.equipment_mapping_status === 'raw_unmapped' ? `AA EQ ${leg.aircraft}` : leg.aircraft) : ''); const equipment = equipmentName ? ` · ${esc(equipmentName)}` : ''; return `<div class="timeline-leg"><span>${i + 1}</span><div><strong>${esc(leg.departure)} ${esc(leg.departure_time)} → ${esc(leg.arrival)} ${esc(leg.arrival_time)}</strong><small>${leg.deadhead ? 'Deadhead' : 'Operating'}${leg.flight ? ` · Flight ${esc(leg.flight)}` : ''}${equipment}</small></div></div>`; }).join('')}</div>`; }
function render() {
  const limit = $('resultLimit').value, shown = limit === 'all' ? allResults : allResults.slice(0, Number(limit)), wrap = $('results'), term = terminology(); wrap.innerHTML = '';
  shown.forEach((item, index) => {
    const [label, cls] = rating(item), [rec, recCls] = recovery(item), legs = (item.duty_legs || []).join(' · ') || '—', layovers = (item.layovers || []).map(x => `${x.city}${x.duration ? ` ${x.duration}` : ''}`).join(', ') || 'No overnights', reasons = (item.reasons || []).slice(0, 6), conflicts = item.calendar_conflicts || [], soft = item.item_type === 'line' ? 'N/A' : (item.soft_credit || ($('airlineChoice').value === 'delta' ? '—' : 'N/A'));
    const card = document.createElement('article'); card.className = 'result-card';
    card.innerHTML = `<div class="rank-badge">${index + 1}</div><div class="result-main"><div class="result-top"><div><span class="item-label">${esc(item.display_label || term.single)}</span><h3>${esc(item.pairing)}</h3></div><span class="match-pill ${cls}">${label}</span></div><div class="metric-strip"><div><span>Credit</span><strong>${esc(item.credit || '—')}</strong></div><div><span>TAFB</span><strong>${esc(item.tafb || '—')}</strong></div><div><span>Legs by duty day</span><strong>${esc(legs)}</strong></div><div><span>First / Last</span><strong>${esc(item.first_day_legs ?? '—')} / ${esc(item.last_day_legs ?? '—')}</strong></div></div><div class="status-row"><span class="status ${recCls}">${esc(rec)}</span><span class="status neutral">Overnights: ${esc(layovers)}</span>${conflicts.length ? `<span class="status low">${conflicts.length} conflict${conflicts.length > 1 ? 's' : ''}</span>` : '<span class="status excellent">No conflicts</span>'}</div><details><summary>${esc(term.details)}</summary><div class="detail-grid"><div><h4>Why it matched</h4><ul>${(reasons.length ? reasons : ['No strong preference signals were detected.']).map(x => `<li>${esc(x)}</li>`).join('')}</ul></div><div><h4>Summary</h4><p><strong>Layovers:</strong> ${esc((item.cities || []).join(', ') || 'None')}</p>${item.equipment_codes?.length ? `<p><strong>Equipment:</strong> ${esc((item.aircraft_display_names?.length ? item.aircraft_display_names : item.equipment_codes).join(', '))}</p>` : ''}<p><strong>Deadheads:</strong> ${esc(item.deadheads || 0)}</p><p><strong>Redeyes:</strong> ${esc(item.redeye || 'none')}</p><p><strong>Soft credit:</strong> ${esc(soft)}</p><p><strong>Conflicts:</strong> ${esc(conflicts.join('; ') || 'None')}</p></div></div><h4>Timeline and duty legs</h4>${timeline(item)}<details class="operating-cities"><summary>All operating cities</summary><p>${esc((item.touched_cities || []).join(', ') || 'Not available')}</p></details><details class="original-display"><summary>${esc(term.viewOriginal)}</summary><pre>${esc(item.original_display || 'Not available')}</pre></details></details></div>`;
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
    allResults = body.results || []; localStorage.setItem('crewbidiqProfile', JSON.stringify(profile())); render();
    setJob(true, 'Complete', 100, body.message || 'Recommendations updated');
    $('csvLink').href = `/api/jobs/${latestJob}/report.pdf`; $('csvLink').classList.remove('disabled');
    $('resultsPanel').scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (error) { showError(error.message || 'Could not rerun preferences'); setJob(false); }
  finally { button.disabled = false; button.textContent = 'Run preferences'; }
});
function toggleGuide(show) { $('guide').classList.toggle('hidden', !show); if (show) $('guide').scrollIntoView({ behavior: 'smooth' }); }
$('guideBtn').addEventListener('click', () => toggleGuide(true)); $('closeGuideBtn').addEventListener('click', () => toggleGuide(false));
$('avoidHolidays').addEventListener('change', () => { if ($('avoidHolidays').checked) $('workHolidays').checked = false; }); $('workHolidays').addEventListener('change', () => { if ($('workHolidays').checked) $('avoidHolidays').checked = false; });
$('demoBtn').addEventListener('click', () => { allResults = [{ pairing: '2478', display_label: 'Rotation', match_level: 'excellent', credit: '21:35', tafb: '72:10', layovers: [{ city: 'SAN', duration: '16:00' }], cities: ['SAN'], touched_cities: ['ATL', 'MCO', 'SAN'], redeye: 'none', deadheads: 0, duty_legs: [2, 3, 1], first_day_legs: 2, last_day_legs: 1, calendar_conflicts: [], reasons: ['SAN is a highest-priority overnight', 'Matches your preferred trip length', 'No required-day conflicts'], legs: [{ departure: 'ATL', departure_time: '0830', arrival: 'SAN', arrival_time: '1035', flight: '1234', aircraft: '321', deadhead: false }], original_display: '#2478 ATL 0830 SAN 1035' }, { pairing: '1884', display_label: 'Rotation', match_level: 'strong', credit: '19:50', tafb: '67:20', layovers: [{ city: 'BOS', duration: '14:20' }], cities: ['BOS'], touched_cities: ['ATL', 'BOS'], redeye: 'possible', deadheads: 1, duty_legs: [1, 3, 2], first_day_legs: 1, last_day_legs: 2, calendar_conflicts: ['Preferred off: 2026-08-11'], reasons: ['BOS is a preferred overnight', 'One deadhead', 'Touches a preferred day off'] }]; render(); });
if (activeJob) { setJob(true, 'Resuming', 1, 'Reconnecting to your analysis…'); pollTimer = setInterval(pollJob, 1500); pollJob(); }
if (latestJob) { $('runPreferencesBtn').disabled = false; $('csvLink').href = `/api/jobs/${latestJob}/report.pdf`; $('csvLink').classList.remove('disabled'); }
