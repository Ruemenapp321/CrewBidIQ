const labsContent = document.getElementById('labsContent');
const labsPage = window.CREWBIDIQ_LABS_PAGE || 'landing';
const latestJobKey = 'crewbidiqLatestJob';
const activeJobKey = 'crewbidiqActiveJob';
const draftKey = 'crewbidiqLabsDraft';
let sessionJob = null;
let sessionLoading = true;

const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, character => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
}[character]));

function readJson(key, fallback = null) {
  try { return JSON.parse(localStorage.getItem(key) || 'null') ?? fallback; }
  catch (_) { return fallback; }
}

function airlineName(value) {
  return ({ delta: 'Delta Air Lines', american: 'American Airlines', southwest: 'Southwest Airlines', generic: 'Other airline' })[value] || value || 'Airline unavailable';
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
    return `<section class="surface no-package"><div><span class="kicker">SHARED SESSION</span><h2>No bid package loaded</h2><p>Analyze a package in Classic once. Labs will use the same parsed trips in this browser session.</p></div><a class="primary button" href="/#upload">Upload in Classic</a></section>`;
  }
  const complete = sessionJob.status === 'complete';
  const status = complete ? 'Ready for Labs' : (sessionJob.status === 'failed' ? 'Analysis needs attention' : 'Classic analysis in progress');
  return `<section class="surface package-status ${escapeHtml(sessionJob.status)}">
    <div class="status-light"></div>
    <div class="package-status-main"><span>Current bid package</span><strong>${escapeHtml(sessionJob.filename || 'Uploaded package')}</strong><small>${escapeHtml(airlineName(sessionJob.airline))} · ${escapeHtml(inferredBidMonth(sessionJob.filename))}</small></div>
    <div class="package-status-state"><span>${escapeHtml(status)}</span><strong>${escapeHtml(sessionJob.progress ?? 0)}%</strong></div>
  </section>`;
}

function landingPage() {
  const hasDraft = Boolean(readJson(draftKey));
  return `${pageHeader('CREWBIDIQ LABS', 'Experimental bidding tools', 'Explore a guided path from your parsed bid package to a clear, pilot-ready bid plan.')}
    <section class="labs-beta-notice"><span>Beta</span><p>Labs features are experimental. Review any proposed bid plan before using it with your airline bidding system.</p></section>
    ${packageCard()}
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
    <section class="surface labs-builder">
      <div class="surface-title"><div><span class="labs-step">1</span><div><h2>Define your month</h2><p>Start with what matters most. You can refine the details later.</p></div></div><span id="draftStatus" class="draft-status">Draft on this device</span></div>
      <div class="labs-form-grid">
        <label>Primary goal<select id="labsFocus"><option value="quality">Quality of life</option><option value="days_off">Protect days off</option><option value="layovers">Preferred layovers</option><option value="credit">Credit and efficiency</option><option value="commute">Commute-friendly trips</option></select></label>
        <label>Required days off<input id="labsRequiredDays" value="${escapeHtml(value('requiredDays', 'required_days_off'))}" placeholder="8/11, 8/18"></label>
        <label>Preferred trip lengths<input id="labsTripLengths" value="${escapeHtml(value('tripLengths', 'preferred_trip_lengths'))}" placeholder="2, 3, 4"></label>
        <label>Highest-priority layovers<input id="labsLayovers" value="${escapeHtml(value('layovers', 'elite_cities'))}" placeholder="HNL, OGG, LIH"></label>
        <label>Avoid layovers<input id="labsAvoidLayovers" value="${escapeHtml(value('avoidLayovers', 'penalty_cities'))}" placeholder="DFW, IAH"></label>
        <label>Maximum legs per duty day<input id="labsMaxLegs" type="number" min="1" value="${escapeHtml(value('maxLegs', 'max_legs_per_day'))}" placeholder="3"></label>
        <label>Earliest report<input id="labsEarliestReport" type="time" value="${escapeHtml(value('earliestReport'))}"></label>
        <label>Latest release<input id="labsLatestRelease" type="time" value="${escapeHtml(value('latestRelease'))}"></label>
      </div>
      <label class="labs-notes">What would make this a successful month?<textarea id="labsNotes" placeholder="Example: Protect my daughter's birthday and favor longer Hawaii layovers.">${escapeHtml(value('notes'))}</textarea></label>
      <div class="labs-builder-actions"><button id="saveLabsDraft" class="secondary">Save draft</button><a class="primary button" href="/labs/recommendations">Refine recommendations</a></div>
    </section>
    <section class="surface labs-next-step"><div><span class="labs-step">2</span><div><h2>Ready for a proposed plan?</h2><p>Review the available trips first, then arrange your strongest options into a working bid order.</p></div></div><a class="text-button button" href="/labs/plan">Open bid plan</a></section>`;
}

function emptyFeature(message) {
  return `<section class="surface labs-feature-empty"><h2>${escapeHtml(message)}</h2><p>Labs needs the completed Classic analysis so it can work from the same parsed bid package.</p><a class="primary button" href="/#upload">Upload in Classic</a></section>`;
}

function matchLabel(item) {
  return ({ excellent: 'Excellent', strong: 'Strong', good: 'Good', fair: 'Fair', low: 'Low' })[item.match_level] || 'Match';
}

function recommendationCards(results) {
  return results.slice(0, 8).map((item, index) => {
    const layovers = (item.layovers || []).map(layover => layover.city).join(', ') || 'No overnights';
    const reasons = (item.reasons || []).slice(0, 3);
    return `<article class="labs-recommendation">
      <div class="labs-rank">${index + 1}</div>
      <div><span>${escapeHtml(item.display_label || 'Trip')} ${escapeHtml(item.pairing)}</span><h3>${escapeHtml(layovers)}</h3><p>${reasons.length ? reasons.map(escapeHtml).join(' · ') : 'No strong preference signals were detected.'}</p></div>
      <div class="labs-recommendation-metrics"><strong>${escapeHtml(matchLabel(item))}</strong><span>${escapeHtml(item.credit || '—')} credit</span></div>
    </article>`;
  }).join('');
}

function recommendationsPage() {
  const ready = sessionJob?.status === 'complete';
  const results = sessionJob?.results || [];
  return `${pageHeader('REFINED RECOMMENDATIONS', 'See the trips worth your attention', 'A quieter review of the strongest recommendations from your current Classic preferences.')}
    ${packageCard()}
    ${!ready ? emptyFeature('Complete a Classic analysis first') : `<section class="surface labs-recommendations-panel"><div class="surface-title"><div><div><h2>Priority review</h2><p>${escapeHtml(results.length)} analyzed trips · showing the first ${Math.min(results.length, 8)}</p></div></div><a class="text-button button" href="/results">Open full Classic results</a></div><div class="labs-recommendation-list">${recommendationCards(results)}</div></section>`}
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
      <article><span>Total trips</span><strong>${escapeHtml(synopsis.total || 0)}</strong><small>Parsed from this package</small></article>
      <article><span>Contain redeyes</span><strong>${escapeHtml(synopsis.redeye?.percent || 0)}%</strong><small>${escapeHtml(synopsis.redeye?.count || 0)} trips</small></article>
      <article><span>Contain deadheads</span><strong>${escapeHtml(synopsis.deadhead?.percent || 0)}%</strong><small>${escapeHtml(synopsis.deadhead?.count || 0)} trips</small></article>
      <article><span>Incomplete data</span><strong>${escapeHtml(synopsis.incomplete || 0)}</strong><small>Kept out of top ranks</small></article>
    </section>
    <section class="labs-preview-grid">
      <article class="surface"><h2>Trip lengths</h2>${compactBreakdown(synopsis.trip_lengths, 'days', '-day')}</article>
      <article class="surface"><h2>Start airports</h2>${compactBreakdown(synopsis.start_airports, 'airport')}</article>
      <article class="surface"><h2>Fleet mix</h2>${compactBreakdown(synopsis.fleets, 'fleet')}</article>
      <article class="surface"><h2>Top overnight cities</h2>${compactBreakdown(synopsis.layover_cities, 'city')}</article>
    </section>`}
    <div class="labs-page-actions"><a class="secondary button" href="/labs/build">Set bid priorities</a><a class="primary button" href="/labs/recommendations">View recommendations</a></div>`;
}

function planPage() {
  const ready = sessionJob?.status === 'complete';
  const results = sessionJob?.results || [];
  const draft = readJson(draftKey, {});
  const focus = ({ quality: 'Quality of life', days_off: 'Protect days off', layovers: 'Preferred layovers', credit: 'Credit and efficiency', commute: 'Commute-friendly trips' })[draft?.focus] || 'Classic preference ranking';
  return `${pageHeader('PROPOSED BID PLAN', 'Turn strong trips into a working order', 'Use this pilot-reviewed draft as a starting point, not an automatic airline submission.')}
    ${packageCard()}
    ${!ready ? emptyFeature('A proposed plan needs Classic results') : `<section class="surface bid-plan">
      <div class="surface-title"><div><div><span class="kicker">PLAN FOCUS</span><h2>${escapeHtml(focus)}</h2><p>Built from the top recommendations currently available in this browser session.</p></div></div><span class="beta-badge">Draft</span></div>
      <ol class="bid-plan-list">${results.slice(0, 10).map((item, index) => {
        const layovers = (item.layovers || []).map(layover => layover.city).join(', ') || 'No overnights';
        return `<li><span>${index + 1}</span><div><strong>${escapeHtml(item.display_label || 'Trip')} ${escapeHtml(item.pairing)}</strong><small>${escapeHtml(layovers)} · ${escapeHtml(item.credit || '—')} credit</small></div><em>${escapeHtml(matchLabel(item))}</em></li>`;
      }).join('')}</ol>
      <div class="labs-plan-note"><strong>Before you submit</strong><p>Confirm dates, report times, legality, and airline-specific bidding rules against the original bid package.</p></div>
    </section>`}
    <div class="labs-page-actions"><a class="secondary button" href="/labs/recommendations">Review recommendations</a><a class="text-button button" href="/">Return to Classic</a></div>`;
}

function render() {
  const pages = { landing: landingPage, build: builderPage, recommendations: recommendationsPage, preview: previewPage, plan: planPage };
  labsContent.innerHTML = (pages[labsPage] || landingPage)();
  const route = labsPage === 'landing' ? '/labs' : `/labs/${labsPage}`;
  document.querySelectorAll('[data-labs-route]').forEach(link => link.classList.toggle('active', link.dataset.labsRoute === route));
  bindBuilder();
}

function bindBuilder() {
  const button = document.getElementById('saveLabsDraft');
  if (!button) return;
  const draft = readJson(draftKey, {}) || {};
  document.getElementById('labsFocus').value = draft.focus || 'quality';
  button.addEventListener('click', () => {
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
    const status = document.getElementById('draftStatus');
    status.textContent = 'Saved just now';
    button.textContent = 'Saved';
    setTimeout(() => { button.textContent = 'Save draft'; }, 1200);
  });
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
