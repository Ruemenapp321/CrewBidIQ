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
function rating(item) { if (item.match_label) { const classes = { exact: 'excellent', strong: 'strong', partial: 'fair', near: 'low' }; return [item.match_label, classes[item.match_class] || 'fair']; } const level = item.match_level || 'fair', labels = { excellent: '★★★★★ Excellent', strong: '★★★★ Strong', good: '★★★ Good', fair: '★★ Fair', low: '★ Low' }; return [labels[level] || labels.fair, level]; }
function fatigueRisk(item) { const fatigue = item.fatigue_index; if (!fatigue) return ['Fatigue Index · Insufficient Data', 'neutral']; const classes = { Low: 'neutral', Moderate: 'good', High: 'fair', 'Very High': 'low', 'Insufficient Data': 'neutral' }; return [`Fatigue Index · ${fatigue.level}`, classes[fatigue.level] || 'neutral']; }
function fatigueDetails(item) { const fatigue = item.fatigue_index; if (!fatigue) return ''; const factors = (fatigue.contributing_factors || []).map(esc).join('; ') || 'No elevated schedule factors detected'; const mitigating = (fatigue.mitigating_factors || []).map(esc).join('; ') || 'None identified'; return `<p><strong>Fatigue Index:</strong> ${esc(fatigue.level)} (${esc(fatigue.confidence)} confidence)</p><p><strong>Contributing factors:</strong> ${factors}</p><p><strong>Mitigating factors:</strong> ${mitigating}</p><p><strong>Legality:</strong> ${esc(fatigue.legality_assessment)}</p>`; }
function holdDetails(item) { const outlook = item.hold_outlook; if (!outlook) return ''; const seniority = item.seniority_context; return `${seniority ? `<p><strong>Seniority context:</strong> ${seniority.wording.map(esc).join(' ')}</p>` : ''}<p><strong>Hold outlook:</strong> ${esc(outlook.outlook)} (${esc(outlook.confidence)} confidence)</p><p><strong>Estimate basis:</strong> ${esc(outlook.estimate_basis)}</p>`; }
function scheduleConflictDetails(item) { const analysis = item.schedule_conflict_analysis; if (!analysis) return ''; return `<p><strong>${esc(analysis.display_label)}:</strong> ${esc(analysis.conflict_value)}</p><p><strong>Detected schedule overlaps:</strong> ${esc((analysis.overlaps || []).map(value => `${value.event_type}: ${value.dates.join(', ')}`).join('; ') || 'None')}</p>`; }
function redeyeSummary(item) { const count = (item.redeye_legs || []).length; return count ? `${count} WOCL departure${count === 1 ? '' : 's'} (02:00–05:59 local)` : 'None'; }
function resultAirline(item) { return item.airline || $('airlineChoice').value || 'generic'; }
function payPresentation(item) {
  const airline = resultAirline(item), legs = (item.duty_legs || []).join(' · ') || '—';
  if (airline === 'southwest') {
    const line = item.item_type === 'line';
    return {
      snapshotLabel: line ? 'Line TFP' : 'Pairing TFP', snapshotValue: line ? item.line_tfp : item.pairing_tfp,
      metrics: [[line ? 'Line TFP' : 'Pairing TFP', line ? item.line_tfp : item.pairing_tfp], ['Carry-out TFP', line ? item.carry_out_tfp : null], ['TFP / duty period', item.tfp_per_duty_period], ['TFP / day away', item.tfp_per_day_away]],
      detail: `<p><strong>${line ? 'Monthly TFP' : 'Pairing TFP'}:</strong> ${esc((line ? item.monthly_tfp : item.pairing_tfp) || 'N/A')}</p>${line ? `<p><strong>Carry-out TFP:</strong> ${esc(item.carry_out_tfp ?? 'N/A')}</p>` : ''}<p><strong>TFP per duty period:</strong> ${esc(item.tfp_per_duty_period || 'N/A')}</p><p><strong>TFP per day away:</strong> ${esc(item.tfp_per_day_away || 'N/A')}</p>`
    };
  }
  if (airline === 'delta') {
    const components = item.pay_components || {};
    const rows = ['EDP', 'HOL', 'SIT'].filter(label => Object.prototype.hasOwnProperty.call(components, label)).map(label => `<p><strong>${label}:</strong> ${esc(components[label])}</p>`).join('');
    const unknown = Object.entries(item.unknown_pay_components || {}).map(([label, value]) => `${label} ${value}`).join(', ');
    return {
      snapshotLabel: 'Total Pay', snapshotValue: item.total_pay,
      metrics: [['Total Pay', item.total_pay], ['Trip Credit', item.trip_credit || item.credit], ['Additional Pay', item.additional_pay], ['Total pay / duty day', item.total_pay_per_duty_day]],
      detail: `<p><strong>Trip Credit:</strong> ${esc(item.trip_credit || item.credit || 'N/A')}</p><p><strong>Additional Pay:</strong> ${esc(item.additional_pay ?? 'N/A')}</p>${rows}<p><strong>Total Pay:</strong> ${esc(item.total_pay ?? 'N/A')}</p>${unknown ? `<p><strong>Unmapped source pay:</strong> ${esc(unknown)}</p>` : ''}`
    };
  }
  if (airline === 'american') {
    return {
      snapshotLabel: 'Total Pay', snapshotValue: item.total_pay,
      metrics: [['Total Pay', item.total_pay], ['TAFB', item.tafb], ['Legs by duty day', legs], ['Total pay / duty day', item.total_pay_per_duty_day]],
      detail: `<p><strong>Total Pay:</strong> ${esc(item.total_pay ?? 'N/A')}</p>`
    };
  }
  return { snapshotLabel: 'Credit', snapshotValue: item.credit, metrics: [['Credit', item.credit], ['TAFB', item.tafb], ['Legs by duty day', legs], ['First / Last', `${item.first_day_legs ?? '—'} / ${item.last_day_legs ?? '—'}`]], detail: `<p><strong>Credit:</strong> ${esc(item.credit || 'N/A')}</p>` };
}
function metricStrip(metrics) { return `<div class="metric-strip">${metrics.map(([label, value]) => `<div><span>${esc(label)}</span><strong>${esc(value ?? '—')}</strong></div>`).join('')}</div>`; }
function timeline(item) { const legs = item.legs || []; if (!legs.length) return '<p class="muted">Detailed legs are not available for this item.</p>'; return `<div class="timeline">${legs.map((leg, i) => { const equipmentName = leg.aircraft_display_name || (leg.aircraft ? (item.equipment_mapping_status === 'raw_unmapped' ? `AA EQ ${leg.aircraft}` : leg.aircraft) : ''); const equipment = equipmentName ? ` · ${esc(equipmentName)}` : ''; const wocl = leg.wocl_departure ? ' · WOCL departure' : ''; return `<div class="timeline-leg"><span>${i + 1}</span><div><strong>${esc(leg.departure)} ${esc(leg.departure_time)} → ${esc(leg.arrival)} ${esc(leg.arrival_time)}</strong><small>${leg.deadhead ? 'Deadhead' : 'Operating'}${leg.flight ? ` · Flight ${esc(leg.flight)}` : ''}${equipment}${wocl}</small></div></div>`; }).join('')}</div>`; }
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
    const [label, cls] = rating(item), [rec, recCls] = fatigueRisk(item), layovers = (item.layovers || []).map(x => `${x.city}${x.duration ? ` ${x.duration}` : ''}`).join(', ') || 'No overnights', conflicts = item.calendar_conflicts || [], pay = payPresentation(item);
    const matched = item.matched_preferences || (item.reasons || []).slice(0, 6);
    const explanations = `${explanationList(item.eligible === false ? 'Closest fit' : 'Matched preferences', matched, 'No strong preference signals were detected.')}${explanationList('Compromises', item.compromises)}${explanationList('Requirements not met', item.eligibility_violations)}${explanationList('Trip facts', item.neutral_attributes)}${item.pay_explanation ? explanationList('Pay ranking', [item.pay_explanation]) : ''}`;
    const card = document.createElement('article'); card.className = `result-card${item.eligible === false ? ' near-result-card' : ''}`;
    card.innerHTML = `<div class="rank-badge">${index + 1}</div><div class="result-main"><div class="result-top"><div><span class="item-label">${esc(item.display_label || term.single)}</span><h3>${esc(item.pairing)}</h3></div><span class="match-pill ${cls}">${esc(label)}</span></div>${metricStrip(pay.metrics)}<div class="status-row"><span class="status ${recCls}">${esc(rec)}</span><span class="status neutral">Overnights: ${esc(layovers)}</span>${conflicts.length ? `<span class="status low">${conflicts.length} conflict${conflicts.length > 1 ? 's' : ''}</span>` : '<span class="status excellent">No conflicts</span>'}</div><details><summary>${esc(term.details)}</summary><div class="detail-grid"><div><h4>${item.eligible === false ? 'What would need to change' : 'Why it matched'}</h4>${explanations}</div><div><h4>Summary</h4><p><strong>Trip length:</strong> ${esc(item.trip_length ? `${item.trip_length} days` : 'N/A')}</p><p><strong>Duty periods:</strong> ${esc((item.duty_legs || []).length || 'N/A')}</p><p><strong>Legs by duty day:</strong> ${esc((item.duty_legs || []).join(' · ') || 'N/A')}</p><p><strong>Layovers:</strong> ${esc((item.cities || []).join(', ') || 'None')}</p>${item.equipment_codes?.length ? `<p><strong>Equipment:</strong> ${esc((item.aircraft_display_names?.length ? item.aircraft_display_names : item.equipment_codes).join(', '))}</p>` : ''}<p><strong>Deadheads:</strong> ${esc(item.deadheads || 0)}</p><p><strong>Redeyes:</strong> ${esc(redeyeSummary(item))}</p>${fatigueDetails(item)}<p><strong>Operating dates:</strong> ${esc((item.operating_dates || item.dates || []).join(', ') || 'Not available')}</p><p><strong>TAFB:</strong> ${esc(item.tafb || 'N/A')}</p>${pay.detail}${holdDetails(item)}${scheduleConflictDetails(item)}<p><strong>Conflicts:</strong> ${esc(conflicts.join('; ') || 'None')}</p></div></div><details class="timeline-details"><summary>Timeline and duty legs</summary>${timeline(item)}</details><details class="operating-cities"><summary>All operating cities</summary><p>${esc((item.touched_cities || []).join(', ') || 'Not available')}</p></details><details class="original-display"><summary>${esc(term.viewOriginal)}</summary><pre>${esc(item.original_display || 'Not available')}</pre></details></details></div>`;
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
  const eligible = allResults.filter(item => item.eligible !== false), near = allResults.filter(item => item.eligible === false);
  const shown = eligible.slice(0, count), nearShown = near.slice(0, Math.min(count, 25));
  appendResultCards(shown, $('results'), term);
  if (!shown.length) $('results').innerHTML = `<div class="empty-state">${near.length ? 'No trips met every hard requirement. Review Near Matches below.' : 'Your ranked results will appear here.'}</div>`;
  $('nearMatchesPanel').classList.toggle('hidden', !near.length);
  appendResultCards(nearShown, $('nearResults'), term);
  const top = shown[0] || nearShown[0];
  if (top) { const topPay = payPresentation(top), [topRating] = rating(top), [topFatigue] = fatigueRisk(top); $('summary').textContent = `${term.analyzed}: ${allResults.length} · ${eligible.length} eligible · ${near.length} near matches.`; $('snapshotMatch').textContent = topRating; $('snapshotPayLabel').textContent = topPay.snapshotLabel; $('snapshotCredit').textContent = topPay.snapshotValue || '—'; $('snapshotLength').textContent = top.trip_length ? `${top.trip_length}-day` : (top.duty_legs && top.duty_legs.length ? `${top.duty_legs.length}-day` : '—'); $('snapshotFatigue').textContent = topFatigue; }
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
function downloadDiagnostic() {
  if (!latestJob || !diagnosticPairingId) return;
  clearError();
  const form = document.createElement('form');
  form.method = 'POST'; form.action = `/api/jobs/${latestJob}/diagnostic.json`; form.target = '_blank'; form.hidden = true;
  const fields = { pairing_id: diagnosticPairingId, category: $('diagnosticCategory').value, notes: $('diagnosticNotes').value.trim() };
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
if (activeJob) { setJob(true, 'Resuming', 1, 'Reconnecting to your analysis…'); pollTimer = setInterval(pollJob, 1500); pollJob(); }
if (latestJob) { $('runPreferencesBtn').disabled = false; $('csvLink').href = `/api/jobs/${latestJob}/report.pdf`; $('csvLink').classList.remove('disabled'); setLabsContinuation(true); }
if (document.body.dataset.classicPage === 'results') loadLatestJob();
