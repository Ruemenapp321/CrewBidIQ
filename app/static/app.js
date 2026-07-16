
const $ = id => document.getElementById(id);
let activeJob = null;
let pollTimer = null;

function listValue(id) {
  return $(id).value.split(",").map(x => x.trim().toUpperCase()).filter(Boolean);
}
function num(id) { return Number($(id).value || 0); }
function timeMinutes(id, fallback) {
  const value = $(id).value;
  if (!value) return fallback;
  const [h,m] = value.split(":").map(Number);
  return h*60+m;
}
function profile() {
  return {
    airline: $("airline").value,
    fleet: $("fleet").value,
    base_airport: $("baseAirport").value.trim().toUpperCase(),
    bid_month: $("bidMonth").value,
    elite_cities: listValue("eliteCities"),
    secondary_cities: listValue("secondaryCities"),
    small_cities: listValue("smallCities"),
    penalty_cities: listValue("penaltyCities"),
    preferred_aircraft: listValue("preferredAircraft"),
    max_deadheads: num("maxDeadheads"),
    max_transfers: num("maxTransfers"),
    preferred_trip_lengths: listValue("preferredTripLengths"),
    max_legs_per_day: num("maxLegsPerDay"),
    min_layover_hours: num("minLayoverHours"),
    required_days_off: listValue("requiredDaysOff"),
    preferred_days_off: listValue("preferredDaysOff"),
    holiday_dates: listValue("holidayDates"),
    preferred_weekdays: listValue("preferredWeekdays"),
    max_consecutive_work_days: num("maxConsecutiveWorkDays"),
    min_consecutive_days_off: num("minConsecutiveDaysOff"),
    earliest_report_minutes: timeMinutes("earliestReport",360),
    latest_release_minutes: timeMinutes("latestRelease",1320),
    commuter_latest_checkin_minutes: timeMinutes("commuterLatestCheckin",510),
    commuter_earliest_release_minutes: timeMinutes("commuterEarliestRelease",1260),
    prefer_weekends_off: $("preferWeekendsOff").checked,
    avoid_holidays: $("avoidHolidays").checked,
    allow_productive_redeye: $("allowProductiveRedeye").checked,
    avoid_final_redeye: $("avoidFinalRedeye").checked,
    avoid_reserve: $("avoidReserve").checked,
    prefer_operate: $("preferOperate").checked,
    weights: {
      elite:num("wElite"), secondary:num("wSecondary"), small:num("wSmall"),
      penalty:num("wPenalty"), aircraft:num("wAircraft"), pure:num("wPure"),
      transfer:num("wTransfer"), deadhead:num("wDeadhead"),
      required_conflict:num("wRequiredConflict"),
      preferred_conflict:num("wPreferredConflict"),
      holiday_conflict:num("wHolidayConflict"),
      early_report:num("wEarlyReport"), late_release:num("wLateRelease")
    }
  };
}

function setJob(show, status="", progress=0, message="") {
  $("jobPanel").classList.toggle("hidden", !show);
  $("jobStatus").textContent = status;
  $("jobPercent").textContent = `${progress}%`;
  $("progressFill").style.width = `${progress}%`;
  $("jobMessage").textContent = message;
}
function showError(message) {
  $("errorBox").textContent = message;
  $("errorBox").classList.remove("hidden");
}
function clearError() { $("errorBox").classList.add("hidden"); }

$("analyzeBtn").addEventListener("click", async () => {
  clearError();
  const file = $("file").files[0];
  if (!file) { alert("Choose a bid package first."); return; }

  const data = new FormData();
  data.append("file", file);
  data.append("context", `${$("airline").value} ${$("fleet").value} ${$("baseAirport").value}`.trim());
  data.append("profile_json", JSON.stringify(profile()));
  data.append("parser_choice", $("parserChoice").value);

  $("analyzeBtn").disabled = true;
  setJob(true, "Uploading", 1, "Sending file to CrewBidIQ");

  try {
    const response = await fetch("/api/jobs", {method:"POST", body:data});
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || "Upload failed");
    activeJob = body.job_id;
    pollTimer = setInterval(pollJob, 1500);
    await pollJob();
  } catch (error) {
    showError(error.message);
    setJob(false);
    $("analyzeBtn").disabled = false;
  }
});

async function pollJob() {
  if (!activeJob) return;
  try {
    const response = await fetch(`/api/jobs/${activeJob}`);
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || "Could not read job status");

    setJob(true, body.status, body.progress, body.message || "");

    if (body.status === "complete") {
      clearInterval(pollTimer);
      $("analyzeBtn").disabled = false;
      render(body.results || []);
      $("summary").textContent = `${body.results.length} pairings ranked from ${body.filename}.`;
      $("csvLink").href = `/api/jobs/${activeJob}/csv`;
      $("csvLink").classList.remove("disabled");
    } else if (body.status === "failed") {
      clearInterval(pollTimer);
      $("analyzeBtn").disabled = false;
      showError(body.error || "Analysis failed");
    }
  } catch (error) {
    clearInterval(pollTimer);
    $("analyzeBtn").disabled = false;
    showError(error.message);
  }
}

function render(results) {
  const tbody = $("results");
  tbody.innerHTML = "";
  results.forEach((item, index) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${index+1}</td>
      <td><strong>${item.pairing}</strong></td>
      <td class="score">${item.score}</td>
      <td>${item.parser} (${Math.round((item.parser_confidence||0)*100)}%)</td>
      <td>${item.credit || "—"}</td>
      <td>${item.tafb || "—"}</td>
      <td>${item.dates.join(", ") || "—"}</td>
      <td>${item.cities.join(", ")}</td>
      <td>${(item.layovers||[]).map(x=>`${x.city}${x.duration?` ${x.duration}`:""}`).join(", ") || "—"}</td>
      <td>${item.preferred_aircraft.join(", ") || "—"}</td>
      <td>${item.redeye}</td>
      <td>${item.deadheads}</td>
      <td>${item.transfers.join(", ") || "—"}</td>
      <td>${item.calendar_conflicts.join("; ") || "—"}</td>
      <td>${item.reasons.join("; ")}</td>`;
    tbody.appendChild(tr);
  });
}

$("saveProfileBtn").addEventListener("click", () => {
  localStorage.setItem("crewbidiqProfile", JSON.stringify(profile()));
  alert("Profile saved on this device.");
});
$("printBtn").addEventListener("click", () => window.print());
$("demoBtn").addEventListener("click", () => {
  render([
    {pairing:"DEMO1",score:184,parser:"demo",parser_confidence:1,credit:"20:15",tafb:"72:00",layovers:[{city:"LAX",duration:"16:00"},{city:"BOS",duration:"18:00"}],dates:["2026-08-03","2026-08-04"],cities:["ATL","LAX","BOS"],preferred_aircraft:["NEO"],redeye:"none",deadheads:0,transfers:[],calendar_conflicts:[],reasons:["LAX: elite","BOS: elite","all-operated signal"]},
    {pairing:"DEMO2",score:92,parser:"demo",parser_confidence:1,credit:"12:30",tafb:"34:00",layovers:[{city:"SAV",duration:"14:00"}],dates:["2026-08-10"],cities:["ATL","DFW","SAV"],preferred_aircraft:[],redeye:"none",deadheads:1,transfers:[],calendar_conflicts:[],reasons:["DFW: penalty","SAV: interesting"]}
  ]);
  $("summary").textContent = "Demo results loaded.";
});
