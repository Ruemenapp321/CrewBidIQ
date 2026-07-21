const flightDeckContent = document.getElementById('flightDeckContent');
const flightDeckPage = window.CREWBIDIQ_FLIGHT_DECK_PAGE || 'results';
const requestedTripId = window.CREWBIDIQ_FLIGHT_DECK_TRIP_ID || '';
const latestJobKey = 'crewbidiqLatestJob';
const activeJobKey = 'crewbidiqActiveJob';
const activePackageKey = 'crewbidiqActivePackage';
const analysisSessionKey = 'crewbidiqAnalysisSession';
const shortlistKey = 'crewbidiqShortlist';
const comparisonKey = 'crewbidiqComparison';
const flightDeckSessionKey = 'crewbidiqFlightDeckSession';
const packageStateKeys = [shortlistKey, comparisonKey, 'crewbidiqPbsPool', 'crewbidiqCommuteAssessments', 'crewbidiqExports'];

let sessionJob = null;
let sessionLoading = true;
let sessionError = '';
let selectedMapDay = 'all';
let flightDeckMap = null;
let flightDeckMapLayers = null;
let renderedBriefingModels = [];
let sharedSessionController = null;
let sharedSessionTimer = null;
let sharedSessionInFlight = false;
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
let selectionNotice = '';
function browserSessionId() { let value = localStorage.getItem(analysisSessionKey); if (!value) { value = globalThis.crypto?.randomUUID?.() || `session-${Date.now()}-${Math.random().toString(16).slice(2)}`; safeLocalStorageSetItem(analysisSessionKey, value); } return value; }
function isQuotaExceededError(error) { return Boolean(error && (error.name === 'QuotaExceededError' || error.code === 22 || error.code === 1014)); }
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
function safeLocalStorageRemoveItem(key) { try { localStorage.removeItem(key); } catch (_) {} }

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

function flightDeckSessionId() {
  let value = localStorage.getItem('crewbidiqAnalysisSession') || localStorage.getItem(flightDeckSessionKey);
  if (!value) {
    value = globalThis.crypto?.randomUUID?.() || `flight-deck-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    safeLocalStorageSetItem(flightDeckSessionKey, value);
  }
  return value;
}

function clearPackageDependentState(nextPackageId = '') {
  packageStateKeys.forEach(key => safeLocalStorageRemoveItem(key));
  if (nextPackageId) safeLocalStorageSetItem(activePackageKey, nextPackageId);
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
  if (!expected) safeLocalStorageSetItem(activePackageKey, incoming);
  return incoming;
}

function packageScopedIds(key) {
  const stored = readJson(key, null);
  const expectedPackage = activePackageId(), expectedSession = flightDeckSessionId();
  if (!stored) return [];
  if (stored.package_id !== expectedPackage || stored.session_id !== expectedSession || !Array.isArray(stored.trip_ids)) {
    safeLocalStorageRemoveItem(key);
    return [];
  }
  return [...new Set(stored.trip_ids.map(value => String(value || '')).filter(Boolean))];
}

function savePackageScopedIds(key, tripIds) {
  const packageId = activePackageId();
  if (!packageId) return;
  safeLocalStorageSetItem(key, JSON.stringify({
    package_id: packageId,
    session_id: flightDeckSessionId(),
    trip_ids: [...new Set(tripIds)],
    updated_at: new Date().toISOString(),
  }));
}

function togglePackageScopedId(key, tripId, maximum = Infinity) {
  if (!resultRecords().some(item => tripId === tripIdForRecord(item))) return false;
  const ids = packageScopedIds(key);
  if (!ids.includes(tripId) && ids.length >= maximum) {
    selectionNotice = `Compare supports up to ${maximum} trips. Remove one before adding another.`;
    return false;
  }
  const next = ids.includes(tripId) ? ids.filter(id => id !== tripId) : [...ids, tripId];
  savePackageScopedIds(key, next);
  selectionNotice = '';
  return true;
}

function tripIdForRecord(item) { return tripId(item); }

function movePackageScopedId(key, tripIdValue, direction) {
  const ids = packageScopedIds(key), index = ids.indexOf(tripIdValue), target = index + direction;
  if (index < 0 || target < 0 || target >= ids.length) return false;
  [ids[index], ids[target]] = [ids[target], ids[index]];
  savePackageScopedIds(key, ids);
  return true;
}

function removePackageScopedId(key, tripIdValue) {
  const ids = packageScopedIds(key);
  if (!ids.includes(tripIdValue)) return false;
  savePackageScopedIds(key, ids.filter(id => id !== tripIdValue));
  selectionNotice = '';
  return true;
}

function canonicalTrips(item) { return item?.canonical_trip ? [item.canonical_trip] : (item?.canonical_trips || []); }
function tripModel(item) { return item?.canonical_trip || {}; }
function tripId(item) { return String(tripModel(item).id || item?.canonical_trip_id || item?.id || ''); }
function sourceNumber(item) { return String(tripModel(item).source_trip_number || item?.source_trip_number || item?.pairing || 'Unavailable'); }
function tripAirline(item) { return String(tripModel(item).airline || item?.airline || sessionJob?.airline || 'generic').toLowerCase(); }
function canonicalTripFacts(item) {
  const model = tripModel(item);
  return {
    orderedLegs: Array.isArray(model.ordered_legs) ? model.ordered_legs : [],
    dutyDays: Array.isArray(model.duty_days) ? model.duty_days : [],
    layovers: Array.isArray(model.layovers) ? model.layovers : [],
    mapAirports: Array.isArray(model.route_map_airports) ? model.route_map_airports : [],
  };
}
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
function tripLegs(item) { return canonicalTripFacts(item).orderedLegs; }
function tripDutyDays(item) { return canonicalTripFacts(item).dutyDays; }
function tripLayovers(item) { return canonicalTripFacts(item).layovers; }
function tripMapAirports(item) { return canonicalTripFacts(item).mapAirports; }
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
  return tripModel(item).simplified_route || 'Route unavailable';
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

function comparisonModels(item) { return canonicalTrips(item).filter(model => model && model.package_id === activePackageId()); }
function comparisonLegs(item) { return comparisonModels(item).flatMap(model => Array.isArray(model.ordered_legs) ? model.ordered_legs : []); }
function comparisonDutyDays(item) { return comparisonModels(item).flatMap(model => Array.isArray(model.duty_days) ? model.duty_days : []); }
function comparisonLayovers(item) { return comparisonModels(item).flatMap(model => Array.isArray(model.layovers) ? model.layovers : []); }
function comparisonDutyPeriodCount(item) {
  const models = comparisonModels(item);
  return models.reduce((total, model) => total + Number(model.duty_period_count || (model.duty_days || []).length || 0), 0) || null;
}
function comparisonMaximumLegs(item) {
  const duties = comparisonDutyDays(item);
  return duties.length ? Math.max(...duties.map(day => (day.ordered_legs || []).length), 0) : maximumLegsPerDutyDay(item);
}
function comparisonDeadheads(item) { return comparisonLegs(item).filter(leg => String(leg.operating_or_deadhead || '').toLowerCase() === 'deadhead').length; }
function comparisonLayoverLabel(item) {
  const airports = comparisonLayovers(item).map(layoverAirport).filter(Boolean);
  return airports.length ? `${airports.length} · ${airports.join(' · ')}` : 'None';
}
function preferredDestinations(item) {
  const preferred = preferredAirports();
  const destinations = comparisonLegs(item).map(leg => String(leg.destination || '').toUpperCase()).filter(Boolean);
  return [...new Set(destinations.filter(airport => preferred.has(airport)))];
}
function savedProfile() {
  const classic = readJson('crewbidiqProfile', {}) || {}, draft = readJson('crewbidiqLabsDraft', {}) || {};
  const split = value => String(value || '').split(',').map(item => item.trim()).filter(Boolean);
  const minutes = value => { if (!value) return null; const [hours, mins] = String(value).split(':').map(Number); return hours * 60 + mins; };
  return {
    ...classic,
    ...(draft.interpretedProfile || {}),
    trip_length_priority: draft.tripLengths ? split(draft.tripLengths) : (classic.trip_length_priority || classic.preferred_trip_lengths || []),
    preferred_trip_lengths: draft.tripLengths ? split(draft.tripLengths) : (classic.preferred_trip_lengths || []),
    max_legs_per_day: draft.maxLegs || classic.max_legs_per_day,
    earliest_report_minutes: draft.earliestReport ? minutes(draft.earliestReport) : (classic.earliest_report_minutes ?? null),
    latest_release_minutes: draft.latestRelease ? minutes(draft.latestRelease) : (classic.latest_release_minutes ?? null),
  };
}
function preferredTripLengths() {
  const profile = savedProfile();
  const values = profile.trip_length_priority || profile.preferred_trip_lengths || [];
  return new Set((Array.isArray(values) ? values : String(values).split(',')).map(Number).filter(value => value > 0));
}
function comparisonPriorityKeys() {
  const profile = savedProfile(), raw = profile.priority_order || [];
  const values = (Array.isArray(raw) ? raw : String(raw).split(',')).map(value => String(value).trim().toLowerCase().replace(/[^a-z0-9]+/g, '_')).filter(Boolean);
  if (profile.pay_priority) values.push('pay');
  if (preferredTripLengths().size) values.push('trip_length');
  if (preferredAirports().size) values.push('preferred_destinations');
  if (profile.max_legs_per_day != null) values.push('maximum_legs');
  if (profile.earliest_report_minutes != null) values.push('report');
  if (profile.latest_release_minutes != null) values.push('release');
  return new Set(values);
}
function durationMinutes(value) {
  const match = String(value ?? '').trim().match(/^(\d+)(?::|\.)(\d{1,2})$/);
  if (match) return Number(match[1]) * 60 + Number(match[2]);
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}
function selectedPayMetric(item) {
  const airline = tripAirline(item), profile = savedProfile(), pay = tripPay(item), tfp = tripTfp(item);
  if (airline === 'southwest') return { label: 'TFP', value: tfp.pairing_tfp ?? item?.line_tfp };
  if (profile.pay_priority === 'trip_credit') return { label: 'Trip Credit', value: pay.trip_credit };
  if (airline === 'delta' || pay.total_pay !== null && pay.total_pay !== undefined) return { label: 'Total Pay', value: pay.total_pay };
  return { label: 'Trip Credit', value: pay.trip_credit };
}
function comparisonStrengths(item, records) {
  const strengths = [], profile = savedProfile(), priorities = comparisonPriorityKeys(), days = tripDayValues(item), preferredLengths = preferredTripLengths();
  if (matchClass(item) === 'exact') strengths.push('Meets every hard requirement');
  if (days.some(day => preferredLengths.has(day))) strengths.push('Matches a preferred trip length');
  const destinations = preferredDestinations(item);
  if (destinations.length) strengths.push(`Includes preferred ${destinations.join(' · ')}`);
  const maximum = comparisonMaximumLegs(item);
  if (profile.max_legs_per_day != null && maximum <= Number(profile.max_legs_per_day)) strengths.push(`Within ${profile.max_legs_per_day} legs per duty day`);
  const report = clockMinutes(eventTime(item, 'report'));
  if (profile.earliest_report_minutes != null && report >= Number(profile.earliest_report_minutes)) strengths.push('Meets the selected report-time preference');
  const release = clockMinutes(eventTime(item, 'release'));
  if (profile.latest_release_minutes != null && release <= Number(profile.latest_release_minutes)) strengths.push('Meets the selected release-time preference');
  if (profile.pay_priority) {
    const selected = selectedPayMetric(item), value = durationMinutes(selected.value);
    const values = records.map(record => durationMinutes(selectedPayMetric(record).value)).filter(candidate => candidate !== null);
    if (value !== null && values.length > 1 && value === Math.max(...values)) strengths.push(`Highest selected ${selected.label}`);
  }
  const lowestSelected = (key, label, getter) => {
    if (!priorities.has(key)) return;
    const value = getter(item), values = records.map(getter).filter(candidate => candidate !== null && Number.isFinite(candidate));
    if (value !== null && values.length > 1 && value === Math.min(...values)) strengths.push(`Lowest selected ${label}`);
  };
  lowestSelected('tafb', 'TAFB', record => durationMinutes(tripTafb(record)));
  lowestSelected('duty_periods', 'duty-period count', record => comparisonDutyPeriodCount(record));
  lowestSelected('total_legs', 'total-leg count', record => comparisonLegs(record).length);
  lowestSelected('deadheads', 'deadhead count', record => comparisonDeadheads(record));
  return [...new Set(strengths)];
}

function recordsForStoredIds(key) {
  const records = new Map(resultRecords().map(item => [tripId(item), item]));
  const ids = packageScopedIds(key), selected = ids.map(id => records.get(id)).filter(Boolean);
  const validIds = selected.map(item => tripId(item));
  if (validIds.length !== ids.length) savePackageScopedIds(key, validIds);
  return selected;
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

function airlinePayMetrics(item, highlight = false) {
  const airline = tripAirline(item), pay = tripPay(item), tfp = tripTfp(item);
  const metrics = [], priorityClass = highlight ? 'fd-priority-metric' : '';
  if (airline === 'delta' && pay.total_pay !== null && pay.total_pay !== undefined) metrics.push(metric('Total Pay', pay.total_pay, `fd-pay-primary ${priorityClass}`));
  if (airline === 'american' && pay.total_pay !== null && pay.total_pay !== undefined) metrics.push(metric('Total Pay', pay.total_pay, `fd-pay-primary ${priorityClass}`));
  if (airline !== 'southwest' && pay.trip_credit !== null && pay.trip_credit !== undefined) metrics.push(metric('Trip Credit', pay.trip_credit, priorityClass));
  if (airline === 'southwest') metrics.push(metric('TFP', tfp.pairing_tfp ?? item?.line_tfp, priorityClass));
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
  const shortlistCount = recordsForStoredIds(shortlistKey).length;
  const compareCount = recordsForStoredIds(comparisonKey).length;
  return `<div class="fd-selection-dock"><a href="/labs/flight-deck/shortlist">Shortlist <strong>${shortlistCount}</strong></a><a href="/labs/flight-deck/compare">Compare <strong>${compareCount}</strong></a></div>`;
}

function shortlistPage() {
  const records = recordsForStoredIds(shortlistKey);
  const saved = records.map((item, index) => `<div class="fd-shortlist-item" data-shortlist-position="${index + 1}">
    <div class="fd-shortlist-order"><span>Position ${index + 1}</span><button type="button" data-action="shortlist-up" data-trip-id="${escapeHtml(tripId(item))}" ${index === 0 ? 'disabled' : ''}>Move up</button><button type="button" data-action="shortlist-down" data-trip-id="${escapeHtml(tripId(item))}" ${index === records.length - 1 ? 'disabled' : ''}>Move down</button><button type="button" data-action="shortlist-remove" data-trip-id="${escapeHtml(tripId(item))}">Remove</button></div>
    ${resultCard(item, index + 1)}
  </div>`).join('');
  return `${pageHero('SAVED TRIPS', 'Shortlist', 'Saved trips remain scoped to this active bid package.')}${packageSummary()}
    <section class="fd-saved-list">${saved || emptyState('Your shortlist is empty', 'Add trips from Flight Deck results to keep them here.')}</section>${selectionDock()}`;
}

function comparisonMetric(label, value, highlighted = false) { return metric(label, value, highlighted ? 'fd-priority-metric' : ''); }
function comparisonDetail(label, values, emptyLabel) {
  const details = uniqueDetails(values);
  return `<div class="fd-compare-detail"><h3>${escapeHtml(label)}</h3>${details.length ? `<ul>${details.map(value => `<li>${escapeHtml(value)}</li>`).join('')}</ul>` : `<p>${escapeHtml(emptyLabel)}</p>`}</div>`;
}
function assessmentValue(value, placeholder) {
  if (!value) return placeholder;
  if (typeof value === 'string') return value;
  return value.level || value.label || value.summary || placeholder;
}
function compareCard(item, records) {
  const profile = savedProfile(), priorities = comparisonPriorityKeys(), preferredLengths = preferredTripLengths(), strengths = comparisonStrengths(item, records);
  const matched = uniqueDetails(item.matched_preferences || []), compromises = uniqueDetails(item.compromises || []);
  const destinations = preferredDestinations(item), totalLegs = comparisonLegs(item).length, maximumLegs = comparisonMaximumLegs(item);
  const maximumMeets = profile.max_legs_per_day != null && maximumLegs <= Number(profile.max_legs_per_day);
  const reportMeets = profile.earliest_report_minutes != null && clockMinutes(eventTime(item, 'report')) >= Number(profile.earliest_report_minutes);
  const releaseMeets = profile.latest_release_minutes != null && clockMinutes(eventTime(item, 'release')) <= Number(profile.latest_release_minutes);
  return `<article class="surface fd-compare-card" data-compare-trip-id="${escapeHtml(tripId(item))}">
    <header><div><span>${escapeHtml(terminology(item))}</span><h2>${escapeHtml(sourceNumber(item))}</h2><p class="fd-route">${escapeHtml(simplifiedRoute(item))}</p></div><span class="fd-match fd-match-${matchClass(item)}">${escapeHtml(matchLabel(item))}</span></header>
    ${strengths.length ? `<div class="fd-priority-strengths"><strong>Strengths for your priorities</strong>${strengths.map(value => `<span>${escapeHtml(value)}</span>`).join('')}</div>` : '<p class="fd-neutral-note">No additional saved-priority strengths were identified.</p>'}
    <div class="fd-compare-metrics">
      ${comparisonMetric('Match Class', matchLabel(item), matchClass(item) === 'exact')}
      ${comparisonMetric('Trip Length', tripLengthLabel(item), tripDayValues(item).some(day => preferredLengths.has(day)))}
      ${airlinePayMetrics(item, priorities.has('pay'))}
      ${comparisonMetric('TAFB', tripTafb(item), strengths.includes('Lowest selected TAFB'))}
      ${comparisonMetric('Duty Periods', comparisonDutyPeriodCount(item), strengths.includes('Lowest selected duty-period count'))}
      ${comparisonMetric('Total Legs', totalLegs, strengths.includes('Lowest selected total-leg count'))}
      ${comparisonMetric('Max Legs / Duty Day', maximumLegs, maximumMeets)}
      ${comparisonMetric('Deadheads', comparisonDeadheads(item), strengths.includes('Lowest selected deadhead count'))}
      ${comparisonMetric('Layovers', comparisonLayoverLabel(item), destinations.length > 0)}
      ${comparisonMetric('Report', eventTime(item, 'report'), reportMeets)}
      ${comparisonMetric('Release', eventTime(item, 'release'), releaseMeets)}
      ${comparisonMetric('Preferred Destinations', destinations.join(' · ') || 'None', destinations.length > 0)}
    </div>
    <div class="fd-compare-details">
      ${comparisonDetail('Exact matched preferences', matched, 'No exact matched preferences were recorded.')}
      ${comparisonDetail('Compromises', compromises, 'No compromises were recorded.')}
      ${comparisonDetail('Assessments', [
        `Fatigue: ${assessmentValue(item.fatigue_index, 'Not available (placeholder)')}`,
        `Likelihood of Holding: ${assessmentValue(item.hold_outlook, 'Not available (placeholder)')}`,
        `Commute: ${assessmentValue(item.commute_assessment, 'Not available (placeholder)')}`,
      ], 'Assessments are unavailable.')}
    </div>
    <footer><a class="text-button button" href="/labs/flight-deck/trip/${encodeURIComponent(tripId(item))}">Open ${escapeHtml(terminology(item))} Briefing</a><button type="button" data-action="compare-remove" data-trip-id="${escapeHtml(tripId(item))}">Remove from Compare</button></footer>
  </article>`;
}

function comparePage() {
  const records = recordsForStoredIds(comparisonKey).slice(0, 4);
  const selected = new Set(records.map(tripId));
  const available = recordsForStoredIds(shortlistKey).filter(item => !selected.has(tripId(item)));
  const needed = Math.max(0, 2 - records.length);
  const status = records.length >= 2
    ? `${records.length} trips selected. Compare each trip against your saved priorities; no universal winner is declared.`
    : `Select ${needed} more trip${needed === 1 ? '' : 's'} to compare. You can compare two to four trips.`;
  const cards = records.map(item => compareCard(item, records)).join('');
  const picker = available.length && records.length < 4 ? `<section class="surface fd-compare-picker"><h2>Add from Shortlist</h2><div>${available.map(item => `<button type="button" data-action="compare" data-trip-id="${escapeHtml(tripId(item))}"><span>${escapeHtml(terminology(item))}</span><strong>${escapeHtml(sourceNumber(item))}</strong></button>`).join('')}</div></section>` : '';
  return `${pageHero('SIDE BY SIDE', 'Compare Trips', 'Compare normalized trip facts from one active bid package using your selected priorities.')}${packageSummary()}
    <section class="fd-compare-status" aria-live="polite"><strong>${escapeHtml(status)}</strong>${selectionNotice ? `<p>${escapeHtml(selectionNotice)}</p>` : ''}</section>
    ${picker}<section class="fd-compare-grid">${cards || emptyState('No trips selected', 'Choose two to four trips from Flight Deck results or your shortlist.')}</section>${selectionDock()}`;
}

function uniqueDetails(values) {
  return [...new Set((Array.isArray(values) ? values : []).map(value => String(value || '').trim()).filter(Boolean))];
}

function briefingModels(item) {
  const packageId = activePackageId();
  return canonicalTrips(item).filter(model => model && model.package_id === packageId && model.bidable_inventory_confirmed === true);
}

function briefingPrimaryModel(item) {
  const models = briefingModels(item);
  if (item?.item_type === 'line') return null;
  if (item?.canonical_trip && models.includes(item.canonical_trip)) return item.canonical_trip;
  return models.length === 1 ? models[0] : null;
}

function briefingTerminology(item, model) {
  if (item?.item_type === 'line' && tripAirline(item) === 'southwest') return 'Line';
  const term = String(model?.terminology || '').toLowerCase();
  if (term === 'rotation') return 'Rotation';
  if (term === 'sequence') return 'Sequence';
  return 'Pairing';
}

function briefingTitle(item, model) {
  const term = briefingTerminology(item, model);
  if (term === 'Rotation') return 'Rotation Briefing';
  if (term === 'Sequence') return 'Sequence Briefing';
  if (term === 'Line') return 'Line Briefing';
  return 'Pairing Briefing';
}

function formatLocalTime24(value) {
  const text = String(value || '').trim();
  const match = text.match(/T(\d{2}):(\d{2})/) || text.match(/^(\d{1,2}):?(\d{2})$/);
  if (!match) return 'Unavailable';
  const hours = Number(match[1]), minutes = Number(match[2]);
  return hours < 24 && minutes < 60 ? `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}` : 'Unavailable';
}

function canonicalEventDisplay(event) {
  if (!event) return 'Unavailable';
  const parts = [formatLocalTime24(event.local_time), event.airport, event.local_timezone].filter(value => value !== null && value !== undefined && value !== '' && value !== 'Unavailable');
  return parts.length ? parts.join(' | ') : 'Unavailable';
}

function detailList(values, missing = 'No supported details are available.') {
  const details = uniqueDetails(values);
  return details.length ? `<ul>${details.map(value => `<li>${escapeHtml(value)}</li>`).join('')}</ul>` : `<p class="fd-missing">${escapeHtml(missing)}</p>`;
}

function briefingOverviewPay(model) {
  if (!model) return '';
  const pay = model.pay_breakdown || {};
  const tfp = model.tfp || {};
  const values = [];
  if (model.airline === 'delta' && pay.total_pay !== null && pay.total_pay !== undefined) values.push(metric('Total Pay', pay.total_pay, 'fd-pay-primary'));
  if (model.airline !== 'southwest' && pay.trip_credit !== null && pay.trip_credit !== undefined) values.push(metric('Trip Credit', pay.trip_credit));
  if (model.airline !== 'delta' && model.airline !== 'southwest' && (pay.trip_credit === null || pay.trip_credit === undefined) && pay.total_pay !== null && pay.total_pay !== undefined) values.push(metric('Total Pay', pay.total_pay));
  if (model.airline === 'southwest') values.push(metric('TFP', tfp.pairing_tfp ?? tfp.line_tfp ?? tfp.monthly_tfp));
  return values.join('');
}

function operationalHighlights(model) {
  if (!model) return '<p class="fd-missing">Canonical trip details are unavailable for this result.</p>';
  const legs = Array.isArray(model.ordered_legs) ? model.ordered_legs : [];
  const operatingDates = Array.isArray(model.operating_dates) ? model.operating_dates : [];
  return `<div class="fd-fact-grid">
    ${metric('Route', model.simplified_route)}
    ${metric('Operating Legs', legs.length || null)}
    ${metric('Operating Dates', operatingDates.length ? operatingDates.join(', ') : null)}
    ${metric('Base', model.base)}
    ${metric('Fleet / Seat', [model.fleet, model.seat].filter(Boolean).join(' / ') || null)}
  </div>`;
}

function thingsToKnow(item) {
  const failures = uniqueDetails(item.eligibility_violations || item.hard_failures || item.violations || []);
  const compromises = uniqueDetails(item.compromises || []);
  const neutral = uniqueDetails(item.neutral_attributes || []);
  if (!failures.length && !compromises.length && !neutral.length) {
    return '<p class="fd-missing">No additional recommendation details are available.</p>';
  }
  return `${failures.length ? `<div class="fd-briefing-detail fd-warning"><h3>Hard requirements not met</h3>${detailList(failures)}</div>` : ''}
    ${compromises.length ? `<div class="fd-briefing-detail"><h3>Compromises</h3>${detailList(compromises)}</div>` : ''}
    ${neutral.length ? `<div class="fd-briefing-detail"><h3>Neutral trip facts</h3>${detailList(neutral)}</div>` : ''}`;
}

function canonicalDutyDayGroups(models) {
  let dayOffset = 0;
  return models.map(model => {
    const canonicalDays = Array.isArray(model.duty_days) ? model.duty_days : [];
    const days = canonicalDays.map((day, dayIndex) => ({ day, mapDayIndex: dayOffset + dayIndex + 1 }));
    dayOffset += days.length;
    return { model, days };
  });
}

function canonicalMapLegs(models) {
  let routeSegmentIndex = 0;
  return canonicalDutyDayGroups(models).flatMap(({ model, days }, modelIndex) => {
    const dayIndexByCanonicalValue = new Map(days.map(({ day, mapDayIndex }) => [String(day.day_index), mapDayIndex]));
    const orderedLegs = Array.isArray(model.ordered_legs) ? model.ordered_legs : [];
    return orderedLegs.map((leg, modelLegIndex) => ({
      leg,
      model,
      modelIndex,
      modelLegIndex,
      mapDayIndex: dayIndexByCanonicalValue.get(String(leg.duty_day_index)) || null,
      routeSegmentIndex: ++routeSegmentIndex,
    }));
  });
}

function canonicalMapEvents(visibleLegs, allLegs = visibleLegs) {
  if (!visibleLegs.length) return [];
  const first = visibleLegs[0];
  const previousLeg = allLegs[first.routeSegmentIndex - 2] || null;
  const events = [{
    airport: first.leg.origin,
    mapDayIndex: first.mapDayIndex,
    model: first.model,
    leg: first.leg,
    previousLeg,
    nextLeg: first,
    boundaryAfterDutyDay: previousLeg && previousLeg.model === first.model && previousLeg.mapDayIndex !== first.mapDayIndex
      ? previousLeg.leg.duty_day_index : null,
  }];
  visibleLegs.forEach(entry => {
    const nextLeg = allLegs[entry.routeSegmentIndex] || null;
    events.push({
      airport: entry.leg.destination,
      mapDayIndex: entry.mapDayIndex,
      model: entry.model,
      leg: entry.leg,
      previousLeg: entry,
      nextLeg,
      boundaryAfterDutyDay: !nextLeg || nextLeg.model !== entry.model || nextLeg.mapDayIndex !== entry.mapDayIndex
        ? entry.leg.duty_day_index : null,
    });
  });
  return events.map((event, eventIndex) => ({ ...event, eventIndex: eventIndex + 1 }));
}

function airportCoordinate(airport) {
  const code = String(airport || '').trim().toUpperCase();
  const record = window.CREWBIDIQ_AIRPORT_COORDINATES?.[code];
  if (!record || !Number.isFinite(record.latitude) || !Number.isFinite(record.longitude)) return null;
  return { ...record, code };
}

function mapEventRole(event) {
  const airport = String(event.airport || '').toUpperCase();
  if (airport && airport === String(event.model?.base || '').toUpperCase()) return 'home';
  const isCanonicalLayover = event.boundaryAfterDutyDay !== null && (event.model?.layovers || []).some(layover => (
    String(layover.after_duty_day) === String(event.boundaryAfterDutyDay)
      && layoverAirport(layover) === airport
  ));
  return isCanonicalLayover ? 'layover' : 'connection';
}

function greatCircleArc(start, end, pointCount = 48) {
  const radians = degrees => degrees * Math.PI / 180;
  const degrees = radiansValue => radiansValue * 180 / Math.PI;
  const vector = coordinate => {
    const latitude = radians(coordinate.latitude), longitude = radians(coordinate.longitude);
    return [Math.cos(latitude) * Math.cos(longitude), Math.cos(latitude) * Math.sin(longitude), Math.sin(latitude)];
  };
  const startVector = vector(start), endVector = vector(end);
  const angle = Math.acos(Math.max(-1, Math.min(1, startVector.reduce((sum, value, index) => sum + value * endVector[index], 0))));
  if (angle < 1e-10) return [[start.latitude, start.longitude], [end.latitude, end.longitude]];
  const denominator = Math.sin(angle);
  return Array.from({ length: pointCount + 1 }, (_, index) => {
    const fraction = index / pointCount;
    const startWeight = Math.sin((1 - fraction) * angle) / denominator;
    const endWeight = Math.sin(fraction * angle) / denominator;
    const x = startWeight * startVector[0] + endWeight * endVector[0];
    const y = startWeight * startVector[1] + endWeight * endVector[1];
    const z = startWeight * startVector[2] + endWeight * endVector[2];
    return [degrees(Math.atan2(z, Math.hypot(x, y))), ((degrees(Math.atan2(y, x)) + 540) % 360) - 180];
  });
}

function splitGreatCircleAtDateLine(points) {
  if (!points.length) return [];
  const parts = [[points[0]]];
  for (let index = 1; index < points.length; index += 1) {
    const previous = points[index - 1], current = points[index];
    const difference = current[1] - previous[1];
    if (Math.abs(difference) <= 180) {
      parts[parts.length - 1].push(current);
      continue;
    }
    const crossingLongitude = difference < -180 ? 180 : -180;
    const wrappedLongitude = difference < -180 ? current[1] + 360 : current[1] - 360;
    const fraction = (crossingLongitude - previous[1]) / (wrappedLongitude - previous[1]);
    const crossingLatitude = previous[0] + (current[0] - previous[0]) * fraction;
    parts[parts.length - 1].push([crossingLatitude, crossingLongitude]);
    parts.push([[crossingLatitude, -crossingLongitude], current]);
  }
  return parts;
}

function unwrapRoutePoints(points, anchorLongitude = null) {
  let previousLongitude = anchorLongitude;
  return points.map(([latitude, rawLongitude]) => {
    let longitude = rawLongitude;
    if (previousLongitude !== null) {
      while (longitude - previousLongitude > 180) longitude -= 360;
      while (longitude - previousLongitude < -180) longitude += 360;
    }
    previousLongitude = longitude;
    return [latitude, longitude];
  });
}

function routeMapMarkup(models) {
  const legs = canonicalMapLegs(models);
  if (!legs.length) return '<p class="fd-missing">Canonical ordered legs are unavailable for the route map.</p>';
  const dayCount = canonicalDutyDayGroups(models).reduce((total, group) => total + group.days.length, 0);
  if (selectedMapDay !== 'all' && (Number(selectedMapDay) < 1 || Number(selectedMapDay) > dayCount)) selectedMapDay = 'all';
  const events = canonicalMapEvents(legs);
  const dayButtons = [['all', 'All Days'], ...Array.from({ length: dayCount }, (_, index) => [String(index + 1), `Day ${index + 1}`])];
  return `<div class="fd-route-map-shell" data-selected-day="${escapeHtml(selectedMapDay)}">
    <div class="fd-route-map-toolbar"><div class="fd-map-day-filters" role="group" aria-label="Route map duty-day filter">${dayButtons.map(([value, label]) => `<button type="button" data-map-day="${value}" aria-pressed="${selectedMapDay === value}">${label}</button>`).join('')}</div><button type="button" class="fd-map-fullscreen" data-action="map-fullscreen" aria-label="Open route map full screen">Full Screen</button></div>
    <div class="fd-map-legend" aria-label="Route map legend"><span class="fd-legend-operating">Operating</span><span class="fd-legend-deadhead">Deadhead</span><span class="fd-legend-home">Home Base</span><span class="fd-legend-layover">Layover</span><span class="fd-legend-connection">Connection</span><span class="fd-legend-favorite">Favorite</span></div>
    <div id="flightDeckRouteMap" class="fd-route-map" role="application" aria-label="Interactive chronological trip route map"></div>
    <div id="flightDeckMapNotice" class="fd-map-notice" aria-live="polite"></div>
    <p class="fd-map-sequence-summary"><strong>${legs.length}</strong> ordered route segment${legs.length === 1 ? '' : 's'} &middot; <strong>${events.length}</strong> ordered airport event${events.length === 1 ? '' : 's'}. Repeated visits remain in sequence. Airport coordinates: <a href="https://ourairports.com/data/" target="_blank" rel="noopener">OurAirports public-domain data</a>.</p>
  </div>`;
}

function syncTripFlowMapDay() {
  document.querySelectorAll('.fd-duty-day[data-map-duty-day]').forEach(day => {
    const selected = selectedMapDay === 'all' || day.dataset.mapDutyDay === selectedMapDay;
    day.classList.toggle('fd-map-day-selected', selectedMapDay !== 'all' && selected);
    day.classList.toggle('fd-map-day-muted', selectedMapDay !== 'all' && !selected);
  });
}

function initializeFlightDeckMap(models) {
  const mapElement = document.getElementById('flightDeckRouteMap');
  if (!mapElement) return;
  if (!window.L || !window.CREWBIDIQ_AIRPORT_COORDINATES) {
    mapElement.innerHTML = '<p class="fd-missing">The interactive route map is temporarily unavailable.</p>';
    return;
  }
  const allLegs = canonicalMapLegs(models);
  const visibleLegs = selectedMapDay === 'all' ? allLegs : allLegs.filter(entry => String(entry.mapDayIndex) === selectedMapDay);
  const visibleEvents = canonicalMapEvents(visibleLegs, allLegs);
  if (flightDeckMap) flightDeckMap.remove();
  flightDeckMap = window.L.map(mapElement, { worldCopyJump: true, minZoom: 2, zoomControl: true });
  window.L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    maxZoom: 19,
  }).addTo(flightDeckMap);
  flightDeckMapLayers = window.L.layerGroup().addTo(flightDeckMap);
  const bounds = [];
  const missingAirports = new Set();
  let routeLongitudeAnchor = null;
  visibleLegs.forEach(entry => {
    const origin = airportCoordinate(entry.leg.origin), destination = airportCoordinate(entry.leg.destination);
    if (!origin || !destination) {
      if (!origin) missingAirports.add(String(entry.leg.origin || 'unknown'));
      if (!destination) missingAirports.add(String(entry.leg.destination || 'unknown'));
      return;
    }
    const operation = entry.leg.operating_or_deadhead === 'deadhead' ? 'deadhead' : 'operating';
    const arc = greatCircleArc(origin, destination);
    splitGreatCircleAtDateLine(arc).forEach(part => {
      const displayedPart = unwrapRoutePoints(part, routeLongitudeAnchor);
      routeLongitudeAnchor = displayedPart[displayedPart.length - 1][1];
      window.L.polyline(displayedPart, {
        className: `fd-map-leg fd-map-leg-${operation}`,
        color: operation === 'deadhead' ? '#e45656' : '#21ad6b',
        dashArray: operation === 'deadhead' ? '8 8' : null,
        lineCap: 'round',
        opacity: 0.92,
        weight: 4,
      }).bindPopup(`<strong>Leg ${entry.routeSegmentIndex}</strong><br>${escapeHtml(entry.leg.origin)} &rarr; ${escapeHtml(entry.leg.destination)}<br>${operation === 'deadhead' ? 'Deadhead' : 'Operating'}${entry.leg.flight_number ? ` &middot; Flight ${escapeHtml(entry.leg.flight_number)}` : ''}`).addTo(flightDeckMapLayers);
      bounds.push(...displayedPart);
    });
  });
  const favorites = preferredAirports();
  let eventLongitudeAnchor = bounds[0]?.[1] ?? null;
  visibleEvents.forEach(event => {
    const coordinate = airportCoordinate(event.airport);
    if (!coordinate) return;
    const role = mapEventRole(event);
    const favorite = favorites.has(String(event.airport || '').toUpperCase());
    const roleLabel = ({ home: 'Home base', layover: 'Layover', connection: 'Connection' })[role];
    const icon = window.L.divIcon({
      className: 'fd-map-marker',
      html: `<span class="fd-map-pin fd-map-pin-${role}" data-map-event-index="${event.eventIndex}">${role === 'home' ? '&#8962;' : event.eventIndex}${favorite ? '<b aria-label="Favorite destination">&#9733;</b>' : ''}</span>`,
      iconAnchor: [17, 17], iconSize: [34, 34], popupAnchor: [0, -16],
    });
    const displayedCoordinate = unwrapRoutePoints([[coordinate.latitude, coordinate.longitude]], eventLongitudeAnchor)[0];
    eventLongitudeAnchor = displayedCoordinate[1];
    window.L.marker(displayedCoordinate, { icon, keyboard: true, title: `${event.eventIndex}. ${event.airport} - ${roleLabel}` })
      .bindPopup(`<strong>${event.eventIndex}. ${escapeHtml(event.airport)}</strong><br>${escapeHtml(coordinate.name)}<br>${roleLabel}${favorite ? ' &middot; Favorite destination' : ''}`)
      .addTo(flightDeckMapLayers);
  });
  if (bounds.length === 1) flightDeckMap.setView(bounds[0], 6);
  else if (bounds.length > 1) flightDeckMap.fitBounds(window.L.latLngBounds(bounds), { padding: [28, 28], maxZoom: 7 });
  else flightDeckMap.setView([20, 0], 2);
  const notice = document.getElementById('flightDeckMapNotice');
  if (notice && missingAirports.size) notice.textContent = `Coordinates unavailable for: ${[...missingAirports].join(', ')}. Affected legs were not drawn.`;
  setTimeout(() => flightDeckMap?.invalidateSize(), 0);
  syncTripFlowMapDay();
}

function toggleRouteMapFullscreen() {
  const shell = document.querySelector('.fd-route-map-shell');
  if (!shell) return;
  if (document.fullscreenElement && document.exitFullscreen) document.exitFullscreen();
  else if (shell.requestFullscreen) shell.requestFullscreen();
  else shell.classList.toggle('fd-route-map-expanded');
  setTimeout(() => flightDeckMap?.invalidateSize(), 0);
}

function tripFlow(models) {
  const groups = canonicalDutyDayGroups(models).map(({ model, days: mappedDays }) => {
    if (!Array.isArray(model.duty_days)) return '';
    const days = mappedDays;
    if (!days.length) return '';
    const dayCards = days.map(({ day, mapDayIndex }) => {
      const legs = Array.isArray(day.ordered_legs) ? day.ordered_legs : [];
      const legRows = legs.length ? legs.map(leg => {
        const operation = leg.operating_or_deadhead === 'deadhead' ? 'Deadhead' : 'Operating';
        const details = [operation, leg.flight_number ? `Flight ${leg.flight_number}` : null, leg.equipment ? `Aircraft ${leg.equipment}` : null].filter(Boolean);
        const connection = leg.connection_after ? `<div class="fd-trip-connection"><span>Connection / Sit</span><strong>${escapeHtml(displayValue(leg.destination))} | ${escapeHtml(leg.connection_after)}</strong></div>` : '';
        return `<li class="fd-trip-leg" data-map-leg-sequence="${escapeHtml(displayValue(leg.sequence_index))}"><span>${escapeHtml(displayValue(leg.sequence_index))}</span><div><strong>${escapeHtml(displayValue(leg.origin))} &rarr; ${escapeHtml(displayValue(leg.destination))}</strong><small>${escapeHtml(details.join(' | '))}</small><div class="fd-leg-times"><span>Depart <strong>${escapeHtml(formatLocalTime24(leg.local_departure_time))}</strong></span><span>Arrive <strong>${escapeHtml(formatLocalTime24(leg.local_arrival_time))}</strong></span></div>${connection}</div></li>`;
      }).join('') : '<li class="fd-missing">No normalized legs are available for this duty day.</li>';
      const layover = day.layover_after_duty;
      const layoverBlock = layover ? `<footer class="fd-duty-layover"><div><span>Layover / Overnight after release</span><strong>${escapeHtml(displayValue(layover.airport || layover.city))}</strong></div><div><span>Duration</span><strong>${escapeHtml(displayValue(layover.duration))}</strong></div><div><span>Hotel</span><strong>${escapeHtml(displayValue(layover.hotel))}</strong></div></footer>` : '<footer class="fd-duty-layover fd-no-layover"><span>No canonical layover after release.</span></footer>';
      return `<article class="fd-duty-day" data-duty-day="${escapeHtml(displayValue(day.day_index))}" data-map-duty-day="${mapDayIndex}"><header><div><span>Duty Day ${escapeHtml(displayValue(day.day_index))}</span><strong>${escapeHtml(displayValue(day.calendar_date, 'Date unavailable'))}</strong></div><div class="fd-duty-endpoints"><small>Local Report</small><strong>${escapeHtml(formatLocalTime24(day.report_event?.local_time))}</strong><span>${escapeHtml(displayValue(day.report_event?.airport, 'Airport unavailable'))}</span><small>Local Release</small><strong>${escapeHtml(formatLocalTime24(day.release_event?.local_time))}</strong><span>${escapeHtml(displayValue(day.release_event?.airport, 'Airport unavailable'))}</span></div></header><ol>${legRows}</ol>${layoverBlock}</article>`;
    }).join('');
    const showMember = models.length > 1;
    return `<div class="fd-duty-group" data-canonical-trip-id="${escapeHtml(model.id)}">${showMember ? `<h3>${escapeHtml(model.terminology || 'pairing')} ${escapeHtml(model.source_trip_number)}</h3>` : ''}${dayCards}</div>`;
  }).filter(Boolean);
  return groups.length ? groups.join('') : '<p class="fd-missing">Duty-day details are unavailable.</p>';
}

function layoversAndHotels(models) {
  const layovers = models.flatMap(model => (Array.isArray(model.layovers) ? model.layovers.map(layover => ({ model, layover })) : []));
  if (!layovers.length) return '<p class="fd-missing">No canonical layovers are available for this trip.</p>';
  return layovers.map(({ model, layover }) => `<article class="fd-layover-card">
    <header><strong>${escapeHtml(displayValue(layover.airport || layover.city))}</strong><span>After duty day ${escapeHtml(displayValue(layover.after_duty_day))}</span></header>
    <div class="fd-fact-grid">
      ${metric('Duration', layover.duration)}
      ${metric('Start', layover.start_local)}
      ${metric('End', layover.end_local)}
      ${metric('Hotel', layover.hotel)}
      ${metric('Transportation', layover.transportation)}
      ${metric('Validation', layover.validated === true ? 'Validated' : 'Not validated')}
    </div>${models.length > 1 ? `<small>${escapeHtml(model.terminology || 'pairing')} ${escapeHtml(model.source_trip_number)}</small>` : ''}
  </article>`).join('');
}

function payOrTfpBreakdown(model) {
  if (!model) return '<p class="fd-missing">A normalized pay or TFP breakdown is unavailable.</p>';
  if (model.airline === 'southwest') {
    const tfp = model.tfp || {};
    const rows = [
      ['Pairing TFP', tfp.pairing_tfp], ['Line TFP', tfp.line_tfp], ['Monthly TFP', tfp.monthly_tfp],
      ['Carry-out TFP', tfp.carry_out_tfp], ['TFP per Duty Period', tfp.tfp_per_duty_period], ['TFP per Day Away', tfp.tfp_per_day_away],
    ].filter(([, value]) => value !== null && value !== undefined && value !== '');
    return rows.length ? `<div class="fd-fact-grid">${rows.map(([label, value]) => metric(label, value)).join('')}</div>` : '<p class="fd-missing">A normalized TFP breakdown is unavailable.</p>';
  }
  const pay = model.pay_breakdown || {};
  const fields = [['Trip Credit', pay.trip_credit]];
  if (model.airline === 'delta') fields.push(
    ['EDP', pay.edp], ['HOL', pay.hol], ['SIT', pay.sit],
    ['Additional Pay', pay.additional_pay], ['Total Pay', pay.total_pay],
  );
  else fields.push(['Total Pay', pay.total_pay]);
  const availableFields = fields.filter(([, value]) => value !== null && value !== undefined && value !== '');
  const rawTokens = uniqueDetails(pay.raw_pay_tokens);
  const unresolved = uniqueDetails(pay.unresolved_pay_tokens);
  if (!availableFields.length && !rawTokens.length && !unresolved.length) return '<p class="fd-missing">A normalized pay breakdown is unavailable.</p>';
  return `<div class="fd-fact-grid">${availableFields.map(([label, value]) => metric(label, value, label === 'Total Pay' ? 'fd-pay-primary' : '')).join('')}</div>
    ${rawTokens.length ? `<div class="fd-briefing-detail"><h3>Raw pay tokens</h3>${detailList(rawTokens)}</div>` : ''}
    ${unresolved.length ? `<div class="fd-briefing-detail fd-warning"><h3>Unresolved pay tokens</h3>${detailList(unresolved)}</div>` : ''}`;
}

function recommendationSection(item) {
  const qualified = uniqueDetails(item.qualification_reasons || []);
  const matched = uniqueDetails(item.matched_preferences || []);
  const compromises = uniqueDetails(item.compromises || []);
  const failures = uniqueDetails(item.eligibility_violations || item.hard_failures || item.violations || []);
  return `<div class="fd-recommendation-class"><span>Match class</span><strong class="fd-match fd-match-${matchClass(item)}">${escapeHtml(matchLabel(item))}</strong></div>
    <div class="fd-briefing-detail"><h3>Why it qualified</h3>${detailList(qualified, 'Qualification details are unavailable.')}</div>
    <div class="fd-briefing-detail"><h3>Matched preferences</h3>${detailList(matched, 'No matched preferences were provided.')}</div>
    ${compromises.length ? `<div class="fd-briefing-detail"><h3>Compromises</h3>${detailList(compromises)}</div>` : ''}
    ${failures.length ? `<div class="fd-briefing-detail fd-warning"><h3>Hard failures</h3>${detailList(failures)}</div>` : ''}`;
}

function fatigueAssessment(item) {
  const fatigue = item?.fatigue_index;
  if (!fatigue || typeof fatigue !== 'object') {
    return '<p class="fd-missing">Fatigue Index is unavailable because the normalized schedule data is incomplete.</p>';
  }
  const factors = uniqueDetails(fatigue.contributing_factors || []);
  const mitigations = uniqueDetails(fatigue.mitigating_factors || []);
  return `<div class="fd-fact-grid">
      ${metric('Fatigue Index', fatigue.level || 'Insufficient Data')}
      ${metric('Confidence', fatigue.confidence || 'Low')}
      ${metric('FAR Legality', fatigue.legality_assessment || 'Not assessed separately')}
    </div>
    <div class="fd-briefing-detail"><h3>Contributing factors</h3>${detailList(factors, 'No elevated schedule factors were identified from the available data.')}</div>
    <div class="fd-briefing-detail"><h3>Mitigating factors</h3>${detailList(mitigations, 'No mitigating factors were confirmed from the available data.')}</div>
    ${fatigue.missing_data_warning ? `<div class="fd-briefing-detail fd-warning"><h3>Missing data</h3><p>${escapeHtml(fatigue.missing_data_warning)}</p></div>` : ''}`;
}

function holdingAssessment(item) {
  const holding = item?.hold_outlook;
  if (!holding || typeof holding !== 'object') {
    return '<p class="fd-missing">Holding estimate is unavailable because active-package inventory data is incomplete.</p>';
  }
  const factors = uniqueDetails(holding.factors || holding.evidence || []);
  const desirabilityFactors = uniqueDetails(holding.desirability_factors || []);
  return `<div class="fd-fact-grid">
      ${metric('Desirability', holding.desirability || 'Insufficient Data')}
      ${metric('Likelihood of Holding', holding.likelihood || holding.outlook || 'Insufficient Data')}
      ${metric('Confidence', holding.confidence || 'Low')}
      ${metric('Estimate Basis', holding.estimate_basis || 'Inventory-based estimate only')}
    </div>
    <div class="fd-briefing-detail"><h3>Factors used</h3>${detailList(factors, 'No trip-specific holding factors are available.')}</div>
    <div class="fd-briefing-detail"><h3>Desirability factors</h3>${detailList(desirabilityFactors, 'Preference-based desirability could not be assessed.')}</div>
    ${holding.missing_data_warning ? `<div class="fd-briefing-detail fd-warning"><h3>Missing data</h3><p>${escapeHtml(holding.missing_data_warning)}</p></div>` : ''}`;
}

function originalAirlineTrip(models) {
  if (!models.length) return '<p class="fd-missing">Confirmed bidable source provenance is unavailable.</p>';
  return models.map(model => {
    if (model.bidable_inventory_confirmed !== true) return '';
    const sourceText = String(model.source_text || '').trim();
    return `<article class="fd-source-record"><div class="fd-source-meta">
      ${metric('Identifier', model.source_trip_number)}${metric('Source Page', model.source_page)}${metric('Source Section', model.source_section)}${metric('Inventory', 'Confirmed bidable inventory')}
    </div>${sourceText ? `<pre>${escapeHtml(sourceText)}</pre>` : '<p class="fd-missing">Extracted source text is unavailable; use the source reference above.</p>'}</article>`;
  }).filter(Boolean).join('') || '<p class="fd-missing">Confirmed bidable source provenance is unavailable.</p>';
}

function tripBriefingPage() {
  const item = resultRecords().find(record => tripId(record) === requestedTripId || sourceNumber(record) === requestedTripId);
  if (!item) return `${pageHero('TRIP BRIEFING', 'Trip unavailable', 'This trip does not belong to the active bid package or is no longer available.')}<a class="primary button" href="/labs/flight-deck">Return to results</a>`;
  const models = briefingModels(item);
  const model = briefingPrimaryModel(item);
  if (!models.length) return `${pageHero('TRIP BRIEFING', 'Trip unavailable', 'Confirmed canonical bidable inventory is unavailable for this result.')}<a class="primary button" href="/labs/flight-deck">Return to results</a>`;
  renderedBriefingModels = models;
  const exactExplanation = matchClass(item) === 'exact'
    ? uniqueDetails([...(item.qualification_reasons || []), ...(item.matched_preferences || [])])
    : uniqueDetails(item.eligibility_violations || item.hard_failures || item.violations || item.qualification_reasons || []);
  const identifier = model?.source_trip_number || sourceNumber(item);
  const route = model?.simplified_route || (models.length > 1 ? `${models.length} canonical pairings` : 'Route unavailable');
  const term = briefingTerminology(item, model);
  return `${pageHero('FLIGHT DECK', briefingTitle(item, model), `${term} ${identifier} | ${route}`)}
    <div class="fd-briefing-layout">
      <section class="surface fd-briefing-section fd-briefing-overview"><span class="fd-section-number">01</span><h2>Overview</h2>
        <div class="fd-overview-identity"><span>${escapeHtml(term)}</span><strong>${escapeHtml(identifier)}</strong><em class="fd-match fd-match-${matchClass(item)}">${escapeHtml(matchLabel(item))}</em></div>
        <div class="fd-compare-metrics">${metric('Trip Length', model?.trip_length_days ? `${model.trip_length_days} day${model.trip_length_days === 1 ? '' : 's'}` : null)}${metric('Duty Periods', model?.duty_period_count)}${metric('TAFB', model?.tafb)}${metric('Report', canonicalEventDisplay(model?.report))}${metric('Release', canonicalEventDisplay(model?.release))}${briefingOverviewPay(model)}</div>
        <div class="fd-briefing-detail"><h3>${matchClass(item) === 'exact' ? 'Exact match explanation' : 'Match explanation'}</h3>${detailList(exactExplanation, 'A recommendation explanation is unavailable.')}</div>
      </section>
      <section class="surface fd-briefing-section"><span class="fd-section-number">02</span><h2>Operational Highlights</h2>${operationalHighlights(model)}</section>
      <section class="surface fd-briefing-section fd-briefing-wide fd-route-map-section"><span class="fd-section-number">MAP</span><h2>Global Route Map</h2>${routeMapMarkup(models)}</section>
      <section class="surface fd-briefing-section"><span class="fd-section-number">03</span><h2>Things to Know</h2>${thingsToKnow(item)}</section>
      <section class="surface fd-briefing-section fd-briefing-wide"><span class="fd-section-number">04</span><span class="fd-trip-flow-label">Trip Flow</span><h2>Duty-Day Summary</h2><div class="fd-trip-flow">${tripFlow(models)}</div></section>
      <section class="surface fd-briefing-section fd-briefing-wide"><span class="fd-section-number">05</span><h2>Layovers and Hotels</h2>${layoversAndHotels(models)}</section>
      <section class="surface fd-briefing-section"><span class="fd-section-number">06</span><h2>Pay or TFP Breakdown</h2>${payOrTfpBreakdown(model)}</section>
      <section class="surface fd-briefing-section"><span class="fd-section-number">07</span><h2>Fatigue</h2>${fatigueAssessment(item)}</section>
      <section class="surface fd-briefing-section"><span class="fd-section-number">08</span><h2>Likelihood of Holding</h2>${holdingAssessment(item)}</section>
      <section class="surface fd-briefing-section fd-placeholder"><span class="fd-section-number">09</span><h2>Commute Planner</h2><p>No commute plan is available for this trip.</p></section>
      <section class="surface fd-briefing-section fd-briefing-wide"><span class="fd-section-number">10</span><h2>Recommendation</h2>${recommendationSection(item)}</section>
      <section class="surface fd-briefing-section fd-briefing-wide"><span class="fd-section-number">11</span><h2>Original Airline Trip</h2>${originalAirlineTrip(models)}</section>
    </div>
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
  if (flightDeckMap) flightDeckMap.remove();
  flightDeckMap = null;
  flightDeckMapLayers = null;
  renderedBriefingModels = [];
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
  if (flightDeckPage === 'trip' && renderedBriefingModels.length) initializeFlightDeckMap(renderedBriefingModels);
}

function bindControls() {
  document.getElementById('flightDeckSort')?.addEventListener('change', event => { sortMode = event.target.value; render(); });
  document.querySelectorAll('[data-filter]').forEach(input => input.addEventListener('change', event => {
    filterState[event.target.dataset.filter] = event.target.checked;
    render();
  }));
  document.querySelectorAll('[data-map-day]').forEach(control => control.addEventListener('click', event => {
    selectedMapDay = event.currentTarget.dataset.mapDay || 'all';
    render();
  }));
  document.querySelectorAll('[data-action]').forEach(control => control.addEventListener('click', event => {
    const action = event.currentTarget.dataset.action;
    if (action === 'map-fullscreen') { toggleRouteMapFullscreen(); return; }
    const id = event.currentTarget.dataset.tripId;
    if (action === 'clear-filters') filterState = Object.fromEntries(Object.keys(filterState).map(key => [key, false]));
    if (action === 'shortlist') togglePackageScopedId(shortlistKey, id);
    if (action === 'shortlist-remove') removePackageScopedId(shortlistKey, id);
    if (action === 'shortlist-up') movePackageScopedId(shortlistKey, id, -1);
    if (action === 'shortlist-down') movePackageScopedId(shortlistKey, id, 1);
    if (action === 'compare') togglePackageScopedId(comparisonKey, id, 4);
    if (action === 'compare-remove') removePackageScopedId(comparisonKey, id);
    render();
  }));
}

async function loadSharedSession() {
  if (sharedSessionInFlight) return;
  const jobId = currentJobId();
  if (!jobId) { sessionJob = null; sessionLoading = false; sessionError = ''; render(); return; }
  sharedSessionInFlight = true;
  sharedSessionController?.abort();
  const controller = new AbortController(); sharedSessionController = controller;
  try {
    // Legacy contract reference: fetch(`/api/jobs/${encodeURIComponent(jobId)}`)
    // The active request additionally proves package and browser-session identity.
    const packageId = activePackageId();
    const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}?package_id=${encodeURIComponent(packageId || '')}`, { headers: { Accept: 'application/json', 'X-CrewBidIQ-Session': browserSessionId() }, signal: controller.signal });
    if (!response.ok) throw new Error('The active package could not be loaded.');
    const body = await response.json();
    acceptPackageResponse(body);
    sessionJob = body; sessionError = ''; sessionLoading = false; render();
    if (body.status === 'queued' || body.status === 'processing') { clearTimeout(sharedSessionTimer); sharedSessionTimer = setTimeout(loadSharedSession, 2000); }
  } catch (error) {
    if (error.name !== 'AbortError') { sessionLoading = false; sessionError = error.message || 'The active package could not be loaded.'; render(); }
  } finally {
    if (sharedSessionController === controller) sharedSessionController = null;
    sharedSessionInFlight = false;
  }
}

function applyTheme() {
  const theme = localStorage.getItem('crewbidiqTheme') || 'dark';
  document.documentElement.dataset.theme = theme;
}

document.getElementById('flightDeckTheme')?.addEventListener('click', () => {
  const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  safeLocalStorageSetItem('crewbidiqTheme', next); applyTheme();
});

document.addEventListener('fullscreenchange', () => setTimeout(() => flightDeckMap?.invalidateSize(), 0));
window.addEventListener('resize', () => flightDeckMap?.invalidateSize());

window.addEventListener('storage', event => {
  if ([shortlistKey, comparisonKey].includes(event.key)) { render(); return; }
  if (![latestJobKey, activeJobKey, activePackageKey, analysisSessionKey, flightDeckSessionKey].includes(event.key)) return;
  if (event.key === activePackageKey && event.oldValue && event.newValue !== event.oldValue) {
    clearPackageDependentState(event.newValue || '');
    selectedMapDay = 'all';
  }
  if ([analysisSessionKey, flightDeckSessionKey].includes(event.key) && event.oldValue && event.newValue !== event.oldValue) {
    clearPackageDependentState(activePackageId() || '');
    selectedMapDay = 'all';
  }
  sessionJob = null; sessionLoading = true; sessionError = ''; render(); loadSharedSession();
});

window.addEventListener('pagehide', event => {
  if (!event.persisted) return;
  clearTimeout(sharedSessionTimer); sharedSessionController?.abort(); sharedSessionController = null; sharedSessionInFlight = false;
  if (flightDeckMap) flightDeckMap.remove();
  flightDeckMap = null; flightDeckMapLayers = null; renderedBriefingModels = []; sessionJob = null;
  flightDeckContent.replaceChildren();
});
window.addEventListener('pageshow', event => {
  if (!event.persisted) return;
  sessionLoading = true; sessionError = ''; render(); loadSharedSession();
});

applyTheme();
render();
loadSharedSession();
