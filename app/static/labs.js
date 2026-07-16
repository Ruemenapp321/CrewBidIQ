const labsContent = document.getElementById('labsContent');
const labsPage = window.CREWBIDIQ_LABS_PAGE || 'landing';
const latestJobKey = 'crewbidiqLatestJob';
const activeJobKey = 'crewbidiqActiveJob';
const draftKey = 'crewbidiqLabsDraft';
let sessionJob = null;
let sessionLoading = true;
let navbluePlan = null;
let navbluePlanJob = null;
let navbluePlanError = '';
let labsUploadBusy = false;
let labsUploadController = null;
let labsUploadError = '';
let refinedRecommendationsLoading = false;
let refinedRecommendationsError = '';
let refinedRecommendationsSignature = '';

const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, character => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
}[character]));

function readJson(key, fallback = null) {
  try { return JSON.parse(localStorage.getItem(key) || 'null') ?? fallback; }
  catch (_) { return fallback; }
}

function airlineName(value) {
  return ({ auto: 'Auto-detecting', delta: 'Delta Air Lines', american: 'American Airlines', southwest: 'Southwest Airlines', generic: 'Other airline' })[value] || value || 'Airline unavailable';
}

function payGoalLabel() {
  if (sessionJob?.airline === 'southwest') return 'TFP and efficiency';
  if (sessionJob?.airline === 'delta') return 'Total Pay and efficiency';
  return 'Trip value and efficiency';
}

function resultPay(item) {
  const airline = item.airline || sessionJob?.airline;
  if (airline === 'southwest') return { label: item.item_type === 'line' ? 'Line TFP' : 'Pairing TFP', value: item.item_type === 'line' ? item.line_tfp : item.pairing_tfp };
  if (airline === 'delta') return { label: 'Total Pay', value: item.total_pay };
  return { label: 'Credit', value: item.credit };
}

function inferredBidMonth(filename = '') {
  const names = {
    JAN: 'January', FEB: 'February', MAR: 'March', APR: 'April', MAY: 'May', JUN: 'June',
    JUL: 'July', AUG: 'August', SEP: 'September', OCT: 'October', NOV: 'November', DEC: 'December'
  };
  const upper = filename.toUpperCase();
  const token = Object.keys(names).find(month => new RegExp(`(^|[^A-Z])${month}([^A-Z]|$)`).test(upper));
  const year = upper.match(/20\d{2}/)?.[0];
  return token ? `${names[token]}${year ? ` ${year}` : ''}` : (year || 'Bid month unavailable');
}

function formatParsedTime(value) {
  if (!value) return 'Not parsed yet';
  const parsed = new Date(value.endsWith('Z') ? value : `${value}Z`);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

function currentJobId() {
  return localStorage.getItem(activeJobKey) || localStorage.getItem(latestJobKey);
}

function pageHeader(kicker, title, description) {
  return `<section class="labs-hero">
    <div><span class="kicker">${escapeHtml(kicker)}</span><h1>${escapeHtml(title)}</h1><p>${escapeHtml(description)}</p></div>
    <div class="labs-hero-actions"><span class="beta-badge">Beta</span><a class="text-button button" href="/">Return to Classic</a></div>
  </section>`;
}

function packageCard() {
  if (sessionLoading) {
    return `<section class="surface package-status loading"><div class="status-light"></div><div><span>Shared bid package</span><strong>Checking this browser session...</strong><small>Classic and Labs use the same analysis.</small></div></section>`;
  }
  if (!sessionJob) {
    return `<section class="surface no-package"><div><span class="kicker">SHARED SESSION</span><h2>No bid package loaded</h2><p>Upload here in Labs or use a package already analyzed in Classic. Both experiences share one parsed package.</p></div><a class="primary button" href="#labsUpload">Upload Bid Package</a></section>`;
  }
  const complete = sessionJob.status === 'complete';
  const metadata = sessionJob.package || {};
  const status = complete ? 'Ready for Labs' : (sessionJob.status === 'failed' ? 'Analysis needs attention' : 'Analysis in progress');
  return `<section class="surface package-status ${escapeHtml(sessionJob.status)}">
    <div class="status-light"></div>
    <div class="package-status-main"><span>Current bid package</span><strong>${escapeHtml(metadata.filename || sessionJob.filename || 'Uploaded package')}</strong><small>${escapeHtml(airlineName(metadata.airline || sessionJob.airline))} · ${escapeHtml(metadata.bid_month || inferredBidMonth(sessionJob.filename))}</small><div class="package-meta-grid"><div class="package-meta-item"><span>Base</span><strong>${escapeHtml(metadata.base || 'Not detected')}</strong></div><div class="package-meta-item"><span>Fleet / category</span><strong>${escapeHtml(metadata.fleet_category || 'Not detected')}</strong></div><div class="package-meta-item"><span>Parsed</span><strong>${complete ? `${escapeHtml(metadata.parsed_count ?? sessionJob.results?.length ?? 0)} ${escapeHtml(metadata.record_label || 'records')}` : 'Processing'}</strong></div><div class="package-meta-item"><span>Last parsed</span><strong>${escapeHtml(formatParsedTime(metadata.last_parsed_at))}</strong></div></div></div>
    <div class="package-status-state"><span>${escapeHtml(status)}</span><strong>${escapeHtml(sessionJob.progress ?? 0)}%</strong><div class="package-status-actions">${complete ? '<a class="text-button button" href="/labs/recommendations">Use Current Package</a>' : ''}<a class="text-button button" href="#labsUpload">Replace Bid Package</a></div></div>
  </section>`;
}

const processingStages = [
  ['uploading', 'Uploading file'],
  ['detecting_package', 'Detecting airline and package type'],
  ['extracting_text', 'Extracting text'],
  ['identifying_records', 'Identifying trip records'],
  ['parsing_details', 'Parsing details'],
  ['building_recommendations', 'Building recommendation data'],
  ['ready', 'Ready']
];

function uploadProgressPanel() {
  if (!labsUploadBusy && !sessionJob) return '';
  const stage = labsUploadBusy ? 'uploading' : (sessionJob?.stage || (sessionJob?.status === 'complete' ? 'ready' : 'detecting_package'));
  const activeIndex = processingStages.findIndex(([value]) => value === stage);
  const percent = labsUploadBusy ? null : sessionJob?.progress;
  const pageDetail = sessionJob?.pages_total ? `Page ${sessionJob.pages_processed} of ${sessionJob.pages_total}` : '';
  const fileDetail = sessionJob?.files_total ? `File ${sessionJob.files_processed} of ${sessionJob.files_total}` : '';
  const packageName = airlineName(sessionJob?.airline);
  return `<div class="labs-processing ${stage === 'failed' ? 'failed' : ''}">
    <div class="labs-processing-heading"><div><span>${stage === 'failed' ? 'Analysis failed' : `Processing ${escapeHtml(packageName)} bid package`}</span><strong>${escapeHtml(sessionJob?.stage_label || (labsUploadBusy ? 'Uploading file' : sessionJob?.message || 'Preparing package'))}</strong><small>${escapeHtml(pageDetail || fileDetail || sessionJob?.message || 'Your progress is saved if you move to another Labs page.')}</small></div><div><strong>${percent == null ? '—' : `${escapeHtml(percent)}%`}</strong><small>${escapeHtml(sessionJob?.elapsed_seconds || 0)}s elapsed</small></div></div>
    <div class="progress"><i style="width:${Math.max(0, Math.min(Number(percent) || (labsUploadBusy ? 4 : 0), 100))}%"></i></div>
    <ol class="labs-stage-list">${processingStages.map(([value, label], index) => `<li class="${index < activeIndex ? 'done' : (index === activeIndex ? 'active' : '')}"><span>${index + 1}</span>${escapeHtml(label)}</li>`).join('')}</ol>
    ${stage === 'failed' ? `<div class="labs-upload-error"><strong>${escapeHtml(sessionJob?.error || 'The server could not analyze this package.')}</strong><p>Try again, select the airline manually, upload Southwest files individually, or return to Classic.</p></div>` : ''}
  </div>`;
}

function uploadPanel() {
  return `<section id="labsUpload" class="surface labs-upload-panel">
    <div class="surface-title"><div><span class="labs-step">⇧</span><div><h2>${sessionJob ? 'Replace Bid Package' : 'Upload Bid Package'}</h2><p>Uses the same 100 MB streaming upload and background parser as Classic.</p></div></div><span class="beta-badge">Shared</span></div>
    <div class="labs-upload-grid">
      <label>Airline<select id="labsAirline"><option value="auto">Auto-detect PDF or ZIP</option><option value="delta">Delta Air Lines</option><option value="american">American Airlines</option><option value="southwest">Southwest Airlines</option><option value="generic">Other airline / generic PDF</option></select></label>
      <div class="labs-file-control"><span>Bid package</span><label class="labs-file-target" for="labsPackageFile"><strong>Choose PDF or ZIP</strong><small id="labsPackageFilename">Files app, iCloud Drive, or this device</small></label><input id="labsPackageFile" class="native-file-input" type="file" accept=".pdf,.zip,application/pdf,application/zip"></div>
    </div>
    <div id="labsSouthwestFiles" class="labs-southwest-files hidden"><div><strong>Or upload individual Southwest text files</strong><small>Pairings and Lines are required. Cover and Seniority are optional.</small></div><div class="sw-files"><label>Pairings TXT<span id="labsPairingsFilename">Choose file</span><input id="labsPairingsFile" class="native-file-input" type="file" accept=".txt,text/plain"></label><label>Lines TXT<span id="labsLinesFilename">Choose file</span><input id="labsLinesFile" class="native-file-input" type="file" accept=".txt,text/plain"></label><label>Seniority TXT<span id="labsSeniorityFilename">Optional</span><input id="labsSeniorityFile" class="native-file-input" type="file" accept=".txt,text/plain"></label><label>Cover TXT<span id="labsCoverFilename">Optional</span><input id="labsCoverFile" class="native-file-input" type="file" accept=".txt,text/plain"></label></div></div>
    <div id="labsUploadError" class="error ${labsUploadError ? '' : 'hidden'}">${escapeHtml(labsUploadError)}</div>
    <div class="labs-upload-actions"><button id="labsAnalyzePackage" class="primary" type="button" ${labsUploadBusy ? 'disabled' : ''}>${labsUploadBusy ? 'Uploading…' : 'Analyze in Labs'}</button><button id="labsCancelUpload" class="text-button ${labsUploadBusy ? '' : 'hidden'}" type="button">Cancel upload</button><small>Maximum 100 MB. Uploaded source files are removed after parsing.</small></div>
    ${uploadProgressPanel()}
    <div id="labsReplacePrompt" class="labs-replace-prompt hidden"><div><span class="kicker">REPLACE CURRENT PACKAGE</span><h3>Replace the current bid package?</h3><p>Current recommendations and unfinished Labs analyses will be recalculated. Records from the two packages will never be mixed.</p><div><button id="labsConfirmReplace" class="primary" type="button">Replace</button><button id="labsCancelReplace" class="text-button" type="button">Cancel</button></div></div></div>
  </section>`;
}

function postParseActions() {
  if (sessionJob?.status !== 'complete') return '';
  const southwest = sessionJob.airline === 'southwest';
  const actions = southwest ? [
    ['/labs/recommendations', 'Rank My Lines'], ['/labs/build', 'Set Line Preferences'], ['/labs/southwest#schedule', 'Add Current Schedule'], ['/labs/southwest#conflicts', 'Optimize Conflicts']
  ] : [
    ['/labs/build', 'Describe the Trip You Want'], ['/labs/recommendations', 'Refine Recommendations'], ['/labs/plan', 'Build My Month'], ['/labs/plan', 'What to Enter in NAVBLUE/PBS']
  ];
  return `<section class="labs-post-parse"><div><span class="kicker">PACKAGE READY</span><h2>Continue with your bid</h2></div><div>${actions.map(([href, label], index) => `<a class="${index === 0 ? 'primary' : 'secondary'} button" href="${href}">${escapeHtml(label)}</a>`).join('')}</div></section>`;
}

function landingPage() {
  const hasDraft = Boolean(readJson(draftKey));
  return `${pageHeader('CREWBIDIQ LABS', 'Experimental bidding tools', 'Explore a guided path from your parsed bid package to a clear, pilot-ready bid plan.')}
    <section class="labs-beta-notice"><span>Beta</span><p>Labs features are experimental. Review any proposed bid plan before using it with your airline bidding system.</p></section>
    ${packageCard()}
    ${uploadPanel()}
    ${postParseActions()}
    <section class="labs-action-grid">
      <a href="/labs/build" class="labs-action-card primary-action"><span>01</span><h2>Build My Bid</h2><p>Turn days off, trip shape, layovers, and workload into a guided bid strategy.</p><strong>Start guided builder</strong></a>
      <a href="/labs/recommendations" class="labs-action-card"><span>02</span><h2>Refine Trip Recommendations</h2><p>Review the strongest options from the Classic analysis with less noise.</p><strong>Open recommendations</strong></a>
      <a href="/labs/preview" class="labs-action-card"><span>03</span><h2>View Bid Pool Preview</h2><p>Understand the shape of the airline's bid package before building a plan.</p><strong>View package picture</strong></a>
      <a href="/labs/build" class="labs-action-card"><span>04</span><h2>Resume Saved Draft</h2><p>${hasDraft ? 'Continue the bid priorities saved on this device.' : 'No saved draft yet. Start one and return whenever you are ready.'}</p><strong>${hasDraft ? 'Resume draft' : 'Start a draft'}</strong></a>
    </section>`;
}

function builderPage() {
  const classic = readJson('crewbidiqProfile', {});
  const draft = readJson(draftKey, {}) || {};
  const value = (key, profileKey = key) => draft[key] ?? (Array.isArray(classic[profileKey]) ? classic[profileKey].join(', ') : classic[profileKey]) ?? '';
  return `${pageHeader('GUIDED BID BUILDER', 'Build around the life you want', 'Set the few priorities that should shape your bid. Labs saves this draft on this device.')}
    ${packageCard()}
    ${uploadPanel()}
    <section class="surface labs-builder">
      <div class="surface-title"><div><span class="labs-step">1</span><div><h2>Define your month</h2><p>Start with what matters most. You can refine the details later.</p></div></div><span id="draftStatus" class="draft-status">Draft on this device</span></div>
      <div class="labs-form-grid">
        <label>Primary goal<select id="labsFocus"><option value="quality">Quality of life</option><option value="days_off">Protect days off</option><option value="layovers">Preferred layovers</option><option value="credit">${escapeHtml(payGoalLabel())}</option><option value="commute">Commute-friendly trips</option></select></label>
        <label>Required days off<input id="labsRequiredDays" value="${escapeHtml(value('requiredDays', 'required_days_off'))}" placeholder="8/11, 8/18"></label>
        <label>Preferred trip lengths<input id="labsTripLengths" value="${escapeHtml(value('tripLengths', 'preferred_trip_lengths'))}" placeholder="2, 3, 4"></label>
        <label>Highest-priority layovers<input id="labsLayovers" value="${escapeHtml(value('layovers', 'elite_cities'))}" placeholder="HNL, OGG, LIH"></label>
        <label>Avoid layovers<input id="labsAvoidLayovers" value="${escapeHtml(value('avoidLayovers', 'penalty_cities'))}" placeholder="DFW, IAH"></label>
        <label>Maximum legs per duty day<input id="labsMaxLegs" type="number" min="1" value="${escapeHtml(value('maxLegs', 'max_legs_per_day'))}" placeholder="3"></label>
        <label>Earliest report<input id="labsEarliestReport" type="time" value="${escapeHtml(value('earliestReport'))}"></label>
        <label>Latest release<input id="labsLatestRelease" type="time" value="${escapeHtml(value('latestRelease'))}"></label>
      </div>
      <label class="labs-notes">What would make this a successful month?<textarea id="labsNotes" placeholder="Example: Protect my daughter's birthday and favor longer Hawaii layovers.">${escapeHtml(value('notes'))}</textarea></label>
      <div class="labs-builder-actions"><button id="saveLabsDraft" class="secondary">Save draft</button><a id="openLabsRecommendations" class="primary button" href="/labs/recommendations">Refine recommendations</a></div>
    </section>
    <section class="surface labs-next-step"><div><span class="labs-step">2</span><div><h2>Ready for a proposed plan?</h2><p>Review the available trips first, then arrange your strongest options into a working bid order.</p></div></div><a class="text-button button" href="/labs/plan">Open bid plan</a></section>`;
}

function emptyFeature(message) {
  return `<section class="surface labs-feature-empty"><h2>${escapeHtml(message)}</h2><p>Labs needs a completed analysis before this tool can use the shared parsed package.</p><a class="primary button" href="/labs#labsUpload">Upload Bid Package</a></section>`;
}

function matchLabel(item) {
  return ({ excellent: 'Excellent', strong: 'Strong', good: 'Good', fair: 'Fair', low: 'Low' })[item.match_level] || 'Match';
}

function recommendationCards(results) {
  return results.slice(0, 8).map((item, index) => {
    const layovers = (item.layovers || []).map(layover => layover.city).join(', ') || 'No overnights';
    const reasons = (item.reasons || []).slice(0, 3);
    const pay = resultPay(item);
    return `<article class="labs-recommendation">
      <div class="labs-rank">${index + 1}</div>
      <div><span>${escapeHtml(item.display_label || 'Trip')} ${escapeHtml(item.pairing)}</span><h3>${escapeHtml(layovers)}</h3><p>${reasons.length ? reasons.map(escapeHtml).join(' · ') : 'No strong preference signals were detected.'}</p></div>
      <div class="labs-recommendation-metrics"><strong>${escapeHtml(matchLabel(item))}</strong><span>${escapeHtml(pay.value || 'N/A')} ${escapeHtml(pay.label)}</span></div>
    </article>`;
  }).join('');
}

function recommendationsPage() {
  const ready = sessionJob?.status === 'complete';
  const results = sessionJob?.results || [];
  return `${pageHeader('REFINED RECOMMENDATIONS', 'See the trips worth your attention', 'A quieter review reranked from your current Classic preferences and saved Labs draft.')}
    ${packageCard()}
    ${uploadPanel()}
    ${postParseActions()}
    ${!ready ? emptyFeature('Complete a Classic analysis first') : refinedRecommendationsLoading ? `<section class="surface labs-loading"><strong>Applying your saved trip preferences...</strong><p>Reranking the parsed package without uploading or parsing it again.</p></section>` : refinedRecommendationsError ? `<section class="surface labs-feature-empty"><h2>Recommendations could not be refreshed</h2><p>${escapeHtml(refinedRecommendationsError)}</p><a class="primary button" href="/labs/build">Review preferences</a></section>` : `<section class="surface labs-recommendations-panel"><div class="surface-title"><div><div><h2>Priority review</h2><p>${escapeHtml(results.length)} analyzed trips · showing the first ${Math.min(results.length, 8)}</p></div></div><a class="text-button button" href="/results">Open full Classic results</a></div><div class="labs-recommendation-list">${recommendationCards(results)}</div></section>`}
    <div class="labs-page-actions"><a class="secondary button" href="/labs/build">Adjust bid priorities</a><a class="primary button" href="/labs/plan">Build proposed plan</a></div>`;
}

function compactBreakdown(rows, key, suffix = '') {
  const values = rows || [];
  if (!values.length) return '<p class="muted">Not available in this package.</p>';
  return `<div class="labs-breakdown">${values.slice(0, 8).map(row => `<div><span>${escapeHtml(row[key])}${suffix}</span><strong>${escapeHtml(row.percent)}%</strong><i style="width:${Math.min(Number(row.percent) || 0, 100)}%"></i></div>`).join('')}</div>`;
}

function previewPage() {
  const ready = sessionJob?.status === 'complete';
  const synopsis = sessionJob?.synopsis;
  return `${pageHeader('BID POOL PREVIEW', 'Know what the package can offer', 'See the overall trip mix before your personal preferences narrow the field.')}
    ${packageCard()}
    ${!ready || !synopsis ? emptyFeature('No bid pool is ready yet') : `<section class="labs-preview-metrics">
      <article><span>Unique trips</span><strong>${escapeHtml(synopsis.total || 0)}</strong><small>Repeated operating dates count once</small></article>
      <article><span>Depart during WOCL</span><strong>${escapeHtml(synopsis.redeye?.percent || 0)}%</strong><small>${escapeHtml(synopsis.redeye?.count || 0)} trips · 02:00–05:59 local</small></article>
      <article><span>Contain deadheads</span><strong>${escapeHtml(synopsis.deadhead?.percent || 0)}%</strong><small>${escapeHtml(synopsis.deadhead?.count || 0)} trips</small></article>
      <article><span>Overnight cities</span><strong>${escapeHtml(synopsis.overnight_city_count || 0)}</strong><small>Distinct layover destinations</small></article>
    </section>
    <section class="labs-preview-grid">
      <article class="surface"><h2>Trip lengths</h2>${compactBreakdown(synopsis.trip_lengths, 'days', '-day')}</article>
      <article class="surface"><h2>Start airports</h2>${compactBreakdown(synopsis.start_airports, 'airport')}</article>
      <article class="surface"><h2>Fleet mix</h2>${compactBreakdown(synopsis.fleets, 'fleet')}</article>
      <article class="surface"><h2>Top overnight cities</h2>${compactBreakdown(synopsis.layover_cities, 'city')}</article>
    </section>`}
    <div class="labs-page-actions"><a class="secondary button" href="/labs/build">Set bid priorities</a><a class="primary button" href="/labs/recommendations">View recommendations</a></div>`;
}

function mergedLabsProfile() {
  const classic = readJson('crewbidiqProfile', {}) || {};
  const draft = readJson(draftKey, {}) || {};
  const split = value => String(value || '').split(',').map(item => item.trim()).filter(Boolean);
  return {
    ...classic,
    required_days_off: draft.requiredDays ? split(draft.requiredDays) : (classic.required_days_off || []),
    preferred_trip_lengths: draft.tripLengths ? split(draft.tripLengths) : (classic.preferred_trip_lengths || []),
    elite_cities: draft.layovers ? split(draft.layovers) : (classic.elite_cities || []),
    penalty_cities: draft.avoidLayovers ? split(draft.avoidLayovers) : (classic.penalty_cities || []),
    max_legs_per_day: draft.maxLegs || classic.max_legs_per_day,
    earliest_report: draft.earliestReport || null,
    latest_release: draft.latestRelease || null
  };
}

function planPage() {
  const ready = sessionJob?.status === 'complete';
  const draft = readJson(draftKey, {}) || {};
  const focus = ({ quality: 'Quality of life', days_off: 'Protect days off', layovers: 'Preferred layovers', credit: payGoalLabel(), commute: 'Commute-friendly trips' })[draft.focus] || 'Classic preference ranking';
  const planBody = navbluePlanError ? `<section class="surface labs-feature-empty"><h2>Bid plan could not be generated</h2><p>${escapeHtml(navbluePlanError)}</p><button class="primary" type="button" onclick="window.location.reload()">Try again</button></section>` : !navbluePlan ? `<section class="surface labs-loading"><strong>Building your NavBlue request layers...</strong><p>Translating your saved preferences into an ordered, pilot-reviewable bid.</p></section>` : `<section class="surface bid-plan navblue-plan">
      <div class="surface-title"><div><div><span class="kicker">NAVBLUE PBS REQUEST PLAN</span><h2>${escapeHtml(focus)}</h2><p>${escapeHtml(navbluePlan.request_count)} ordered requests derived from your Classic preferences and Labs draft.</p></div></div><span class="beta-badge">Draft</span></div>
      <div class="navblue-layer-list">${navbluePlan.layers.map(layer => `<article class="navblue-layer"><header><span>Layer ${escapeHtml(layer.number)}</span><h3>${escapeHtml(layer.title)}</h3></header><ol>${layer.requests.map(request => `<li><code>${escapeHtml(request.request)}</code><p>${escapeHtml(request.reason)}</p>${request.matching_trip_count !== undefined ? `<small>${escapeHtml(request.matching_trip_count)} trip${request.matching_trip_count === 1 ? '' : 's'} associated with this request</small>` : ''}</li>`).join('')}</ol></article>`).join('')}</div>
      <div class="labs-plan-note"><strong>Before you submit</strong>${navbluePlan.warnings.map(warning => `<p>${escapeHtml(warning)}</p>`).join('')}</div>
    </section>`;
  return `${pageHeader('PROPOSED BID PLAN', 'Build actual NavBlue request layers', 'Review an ordered PBS request strategy—not another list of pairings—and enter it in NavBlue only after pilot verification.')}
    ${packageCard()}
    ${!ready ? emptyFeature('A proposed plan needs Classic results') : planBody}
    <div class="labs-page-actions"><a class="secondary button" href="/labs/recommendations">Review supporting pairings</a><a class="text-button button" href="/">Return to Classic</a></div>`;
}

function southwestPage() {
  const ready = sessionJob?.status === 'complete' && sessionJob.airline === 'southwest';
  return `${pageHeader('SOUTHWEST LABS', 'Build a line strategy around your life', 'Upload a Southwest package here, rank lines with TFP-aware preferences, and prepare conflict analysis.')}
    ${packageCard()}
    ${uploadPanel()}
    ${postParseActions()}
    ${ready ? `<section class="labs-action-grid"><a id="schedule" href="/labs/build" class="labs-action-card primary-action"><span>01</span><h2>Set Line Preferences</h2><p>Define TFP, days off, workload, and overnight priorities.</p><strong>Open preferences</strong></a><a id="conflicts" href="/labs/recommendations" class="labs-action-card"><span>02</span><h2>Optimize Conflicts</h2><p>Review line recommendations against your current schedule and protected dates.</p><strong>Review lines</strong></a></section>` : `<section class="surface labs-feature-empty"><h2>Upload a Southwest package to begin</h2><p>Use one airline ZIP or the individual Pairings and Lines TXT files above.</p></section>`}`;
}

function render() {
  const pages = { landing: landingPage, build: builderPage, recommendations: recommendationsPage, preview: previewPage, plan: planPage, southwest: southwestPage };
  labsContent.innerHTML = (pages[labsPage] || landingPage)();
  const route = labsPage === 'landing' ? '/labs' : `/labs/${labsPage}`;
  document.querySelectorAll('[data-labs-route]').forEach(link => link.classList.toggle('active', link.dataset.labsRoute === route));
  bindBuilder();
  bindUploader();
}

function showLabsUploadError(message) {
  labsUploadError = message || '';
  const box = document.getElementById('labsUploadError');
  if (!box) return;
  box.textContent = labsUploadError;
  box.classList.toggle('hidden', !labsUploadError);
}

function syncLabsFilename(inputId, labelId) {
  const input = document.getElementById(inputId), label = document.getElementById(labelId);
  if (!input || !label) return;
  const file = input.files?.[0];
  if (file?.name) label.textContent = file.name;
}

function setLabsUploadBusyState(busy) {
  labsUploadBusy = busy;
  const button = document.getElementById('labsAnalyzePackage'), cancel = document.getElementById('labsCancelUpload');
  if (button) { button.disabled = busy; button.textContent = busy ? 'Uploading…' : 'Analyze in Labs'; }
  if (cancel) cancel.classList.toggle('hidden', !busy);
}

function selectedLabsFiles() {
  return {
    packageFile: document.getElementById('labsPackageFile')?.files?.[0] || null,
    pairingsFile: document.getElementById('labsPairingsFile')?.files?.[0] || null,
    linesFile: document.getElementById('labsLinesFile')?.files?.[0] || null,
    seniorityFile: document.getElementById('labsSeniorityFile')?.files?.[0] || null,
    coverFile: document.getElementById('labsCoverFile')?.files?.[0] || null
  };
}

async function submitLabsPackage(replaceConfirmed = false) {
  if (labsUploadBusy) return;
  const selector = document.getElementById('labsAirline');
  const files = selectedLabsFiles();
  const individualFiles = [files.pairingsFile, files.linesFile, files.seniorityFile, files.coverFile].filter(Boolean);
  let airline = selector?.value || 'auto';
  const extension = files.packageFile?.name?.toLowerCase().match(/\.[^.]+$/)?.[0] || '';
  if (airline === 'auto' && extension === '.zip') airline = 'southwest';

  showLabsUploadError('');
  if (files.packageFile && individualFiles.length) return showLabsUploadError('Choose either one package file or individual Southwest TXT files, not both.');
  if (airline === 'southwest') {
    if (files.packageFile && extension !== '.zip') return showLabsUploadError('Southwest combined uploads must be a ZIP containing Pairings and Lines.');
    if (!files.packageFile && !(files.pairingsFile && files.linesFile)) return showLabsUploadError('Choose a Southwest ZIP, or both the Pairings and Lines TXT files.');
  } else {
    if (!files.packageFile) return showLabsUploadError('Choose a bid-package PDF.');
    if (extension !== '.pdf') return showLabsUploadError('This airline selection requires a PDF bid package.');
  }
  const oversized = [files.packageFile, ...individualFiles].find(file => file && file.size > 100 * 1024 * 1024);
  if (oversized) return showLabsUploadError(`${oversized.name} exceeds the 100 MB upload limit.`);

  if (sessionJob && !replaceConfirmed) {
    document.getElementById('labsReplacePrompt')?.classList.remove('hidden');
    return;
  }

  setLabsUploadBusyState(true);
  const data = new FormData();
  data.append('airline', airline);
  data.append('context', 'labs');
  data.append('profile_json', JSON.stringify(mergedLabsProfile()));
  if (files.packageFile) data.append('file', files.packageFile);
  else {
    data.append('pairings_file', files.pairingsFile);
    data.append('lines_file', files.linesFile);
    if (files.seniorityFile) data.append('seniority_file', files.seniorityFile);
    if (files.coverFile) data.append('cover_file', files.coverFile);
  }
  labsUploadController = new AbortController();
  try {
    const response = await fetch('/api/jobs', { method: 'POST', body: data, signal: labsUploadController.signal, headers: { Accept: 'application/json' } });
    const responseText = await response.text();
    let body = {}; try { body = responseText ? JSON.parse(responseText) : {}; } catch (_) {}
    if (!response.ok) throw new Error(body.detail || `Upload failed (${response.status})`);
    if (!body.job_id) throw new Error('Upload finished, but the parsing job was not created.');
    localStorage.setItem(activeJobKey, body.job_id);
    localStorage.removeItem(latestJobKey);
    sessionJob = { ...body, status: 'queued', progress: 1, stage: 'detecting_package', stage_label: 'Detecting airline and package type', message: 'Upload received' };
    sessionLoading = false;
    navbluePlan = null; navbluePlanJob = null; navbluePlanError = '';
    labsUploadBusy = false; labsUploadController = null; labsUploadError = '';
    render();
    loadSharedSession();
  } catch (error) {
    const cancelled = error.name === 'AbortError';
    setLabsUploadBusyState(false);
    labsUploadController = null;
    showLabsUploadError(cancelled ? 'Upload canceled. The current package was not replaced.' : (error.message || 'Server error while uploading the package. Try again.'));
  }
}

function bindUploader() {
  const analyze = document.getElementById('labsAnalyzePackage');
  if (!analyze) return;
  const airline = document.getElementById('labsAirline');
  const southwestFiles = document.getElementById('labsSouthwestFiles');
  const toggleSouthwest = () => southwestFiles?.classList.toggle('hidden', airline.value !== 'southwest');
  airline.addEventListener('change', toggleSouthwest);
  toggleSouthwest();
  const bindings = [
    ['labsPackageFile', 'labsPackageFilename'], ['labsPairingsFile', 'labsPairingsFilename'],
    ['labsLinesFile', 'labsLinesFilename'], ['labsSeniorityFile', 'labsSeniorityFilename'], ['labsCoverFile', 'labsCoverFilename']
  ];
  bindings.forEach(([inputId, labelId]) => document.getElementById(inputId)?.addEventListener('change', () => { syncLabsFilename(inputId, labelId); showLabsUploadError(''); }));
  analyze.addEventListener('click', () => submitLabsPackage(false));
  document.getElementById('labsConfirmReplace')?.addEventListener('click', () => { document.getElementById('labsReplacePrompt')?.classList.add('hidden'); submitLabsPackage(true); });
  document.getElementById('labsCancelReplace')?.addEventListener('click', () => document.getElementById('labsReplacePrompt')?.classList.add('hidden'));
  document.getElementById('labsCancelUpload')?.addEventListener('click', () => labsUploadController?.abort());
}

function bindBuilder() {
  const button = document.getElementById('saveLabsDraft');
  if (!button) return;
  const draft = readJson(draftKey, {}) || {};
  document.getElementById('labsFocus').value = draft.focus || 'quality';
  const saveCurrentDraft = (showConfirmation = true) => {
    const saved = {
      focus: document.getElementById('labsFocus').value,
      requiredDays: document.getElementById('labsRequiredDays').value.trim(),
      tripLengths: document.getElementById('labsTripLengths').value.trim(),
      layovers: document.getElementById('labsLayovers').value.trim(),
      avoidLayovers: document.getElementById('labsAvoidLayovers').value.trim(),
      maxLegs: document.getElementById('labsMaxLegs').value,
      earliestReport: document.getElementById('labsEarliestReport').value,
      latestRelease: document.getElementById('labsLatestRelease').value,
      notes: document.getElementById('labsNotes').value.trim(),
      savedAt: new Date().toISOString()
    };
    localStorage.setItem(draftKey, JSON.stringify(saved));
    refinedRecommendationsSignature = '';
    if (!showConfirmation) return;
    const status = document.getElementById('draftStatus');
    status.textContent = 'Saved just now';
    button.textContent = 'Saved';
    setTimeout(() => { button.textContent = 'Save draft'; }, 1200);
  };
  button.addEventListener('click', () => saveCurrentDraft(true));
  document.getElementById('openLabsRecommendations')?.addEventListener('click', () => saveCurrentDraft(false));
}

async function loadRefinedRecommendations(jobId) {
  const profile = mergedLabsProfile();
  const signature = `${jobId}:${JSON.stringify(profile)}`;
  if (!jobId || refinedRecommendationsLoading || refinedRecommendationsSignature === signature) return;
  refinedRecommendationsLoading = true;
  refinedRecommendationsError = '';
  render();
  try {
    const data = new FormData();
    data.append('profile_json', JSON.stringify(profile));
    const response = await fetch(`/api/jobs/${jobId}/rescore`, { method: 'POST', body: data, headers: { Accept: 'application/json' } });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || 'Could not apply the saved trip preferences');
    sessionJob = { ...sessionJob, results: body.results || [], synopsis: body.synopsis || sessionJob.synopsis };
    refinedRecommendationsSignature = signature;
  } catch (error) {
    refinedRecommendationsError = error.message || 'Could not refresh recommendations';
  } finally {
    refinedRecommendationsLoading = false;
    render();
  }
}

async function loadNavbluePlan(jobId) {
  if (!jobId || navbluePlanJob === jobId) return;
  navbluePlanJob = jobId;
  navbluePlan = null;
  navbluePlanError = '';
  render();
  try {
    const response = await fetch(`/api/jobs/${jobId}/navblue-plan`, {
      method: 'POST',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
      body: JSON.stringify(mergedLabsProfile())
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || 'Could not build the NavBlue request plan');
    navbluePlan = body;
  } catch (error) {
    navbluePlanError = error.message || 'Could not build the NavBlue request plan';
  }
  render();
}

async function loadSharedSession() {
  const jobId = currentJobId();
  if (!jobId) { sessionLoading = false; render(); return; }
  try {
    const response = await fetch(`/api/jobs/${jobId}`, { headers: { Accept: 'application/json' } });
    if (!response.ok) throw new Error('Stored analysis is unavailable');
    sessionJob = await response.json();
    if (sessionJob.status === 'complete') {
      localStorage.setItem(latestJobKey, jobId);
      localStorage.removeItem(activeJobKey);
    }
    sessionLoading = false;
    render();
    if (labsPage === 'recommendations' && sessionJob.status === 'complete') loadRefinedRecommendations(jobId);
    if (labsPage === 'plan' && sessionJob.status === 'complete') loadNavbluePlan(jobId);
    if (sessionJob.status === 'queued' || sessionJob.status === 'processing') setTimeout(loadSharedSession, 2000);
  } catch (_) {
    if (localStorage.getItem(latestJobKey) === jobId) localStorage.removeItem(latestJobKey);
    sessionJob = null;
    sessionLoading = false;
    render();
  }
}

document.documentElement.dataset.theme = 'dark';
render();
loadSharedSession();
