const flightDeckContent = document.getElementById('flightDeckContent');
const flightDeckPage = window.CREWBIDIQ_FLIGHT_DECK_PAGE || 'results';
const requestedTripId = window.CREWBIDIQ_FLIGHT_DECK_TRIP_ID || '';
const latestJobKey = 'crewbidiqLatestJob';
const activeJobKey = 'crewbidiqActiveJob';
const activePackageKey = 'crewbidiqActivePackage';
const shortlistKey = 'crewbidiqShortlist';
const comparisonKey = 'crewbidiqComparison';
const packageStateKeys = [shortlistKey, comparisonKey, 'crewbidiqPbsPool', 'crewbidiqCommuteAssessments', 'crewbidiqExports'];

let sessionJob = null;
let sessionLoading = true;
let sessionError = '';
let filterState = {
  exactOnly: false,
  oneDay: false,
  twoDay: false,
  threeDay: false,
  fourDay: false,
  fivePlusDay: false,
  noRedeyes: false,
  oneLegPerDutyDay: false,
  twoLegsMaximum: false,
  preferredLayovers: false,
  savedTrips: false,
};
let sortMode = 'best';

const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, character => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
}[character]));

function readJson(key, fallback = null) {
  try { return JSON.parse(localStorage.getItem(key) || 'null') ?? fallback; }
  catch (_) { return fallback; }
}

function currentJobId() {
  return localStorage.getItem(activeJobKey) || localStorage.getItem(latestJobKey);
}

function activePackageId() {
  return localStorage.getItem(activePackageKey);
}

function clearPackageDependentState(nextPackageId = '') {
  packageStateKeys.forEach(key => localStorage.removeItem(key));
  if (nextPackageId) localStorage.setItem(activePackageKey, nextPackageId);
}

function acceptPackageResponse(body) {
  const incoming = body?.package_id || body?.package?.package_id;
  const expected = activePackageId();
  if (!incoming) throw new Error('The active bid package has no package identifier.');
  if (expected && incoming !== expected) throw new Error('Results from a replaced bid package were rejected.');
  const records = body.results || [];
  if (records.some(record => record.package_id !== incoming || !canonicalTrips(record).length || canonicalTrips(record).some(trip => trip.package_id !== incoming))) {
    throw new Error('Mixed-package results were rejected.');
  }
  if (!expected) localStorage.setItem(activePackageKey, incoming);
  return incoming;
}

function packageScopedIds(key) {
  const stored = readJson(key, null);
  if (!stored || stored.package_id !== activePackageId() || !Array.isArray(stored.trip_ids)) return [];
  return stored.trip_ids;
}

function savePackageScopedIds(key, tripIds) {
  const packageId = activePackageId();
  if (!packageId) return;
  localStorage.setItem(key, JSON.stringify({ package_id: packageId, trip_ids: [...new Set(tripIds)] }));
}

function togglePackageScopedId(key, tripId, maximum = Infinity) {
  const ids = packageScopedIds(key);
  const next = ids.includes(tripId) ? ids.filter(id => id !== tripId) : [...ids, tripId].slice(-maximum);
  savePackageScopedIds(key, next);
}

function canonicalTrips(item) { return item?.canonical_trip ? [item.canonical_trip] : (item?.canonical_trips || []); }
function tripModel(item) { return item?.canonical_trip || {}; }
function tripId(item) { return String(tripModel(item).id || item?.canonical_trip_id || item?.id || ''); }
function sourceNumber(item) { return String(tripModel(item).source_trip_number || item?.source_trip_number || item?.pairing || 'Unavailable'); }
function tripAirline(item) { return String(tripModel(item).airline || item?.airline || sessionJob?.airline || 'generic').toLowerCase(); }
function tripDayValues(item) {
  const values = item?.item_type === 'line'
    ? canonicalTrips(item).map(trip => Number(trip.trip_length_days || 0))
    : [Number(tripModel(item).trip_length_days ?? item?.trip_length_days ?? item?.trip_length ?? 0)];
  return [...new Set(values.filter(value => value > 0))];
}
function tripDays(item) { return tripDayValues(item)[0] || 0; }
function tripLengthLabel(item) {
  const values = tripDayValues(item);
  if (item?.item_type === 'line' && values.length > 1) return 'Mixed trips';
  const days = values[0];
  return days ? `${days} day${days === 1 ? '' : 's'}` : 'Unavailable';
}
function tripLegs(item) { return tripModel(item).ordered_legs || item?.ordered_legs || []; }
function tripDutyDays(item) { return tripModel(item).duty_days || item?.duty_days || []; }
function tripLayovers(item) { return tripModel(item).layovers || item?.layovers || []; }
function tripPay(item) { return tripModel(item).pay_breakdown || item?.pay_breakdown || {}; }
function tripTfp(item) { return tripModel(item).tfp || item?.tfp || {}; }
function tripTafb(item) { return tripModel(item).tafb ?? item?.tafb; }

function airlineName(airline) {
  return ({ delta: 'Delta Air Lines', american: 'American Airlines', southwest: 'Southwest Airlines', generic: 'Airline' })[airline] || airline || 'Airline';
}

function terminology(item) {
  if (tripAirline(item) === 'delta') return 'Rotation';
  if (tripAirline(item) === 'american') return 'Sequence';
  if (tripAirline(item) === 'southwest') return item?.item_type === 'line' ? 'Line' : 'Pairing';
  return 'Pairing';
}

function matchClass(item) {
  const value = String(item?.match_class || (item?.eligible === false ? 'near' : 'partial')).toLowerCase();
  return ['exact', 'strong', 'partial', 'near'].includes(value) ? value : 'partial';
}

function matchLabel(item) {
  return ({ exact: 'Exact Match', strong: 'Strong Match', partial: 'Partial Match', near: 'Near Match' })[matchClass(item)];
}

function simplifiedRoute(item) {
  const normalized = tripModel(item).simplified_route;
  if (normalized) return normalized;
  const legs = tripLegs(item);
  if (!legs.length) return item?.route || 'Route unavailable';
  return [legs[0].origin, ...legs.map(leg => leg.destination)].filter(Boolean).join('–');
}

function layoverAirport(layover) { return String(layover?.airport || layover?.station || layover?.city || '').toUpperCase(); }

function preferredAirports() {
  const profile = readJson('crewbidiqProfile', {}) || {};
  const draft = readJson('crewbidiqLabsDraft', {}) || {};
  const values = [profile.elite_cities, profile.secondary_cities, profile.preferred_layovers, draft.layovers];
  return new Set(values.flatMap(value => Array.isArray(value) ? value : String(value || '').split(','))
    .map(value => String(value).trim().toUpperCase()).filter(Boolean));
}

function priorityLayovers(item) {
  const preferred = preferredAirports();
  const all = tripLayovers(item).map(layoverAirport).filter(Boolean);
  const matches = all.filter(airport => preferred.has(airport));
  return matches.length ? matches : all.slice(0, 3);
}

function valueNumber(value) {
  if (value === null || value === undefined || value === '') return null;
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  const parsed = Number(String(value).replace(/[^0-9.-]/g, ''));
  return Number.isFinite(parsed) ? parsed : null;
}

function displayValue(value, fallback = 'Unavailable') {
  return value === null || value === undefined || value === '' ? fallback : String(value);
}

function eventFor(item, name) {
  const model = tripModel(item);
  return model[name] || item?.[name] || {};
}

function eventTime(item, name) {
  const event = eventFor(item, name);
  return displayValue(event.local_time || event.local_event_timestamp || event.time || item?.[`${name}_time`]);
}

function clockMinutes(value) {
  const match = String(value || '').match(/(?:T|\s|^)(\d{1,2}):?(\d{2})(?:\D|$)/);
  return match ? Number(match[1]) * 60 + Number(match[2]) : Number.MAX_SAFE_INTEGER;
}

function hasRedeye(item) {
  const model = tripModel(item);
  return Number(item?.redeye_count || model.raw_source_fields?.redeye_count || 0) > 0 || item?.has_redeye === true || item?.redeye === true;
}

function maximumLegsPerDutyDay(item) {
  const duties = tripDutyDays(item);
  if (duties.length) return Math.max(...duties.map(day => (day.ordered_legs || []).length), 0);
  const dutyCount = Number(tripModel(item).duty_period_count || item?.duty_period_count || tripDays(item) || 1);
  return Math.ceil(tripLegs(item).length / dutyCount);
}

function resultRecords() {
  if (!sessionJob || sessionJob.status !== 'complete') return [];
  const packageId = activePackageId();
  return (sessionJob.results || []).filter(item => {
    const canonical = canonicalTrips(item);
    const packageConfirmed = item.package_id === packageId && canonical.length > 0 && canonical.every(trip => trip.package_id === packageId);
    const inventoryConfirmed = item.item_type === 'line'
      ? canonical.every(trip => trip.bidable_inventory_confirmed === true)
      : item.bidable_inventory_confirmed === true && tripModel(item).bidable_inventory_confirmed === true;
    return packageConfirmed && inventoryConfirmed;
  });
}

function filteredResults(records) {
  const saved = new Set(packageScopedIds(shortlistKey));
  const dayFilters = [filterState.oneDay, filterState.twoDay, filterState.threeDay, filterState.fourDay, filterState.fivePlusDay];
  return records.filter(item => {
    const dayValues = tripDayValues(item);
    if (filterState.exactOnly && matchClass(item) !== 'exact') return false;
    if (dayFilters.some(Boolean) && !(
      (filterState.oneDay && dayValues.includes(1)) || (filterState.twoDay && dayValues.includes(2)) ||
      (filterState.threeDay && dayValues.includes(3)) || (filterState.fourDay && dayValues.includes(4)) ||
      (filterState.fivePlusDay && dayValues.some(days => days >= 5))
    )) return false;
    if (filterState.noRedeyes && hasRedeye(item)) return false;
    if (filterState.oneLegPerDutyDay && maximumLegsPerDutyDay(item) > 1) return false;
    if (filterState.twoLegsMaximum && tripLegs(item).length > 2) return false;
    if (filterState.preferredLayovers && !tripLayovers(item).some(layover => preferredAirports().has(layoverAirport(layover)))) return false;
    if (filterState.savedTrips && !saved.has(tripId(item))) return false;
    return true;
  });
}

function sortResults(records) {
  const copy = [...records];
  const numeric = (item, mode) => {
    const pay = tripPay(item), tfp = tripTfp(item);
    if (mode === 'length') return tripDays(item);
    if (mode === 'tafb') return valueNumber(tripTafb(item)) ?? Number.MAX_SAFE_INTEGER;
    if (mode === 'totalPay') return -(valueNumber(pay.total_pay) ?? -Number.MAX_SAFE_INTEGER);
    if (mode === 'tripCredit') return -(valueNumber(pay.trip_credit) ?? -Number.MAX_SAFE_INTEGER);
    if (mode === 'tfp') return -(valueNumber(tfp.pairing_tfp ?? item?.line_tfp) ?? -Number.MAX_SAFE_INTEGER);
    if (mode === 'report') return clockMinutes(eventTime(item, 'report'));
    if (mode === 'release') return clockMinutes(eventTime(item, 'release'));
    if (mode === 'layovers') return -priorityLayovers(item).filter(airport => preferredAirports().has(airport)).length;
    return -(valueNumber(item.ranking_score ?? item.score) ?? -Number.MAX_SAFE_INTEGER);
  };
  return copy.sort((left, right) => numeric(left, sortMode) - numeric(right, sortMode));
}

function sortOptions() {
  const airline = String(sessionJob?.airline || '').toLowerCase();
  const options = [
    ['best', 'Best Match'], ['length', 'Trip Length'], ['tafb', 'TAFB'],
    ['report', 'Report Time'], ['release', 'Release Time'], ['layovers', 'Preferred Layovers'],
  ];
  if (airline === 'delta') options.splice(3, 0, ['totalPay', 'Total Pay'], ['tripCredit', 'Trip Credit']);
  if (airline === 'american' || airline === 'generic') options.splice(3, 0, ['tripCredit', 'Trip Credit']);
  if (airline === 'southwest') options.splice(3, 0, ['tfp', 'TFP']);
  return options.map(([value, label]) => `<option value="${value}" ${sortMode === value ? 'selected' : ''}>${label}</option>`).join('');
}

function packageSummary() {
  const metadata = sessionJob?.package || {};
  return `<div class="fd-package-summary"><span>${escapeHtml(airlineName(metadata.airline || sessionJob?.airline))}</span><strong>${escapeHtml(metadata.base || 'Base unavailable')} · ${escapeHtml(metadata.fleet_category || metadata.fleet || 'Fleet unavailable')}</strong><small>${escapeHtml(metadata.bid_month || 'Bid month unavailable')}</small></div>`;
}

function metric(label, value, className = '') {
  return `<div class="fd-metric ${className}"><span>${escapeHtml(label)}</span><strong>${escapeHtml(displayValue(value))}</strong></div>`;
}

function airlinePayMetrics(item) {
  const airline = tripAirline(item), pay = tripPay(item), tfp = tripTfp(item);
  const metrics = [];
  if (airline === 'delta' && pay.total_pay !== null && pay.total_pay !== undefined) metrics.push(metric('Total Pay', pay.total_pay, 'fd-pay-primary'));
  if (airline !== 'southwest' && pay.trip_credit !== null && pay.trip_credit !== undefined) metrics.push(metric('Trip Credit', pay.trip_credit));
  if (airline === 'southwest') metrics.push(metric('TFP', tfp.pairing_tfp ?? item?.line_tfp));
  return metrics.join('');
}

function resultCard(item, rank) {
  const id = tripId(item);
  const shortlisted = packageScopedIds(shortlistKey).includes(id);
  const comparing = packageScopedIds(comparisonKey).includes(id);
  const layovers = priorityLayovers(item);
  const reasons = matchClass(item) === 'near'
    ? (item.eligibility_violations || item.hard_failures || item.violations || item.compromises || [])
    : (item.qualification_reasons || item.matched_preferences || []);
  const visibleReasons = matchClass(item) === 'near' ? reasons : reasons.slice(0, 2);
  return `<article class="fd-card fd-${matchClass(item)}" data-trip-id="${escapeHtml(id)}">
    <div class="fd-rank" aria-label="Rank ${rank}">${rank}</div>
    <div class="fd-card-content">
      <header><div><span class="fd-identifier-label">${escapeHtml(terminology(item))}</span><h3>${escapeHtml(sourceNumber(item))}</h3></div><span class="fd-match fd-match-${matchClass(item)}">${escapeHtml(matchLabel(item))}</span></header>
      <p class="fd-route">${escapeHtml(simplifiedRoute(item))}</p>
      <div class="fd-metrics">
        ${metric('Trip Length', tripLengthLabel(item))}
        ${airlinePayMetrics(item)}
        ${metric('TAFB', tripTafb(item))}
        ${metric('Report', eventTime(item, 'report'))}
        ${metric('Release', eventTime(item, 'release'))}
      </div>
      <div class="fd-card-detail"><span>Priority layovers</span><strong>${escapeHtml(layovers.join(' · ') || 'None parsed')}</strong></div>
      ${visibleReasons.length ? `<p class="fd-reason">${escapeHtml(visibleReasons.join(' · '))}</p>` : ''}
      <footer class="fd-card-actions">
        <button type="button" data-action="shortlist" data-trip-id="${escapeHtml(id)}" aria-pressed="${shortlisted}">${shortlisted ? 'Shortlisted' : 'Shortlist'}</button>
        <button type="button" data-action="compare" data-trip-id="${escapeHtml(id)}" aria-pressed="${comparing}">${comparing ? 'In Compare' : 'Compare'}</button>
        <a class="primary button" href="/labs/flight-deck/trip/${encodeURIComponent(id)}">Open Trip Briefing</a>
      </footer>
    </div>
  </article>`;
}

function filtersPanel() {
  const filters = [
    ['exactOnly', 'Exact Matches only'], ['oneDay', '1-day'], ['twoDay', '2-day'], ['threeDay', '3-day'],
    ['fourDay', '4-day'], ['fivePlusDay', '5+ day'], ['noRedeyes', 'No redeyes'],
    ['oneLegPerDutyDay', 'One leg per duty day'], ['twoLegsMaximum', 'Two legs maximum'],
    ['preferredLayovers', 'Preferred layovers'], ['savedTrips', 'Saved trips'],
  ];
  return `<aside class="surface fd-filters"><div class="fd-filter-title"><h2>Filters</h2><button type="button" data-action="clear-filters">Clear</button></div>
    <div class="fd-filter-list">${filters.map(([key, label]) => `<label><input type="checkbox" data-filter="${key}" ${filterState[key] ? 'checked' : ''}><span>${label}</span></label>`).join('')}</div>
  </aside>`;
}

function resultsPage() {
  const records = sortResults(filteredResults(resultRecords()));
  const groups = [
    ['exact', 'Exact Matches'], ['strong', 'Strong Matches'], ['partial', 'Partial Matches'], ['near', 'Near Matches'],
  ];
  let rank = 0;
  const sections = groups.map(([key, label]) => {
    const members = records.filter(item => matchClass(item) === key);
    if (!members.length) return '';
    const cards = members.map(item => resultCard(item, ++rank)).join('');
    return `<section class="fd-match-section" data-match-section="${key}"><div class="fd-section-heading"><h2>${label}</h2><span>${members.length}</span></div>${cards}</section>`;
  }).join('');
  return `${pageHero('RECOMMENDATIONS', 'Flight Deck Preview', 'Strict eligibility first, then ranked results from the active bid package.')}
    <div class="fd-toolbar">${packageSummary()}<label>Sort by<select id="flightDeckSort">${sortOptions()}</select></label></div>
    <div class="fd-workspace">${filtersPanel()}<div class="fd-results-scroll">${sections || emptyState('No trips match these filters', 'Clear filters or review Near Matches from this package.')}</div></div>
    ${selectionDock()}`;
}

function pageHero(kicker, title, description) {
  return `<section class="fd-hero"><div><span class="kicker">${kicker}</span><h1>${escapeHtml(title)}</h1><p>${escapeHtml(description)}</p></div><a class="text-button button" href="/labs">Labs home</a></section>`;
}

function emptyState(title, description) {
  return `<section class="surface fd-empty"><h2>${escapeHtml(title)}</h2><p>${escapeHtml(description)}</p></section>`;
}

function selectionDock() {
  const shortlistCount = packageScopedIds(shortlistKey).length;
  const compareCount = packageScopedIds(comparisonKey).length;
  return `<div class="fd-selection-dock"><a href="/labs/flight-deck/shortlist">Shortlist <strong>${shortlistCount}</strong></a><a href="/labs/flight-deck/compare">Compare <strong>${compareCount}</strong></a></div>`;
}

function shortlistPage() {
  const ids = new Set(packageScopedIds(shortlistKey));
  const records = resultRecords().filter(item => ids.has(tripId(item)));
  return `${pageHero('SAVED TRIPS', 'Shortlist', 'Saved trips remain scoped to this active bid package.')}${packageSummary()}
    <section class="fd-saved-list">${records.length ? records.map((item, index) => resultCard(item, index + 1)).join('') : emptyState('Your shortlist is empty', 'Add trips from Flight Deck results to keep them here.')}</section>${selectionDock()}`;
}

function comparePage() {
  const ids = new Set(packageScopedIds(comparisonKey));
  const records = resultRecords().filter(item => ids.has(tripId(item)));
  const cards = records.map(item => `<article class="surface fd-compare-card"><span>${escapeHtml(terminology(item))}</span><h2>${escapeHtml(sourceNumber(item))}</h2><p class="fd-route">${escapeHtml(simplifiedRoute(item))}</p><div class="fd-compare-metrics">${metric('Match', matchLabel(item))}${metric('Trip Length', tripLengthLabel(item))}${airlinePayMetrics(item)}${metric('TAFB', tripTafb(item))}${metric('Report', eventTime(item, 'report'))}${metric('Release', eventTime(item, 'release'))}</div><button type="button" data-action="compare" data-trip-id="${escapeHtml(tripId(item))}">Remove</button></article>`).join('');
  return `${pageHero('SIDE BY SIDE', 'Compare Trips', 'Compare normalized trip facts from one active bid package.')}${packageSummary()}
    <section class="fd-compare-grid">${cards || emptyState('No trips selected', 'Choose up to four trips from Flight Deck results.')}</section>${selectionDock()}`;
}

function tripBriefingPage() {
  const item = resultRecords().find(record => tripId(record) === requestedTripId || sourceNumber(record) === requestedTripId);
  if (!item) return `${pageHero('TRIP BRIEFING', 'Trip unavailable', 'This trip does not belong to the active bid package or is no longer available.')}<a class="primary button" href="/labs/flight-deck">Return to results</a>`;
  const legs = tripLegs(item);
  const layovers = tripLayovers(item);
  const explanation = [
    ...(item.qualification_reasons || []), ...(item.matched_preferences || []),
    ...(item.compromises || []), ...(item.eligibility_violations || item.hard_failures || item.violations || []),
  ];
  return `${pageHero('TRIP BRIEFING', `${terminology(item)} ${sourceNumber(item)}`, simplifiedRoute(item))}
    <section class="fd-briefing-grid">
      <article class="surface"><h2>Overview</h2><div class="fd-compare-metrics">${metric('Match', matchLabel(item))}${metric('Trip Length', tripLengthLabel(item))}${airlinePayMetrics(item)}${metric('TAFB', tripTafb(item))}${metric('Report', eventTime(item, 'report'))}${metric('Release', eventTime(item, 'release'))}</div></article>
      <article class="surface"><h2>Why it appears</h2>${explanation.length ? `<ul>${[...new Set(explanation)].map(reason => `<li>${escapeHtml(reason)}</li>`).join('')}</ul>` : '<p>It passed inventory and package checks for the active package.</p>'}</article>
    </section>
    <section class="surface fd-flow"><h2>Trip Flow</h2>${legs.length ? `<ol>${legs.map((leg, index) => `<li><span>${index + 1}</span><div><strong>${escapeHtml(leg.origin || '—')} → ${escapeHtml(leg.destination || '—')}</strong><small>${escapeHtml(displayValue(leg.local_departure_time))} – ${escapeHtml(displayValue(leg.local_arrival_time))}${leg.operating_or_deadhead === 'deadhead' ? ' · Deadhead' : ''}</small></div></li>`).join('')}</ol>` : '<p>Detailed leg times are unavailable.</p>'}</section>
    <section class="surface fd-layover-list"><h2>Layovers</h2>${layovers.length ? layovers.map(layover => `<article><strong>${escapeHtml(layoverAirport(layover))}</strong><span>${escapeHtml(displayValue(layover.duration))}</span><small>${escapeHtml(layover.hotel || 'Hotel unavailable')}${layover.transportation ? ` · ${escapeHtml(layover.transportation)}` : ''}</small></article>`).join('') : '<p>No validated layovers are available.</p>'}</section>
    <div class="fd-briefing-actions"><button type="button" data-action="shortlist" data-trip-id="${escapeHtml(tripId(item))}">Toggle Shortlist</button><button type="button" data-action="compare" data-trip-id="${escapeHtml(tripId(item))}">Toggle Compare</button><a class="primary button" href="/labs/flight-deck">Back to results</a></div>${selectionDock()}`;
}

function noPackagePage() {
  return `${pageHero('FLIGHT DECK PREVIEW', 'Upload a bid package to begin', 'Flight Deck uses the same upload and active package as Classic and Labs.')}
    <section class="surface fd-empty"><h2>No active package</h2><p>Upload once in Labs, or return to a package already analyzed in Classic.</p><a class="primary button" href="/labs#labsUpload">Upload Bid Package</a><a class="text-button button" href="/">Use Classic</a></section>`;
}

function loadingPage() {
  return `${pageHero('FLIGHT DECK PREVIEW', 'Preparing recommendations', sessionJob?.message || 'Opening the active bid package.')}
    <section class="surface labs-loading"><strong>${escapeHtml(sessionJob?.stage_label || 'Loading package...')}</strong><p>${escapeHtml(sessionJob?.progress ?? 0)}%</p></section>`;
}

function render() {
  if (sessionLoading) flightDeckContent.innerHTML = loadingPage();
  else if (sessionError) flightDeckContent.innerHTML = `${pageHero('FLIGHT DECK PREVIEW', 'Package unavailable', sessionError)}<a class="primary button" href="/labs">Open Labs</a>`;
  else if (!sessionJob) flightDeckContent.innerHTML = noPackagePage();
  else if (sessionJob.status !== 'complete') flightDeckContent.innerHTML = loadingPage();
  else {
    const pages = { results: resultsPage, shortlist: shortlistPage, compare: comparePage, trip: tripBriefingPage };
    flightDeckContent.innerHTML = (pages[flightDeckPage] || resultsPage)();
  }
  document.querySelectorAll('[data-flight-deck-route]').forEach(link => link.classList.toggle('active', link.dataset.flightDeckRoute === flightDeckPage));
  bindControls();
}

function bindControls() {
  document.getElementById('flightDeckSort')?.addEventListener('change', event => { sortMode = event.target.value; render(); });
  document.querySelectorAll('[data-filter]').forEach(input => input.addEventListener('change', event => {
    filterState[event.target.dataset.filter] = event.target.checked;
    render();
  }));
  document.querySelectorAll('[data-action]').forEach(control => control.addEventListener('click', event => {
    const action = event.currentTarget.dataset.action;
    if (action === 'clear-filters') filterState = Object.fromEntries(Object.keys(filterState).map(key => [key, false]));
    if (action === 'shortlist') togglePackageScopedId(shortlistKey, event.currentTarget.dataset.tripId);
    if (action === 'compare') togglePackageScopedId(comparisonKey, event.currentTarget.dataset.tripId, 4);
    render();
  }));
}

async function loadSharedSession() {
  const jobId = currentJobId();
  if (!jobId) { sessionJob = null; sessionLoading = false; sessionError = ''; render(); return; }
  try {
    const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`);
    if (!response.ok) throw new Error('The active package could not be loaded.');
    const body = await response.json();
    acceptPackageResponse(body);
    sessionJob = body; sessionError = ''; sessionLoading = false; render();
    if (body.status === 'queued' || body.status === 'processing') setTimeout(loadSharedSession, 2000);
  } catch (error) {
    sessionLoading = false; sessionError = error.message || 'The active package could not be loaded.'; render();
  }
}

function applyTheme() {
  const theme = localStorage.getItem('crewbidiqTheme') || 'dark';
  document.documentElement.dataset.theme = theme;
}

document.getElementById('flightDeckTheme')?.addEventListener('click', () => {
  const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  localStorage.setItem('crewbidiqTheme', next); applyTheme();
});

window.addEventListener('storage', event => {
  if (![latestJobKey, activeJobKey, activePackageKey].includes(event.key)) return;
  if (event.key === activePackageKey && event.oldValue && event.newValue !== event.oldValue) clearPackageDependentState(event.newValue || '');
  sessionJob = null; sessionLoading = true; sessionError = ''; render(); loadSharedSession();
});

applyTheme();
render();
loadSharedSession();
