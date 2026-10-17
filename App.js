// Report EC dashboard
//
// While the bot and backend are being built, the dashboard runs on the sample
// reports in sample-data.js. When the backend is ready, set USE_SAMPLE_DATA to
// false and the same page will read from GET /reports and send updates with
// PATCH /reports/{ref}.

const USE_SAMPLE_DATA = false;
const API_BASE = "";            // empty = same server as FastAPI
const REFRESH_SECONDS = 15;     // how often to check for new reports (API mode)
const OVERDUE_HOURS = 48;       // no update for this long = overdue
const PAGE_SIZE = 6;            // rows per page in the issue queue

// ---------- Labels and icons ----------

const CATEGORIES = {
  water_leak: "Water leak",
  no_water: "No water",
  power_outage: "Power outage",
  pothole: "Pothole",
  streetlight: "Streetlight",
  other: "Other",
};

const PRIORITIES = {
  critical: { label: "Critical", rank: 0 },
  high: { label: "High", rank: 1 },
  medium: { label: "Medium", rank: 2 },
};

const STATUSES = {
  received: "Received",
  awaiting_dispatch: "Awaiting dispatch",
  team_assigned: "Team assigned",
  in_progress: "In progress",
  resolved: "Resolved",
};

// Small line icons for each category (24 x 24)
const CATEGORY_ICONS = {
  water_leak: '<path d="M12 3s-6 6.5-6 11a6 6 0 0 0 12 0c0-4.5-6-11-6-11z"/>',
  no_water: '<path d="M12 3s-6 6.5-6 11a6 6 0 0 0 12 0c0-4.5-6-11-6-11z"/><path d="M4 4l16 16"/>',
  power_outage: '<path d="M13 2L4 14h7l-1 8 9-12h-7l1-8z"/>',
  pothole: '<path d="M9 4h6l4 15H5L9 4z"/><path d="M3 20h18"/><path d="M7.5 10h9"/>',
  streetlight: '<path d="M9 18h6"/><path d="M10 21h4"/><path d="M12 3a6.5 6.5 0 0 0-3.7 11.8V16h7.4v-1.2A6.5 6.5 0 0 0 12 3z"/>',
  other: '<circle cx="5" cy="12" r="1.4"/><circle cx="12" cy="12" r="1.4"/><circle cx="19" cy="12" r="1.4"/>',
};

const ICONS = {
  pin: '<svg class="icon" viewBox="0 0 24 24"><path d="M12 21s-7-6.2-7-11.5A7 7 0 0 1 19 9.5C19 14.8 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.5"/></svg>',
  people: '<svg class="icon" viewBox="0 0 24 24"><circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M16 4.5a3.5 3.5 0 0 1 0 7"/><path d="M18 14a6.5 6.5 0 0 1 3.5 6"/></svg>',
  layers: '<svg class="icon" viewBox="0 0 24 24"><path d="M12 3l9 5-9 5-9-5 9-5z"/><path d="M3 13l9 5 9-5"/></svg>',
  alarm: '<svg class="icon" viewBox="0 0 24 24"><circle cx="12" cy="13" r="8"/><path d="M12 9v4l2.5 2.5"/><path d="M5 3L2.5 5.5M19 3l2.5 2.5"/></svg>',
  translate: '<svg class="icon" viewBox="0 0 24 24"><path d="M4 5h9M8.5 3v2M6 5c1 4 4 7 7 8"/><path d="M11 5c-1 4-4 7-7 8"/><path d="M13 21l4-9 4 9M14.5 18h5"/></svg>',
  plus: '<svg class="icon" viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>',
};

function categoryIcon(category) {
  const paths = CATEGORY_ICONS[category] || CATEGORY_ICONS.other;
  return `<svg viewBox="0 0 24 24">${paths}</svg>`;
}

// ---------- State ----------

const state = {
  reports: [],
  selectedRef: null,
  page: 1,
  filters: { category: "all", priority: "all", status: "active", search: "" },
};

let map = null;
let markerLayer = null;
let hasFittedMap = false;
let messageEditedByUser = false;

// ---------- Helpers ----------

// Resident messages come from WhatsApp, so never put them into the page as raw HTML
function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function hoursSince(date) {
  return (Date.now() - date.getTime()) / 3600000;
}

function timeAgo(date) {
  const minutes = Math.round((Date.now() - date.getTime()) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function formatDateTime(date) {
  return date.toLocaleString("en-ZA", {
    timeZone: "Africa/Johannesburg",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatDuration(hours) {
  if (hours < 1) return `${Math.round(hours * 60)} min`;
  return `${hours.toFixed(1)} h`;
}

function toDate(value) {
  return value ? new Date(value) : null;
}

// Converts a report from the sample file or the backend into one shape.
// The backend may use the database column names (latitude, urgency, landmark),
// so both spellings are accepted here.
function normaliseReport(raw, idToRef) {
  let duplicateOf = raw.duplicate_of ?? null;
  if (duplicateOf !== null && idToRef[duplicateOf]) duplicateOf = idToRef[duplicateOf];

  return {
    ref: raw.ref,
    category: raw.category || "other",
    title: raw.title || CATEGORIES[raw.category] || "Report",
    street: raw.street || raw.landmark || "Location shared on WhatsApp",
    suburb: raw.suburb || "",
    ward: raw.ward ? String(raw.ward) : "",
    lat: Number(raw.lat ?? raw.latitude),
    lng: Number(raw.lng ?? raw.longitude),
    priority: raw.priority || raw.urgency || "medium",
    status: raw.status || "received",
    team: raw.team || "Not assigned",
    language: raw.language || "en",
    description: raw.description || "",
    translation: raw.translation || null,
    channel: raw.channel || "whatsapp",
    photoUrl: raw.photo_url || null,
    duplicateOf,
    createdAt: toDate(raw.created_at),
    updatedAt: toDate(raw.updated_at || raw.created_at),
    firstResponseAt: toDate(raw.first_response_at),
    resolvedAt: toDate(raw.resolved_at),
    updates: (raw.updates || []).map((u) => ({ at: toDate(u.at), text: u.text })),
  };
}

function isActive(report) {
  return report.status !== "resolved";
}

function isOverdue(report) {
  return isActive(report) && hoursSince(report.updatedAt) >= OVERDUE_HOURS;
}

function hasLocation(report) {
  return Number.isFinite(report.lat) && Number.isFinite(report.lng);
}

// Only the first report of each issue shows in the queue; the rest are linked to it
function mainReports() {
  return state.reports.filter((r) => !r.duplicateOf);
}

function linkedReports(ref) {
  return state.reports.filter((r) => r.duplicateOf === ref);
}

function groupSize(report) {
  return 1 + linkedReports(report.ref).length;
}

function locationText(report) {
  const parts = [report.street, report.suburb].filter(Boolean).join(", ");
  return report.ward ? `${parts} · Ward ${report.ward}` : parts;
}

function findReport(ref) {
  return state.reports.find((r) => r.ref === ref);
}

// ---------- Loading data ----------

async function loadReports() {
  let rawReports;

  if (USE_SAMPLE_DATA) {
    rawReports = window.SAMPLE_REPORTS || [];
  } else {
    try {
      const response = await fetch(`${API_BASE}/reports`);
      if (!response.ok) throw new Error(`Server replied ${response.status}`);
      rawReports = await response.json();
    } catch (error) {
      console.error(error);
      showToast("Could not load reports from the server. Is the backend running?", true);
      return;
    }
  }

  const idToRef = {};
  rawReports.forEach((r) => {
    if (r.id !== undefined) idToRef[r.id] = r.ref;
  });

  state.reports = rawReports.map((r) => normaliseReport(r, idToRef));

  // Keep the current selection if it still exists, otherwise pick the top of the queue
  if (!state.selectedRef || !findReport(state.selectedRef)) {
    const first = filteredReports()[0];
    state.selectedRef = first ? first.ref : null;
  }

  renderAll();
}

// ---------- Filtering and sorting ----------

function filteredReports() {
  const { category, priority, status, search } = state.filters;
  const term = search.trim().toLowerCase();

  return mainReports()
    .filter((r) => category === "all" || r.category === category)
    .filter((r) => priority === "all" || r.priority === priority)
    .filter((r) => {
      if (status === "all") return true;
      if (status === "active") return isActive(r);
      if (status === "overdue") return isOverdue(r);
      return r.status === status;
    })
    .filter((r) => {
      if (!term) return true;
      const haystack = [r.ref, r.title, r.street, r.suburb, `ward ${r.ward}`, CATEGORIES[r.category]]
        .join(" ")
        .toLowerCase();
      return haystack.includes(term);
    })
    .sort((a, b) => {
      // Resolved last, then priority, then overdue first, then oldest update first
      if (isActive(a) !== isActive(b)) return isActive(a) ? -1 : 1;
      const rankDiff = (PRIORITIES[a.priority]?.rank ?? 9) - (PRIORITIES[b.priority]?.rank ?? 9);
      if (rankDiff !== 0) return rankDiff;
      if (isOverdue(a) !== isOverdue(b)) return isOverdue(a) ? -1 : 1;
      return a.updatedAt - b.updatedAt;
    });
}

// ---------- Rendering ----------

function renderAll() {
  renderStats();
  renderQueue();
  renderMap();
  renderDetails();
}

function renderStats() {
  const main = mainReports();
  const active = main.filter(isActive);
  const wards = new Set(active.map((r) => r.ward).filter(Boolean));
  const overdue = active.filter(isOverdue);
  const critical = active.filter((r) => r.priority === "critical");

  const responded = main.filter((r) => r.firstResponseAt && r.createdAt);
  const avgHours = responded.length
    ? responded.reduce((sum, r) => sum + (r.firstResponseAt - r.createdAt) / 3600000, 0) / responded.length
    : null;

  document.getElementById("stat-active").textContent = active.length;
  document.getElementById("stat-active-sub").textContent =
    `Across ${wards.size} ward${wards.size === 1 ? "" : "s"}`;
  document.getElementById("stat-critical").textContent = critical.length;
  document.getElementById("stat-overdue").textContent = overdue.length;
  document.getElementById("stat-response").textContent = avgHours === null ? "–" : formatDuration(avgHours);

  document.getElementById("nav-all-count").textContent = main.length;
  document.getElementById("nav-overdue-count").textContent = overdue.length;
}

function priorityBadge(priority) {
  const label = PRIORITIES[priority]?.label || priority;
  return `<span class="badge badge-${escapeHtml(priority)}">${escapeHtml(label)}</span>`;
}

function renderQueue() {
  const rows = filteredReports();
  const pageCount = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  state.page = Math.min(state.page, pageCount);

  const start = (state.page - 1) * PAGE_SIZE;
  const pageRows = rows.slice(start, start + PAGE_SIZE);
  const body = document.getElementById("queue-body");

  if (pageRows.length === 0) {
    body.innerHTML = '<tr class="queue-empty"><td colspan="6">No reports match these filters.</td></tr>';
  } else {
    body.innerHTML = pageRows
      .map((r) => {
        const overdue = isOverdue(r);
        const lastUpdate = overdue
          ? `<span class="overdue-text">${Math.floor(hoursSince(r.updatedAt))}h · Overdue</span>`
          : escapeHtml(timeAgo(r.updatedAt));

        return `
          <tr data-ref="${escapeHtml(r.ref)}" tabindex="0" class="${r.ref === state.selectedRef ? "is-selected" : ""}">
            <td>
              <div class="ticket">
                <span class="ticket-icon">${categoryIcon(r.category)}</span>
                <div>
                  <div class="ticket-title">${escapeHtml(r.ref)} · ${escapeHtml(r.title)}</div>
                  <div class="ticket-sub">${escapeHtml(locationText(r))}</div>
                </div>
              </div>
            </td>
            <td>${priorityBadge(r.priority)}</td>
            <td>${escapeHtml(STATUSES[r.status] || r.status)}</td>
            <td>${lastUpdate}</td>
            <td><span class="reports-cell">${ICONS.people}${groupSize(r)}</span></td>
            <td class="chevron">›</td>
          </tr>`;
      })
      .join("");
  }

  const groupedCount = rows.reduce((sum, r) => sum + groupSize(r), 0);
  document.getElementById("queue-count").textContent = `${rows.length} shown`;
  document.getElementById("queue-summary").textContent = rows.length
    ? `Showing ${start + 1}–${start + pageRows.length} of ${rows.length} issues · ${groupedCount} resident reports, grouped not duplicated`
    : "";
  document.getElementById("page-label").textContent = `${state.page} / ${pageCount}`;
  document.getElementById("prev-page").disabled = state.page <= 1;
  document.getElementById("next-page").disabled = state.page >= pageCount;
}

function initMap() {
  const mapElement = document.getElementById("map");

  if (!window.L) {
    mapElement.innerHTML = '<div class="map-empty">The map could not load. Check the internet connection.</div>';
    return;
  }

  // zoomSnap 0.5 lets the map fit the pins more tightly than whole zoom levels
  map = L.map(mapElement, { scrollWheelZoom: false, zoomSnap: 0.5 }).setView([-33.865, 25.575], 13);

  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap contributors",
  }).addTo(map);

  markerLayer = L.layerGroup().addTo(map);
}

function renderMap() {
  if (!map) return;

  markerLayer.clearLayers();
  const reports = filteredReports().filter(hasLocation);

  reports.forEach((r) => {
    const size = groupSize(r);
    const colour = isActive(r) ? r.priority : "resolved";
    const selected = r.ref === state.selectedRef;
    const inner = size > 1 ? size : categoryIcon(r.category);

    const icon = L.divIcon({
      className: "pin-wrap",
      html: `<div class="pin pin-${colour}${selected ? " is-selected" : ""}">${inner}</div>`,
      iconSize: [30, 30],
      iconAnchor: [15, 15],
    });

    L.marker([r.lat, r.lng], { icon, zIndexOffset: selected ? 1000 : 0 })
      .bindTooltip(
        `<strong>${escapeHtml(r.ref)} · ${escapeHtml(PRIORITIES[r.priority]?.label || "")}</strong>${escapeHtml(r.title)}` +
          (size > 1 ? ` · ${size} reports` : ""),
        { direction: "top", offset: [0, -14], className: "pin-tip" }
      )
      .on("click", () => selectReport(r.ref, false))
      .addTo(markerLayer);
  });

  if (!hasFittedMap && reports.length) {
    fitMapTo(reports);
    hasFittedMap = true;
  }
}

function fitMapTo(reports) {
  if (!map || !reports.length) return;
  const bounds = L.latLngBounds(reports.map((r) => [r.lat, r.lng]));
  map.fitBounds(bounds, { padding: [40, 40], maxZoom: 15 });
}

function renderDetails() {
  const panel = document.getElementById("details");
  const report = findReport(state.selectedRef);

  if (!report) {
    panel.innerHTML = `
      <div class="details-head"><h3>Issue details</h3></div>
      <p class="details-empty">Select a report on the map or in the queue to see its details.</p>`;
    return;
  }

  const linked = linkedReports(report.ref);
  const size = linked.length + 1;
  const overdue = isOverdue(report);

  const statusChip = isActive(report)
    ? `<span class="badge badge-status">${escapeHtml(STATUSES[report.status] || report.status)}</span>`
    : `<span class="badge badge-resolved">Resolved</span>`;
  const overdueChip = overdue ? '<span class="badge badge-overdue">Update overdue</span>' : "";

  const photo = report.photoUrl
    ? `<div class="photo"><img src="${escapeHtml(report.photoUrl)}" alt="Photo sent by the resident"></div>`
    : `<div class="photo"><div class="photo-placeholder">${categoryIcon(report.category)}The resident’s WhatsApp photo will appear here</div></div>`;

  const translation =
    report.language !== "en" && report.translation
      ? `<div class="info-box">
           <h4>${ICONS.translate}Language-assisted triage</h4>
           <p>isiXhosa → English: “${escapeHtml(report.translation)}”</p>
           <p class="muted">Suggested translation. Staff can review it.</p>
         </div>`
      : "";

  const group =
    size > 1
      ? `<div class="info-box">
           <h4>${ICONS.layers}${size} neighbours. One shared issue.</h4>
           <p class="muted">Same category, close together. New nearby reports join ${escapeHtml(report.ref)} instead of creating extra jobs.</p>
           <p class="linked">Linked: ${linked.map((l) => escapeHtml(l.ref)).join(" · ")}</p>
         </div>`
      : "";

  const escalation = overdue
    ? `<div class="info-box info-box-warning">
         <h4>${ICONS.alarm}48h rule · Automatically escalated</h4>
         <p>No service update for ${Math.floor(hoursSince(report.updatedAt))} hours. Escalated to the Ward ${escapeHtml(report.ward)} councillor.</p>
         <p class="simulated">Email and SMS: simulated, not sent.</p>
         <p class="muted">An escalation is not a repair or a dispatch confirmation.</p>
       </div>`
    : "";

  const timeline = [...report.updates]
    .sort((a, b) => b.at - a.at)
    .map((u) => `<li><time>${escapeHtml(formatDateTime(u.at))}</time>${escapeHtml(u.text)}</li>`)
    .join("");

  // "Burst pipe on Njoli Road" reads well; "on Near the sports field" does not
  const streetInTitle = /\b(road|street|avenue|drive|lane|way)$/i.test(report.street);
  const heading = streetInTitle ? `${report.title} on ${report.street}` : report.title;
  const place = [streetInTitle ? "" : report.street, report.suburb, report.ward ? `Ward ${report.ward}` : ""]
    .filter(Boolean)
    .join(" · ");

  panel.innerHTML = `
    <div class="details-head"><h3>Issue details</h3></div>

    <div class="details-ref">${escapeHtml(report.ref)} ${priorityBadge(report.priority)}</div>
    <h2 class="details-title">${escapeHtml(heading)}</h2>
    <div class="details-location">${ICONS.pin}${escapeHtml(place)}</div>
    <div class="chips">${statusChip}${overdueChip}</div>

    ${photo}

    <div class="section-label">Original report · ${escapeHtml(report.channel === "whatsapp" ? "WhatsApp" : report.channel)}</div>
    <p class="quote">“${escapeHtml(report.description)}”</p>

    <dl class="facts">
      <div><dt>Responsible team</dt><dd>${escapeHtml(report.team)}</dd></div>
      <div><dt>Last service update</dt><dd>${escapeHtml(formatDateTime(report.updatedAt))}</dd></div>
    </dl>

    ${translation}
    ${group}
    ${escalation}

    <div class="section-label">What has happened</div>
    <ul class="timeline">${timeline}</ul>

    <div class="details-actions">
      <button type="button" class="btn btn-primary" id="post-update-btn">${ICONS.plus}Post update</button>
      <button type="button" class="btn btn-outline" id="assign-team-btn">Assign team</button>
    </div>`;

  document.getElementById("post-update-btn").addEventListener("click", () => openUpdateDialog("update"));
  document.getElementById("assign-team-btn").addEventListener("click", () => openUpdateDialog("assign"));
}

// ---------- Selecting a report ----------

function selectReport(ref, panMap = true) {
  state.selectedRef = ref;
  renderQueue();
  renderMap();
  renderDetails();

  const report = findReport(ref);
  if (panMap && map && report && hasLocation(report)) {
    map.panTo([report.lat, report.lng]);
  }
}

// ---------- Post update dialog ----------

function defaultMessage(ref, status, team) {
  switch (status) {
    case "received":
      return `We have received your report ${ref}. We will update you here.`;
    case "awaiting_dispatch":
      return `${ref} update: ${team} is responsible for this issue. A repair team has not been dispatched yet.`;
    case "team_assigned":
      return `${ref} update: The ${team} team has been assigned to your report.`;
    case "in_progress":
      return `${ref} update: Work has started on your report.`;
    case "resolved":
      return `${ref} update: Your report has been marked as resolved. Thank you for reporting.`;
    default:
      return `${ref} update.`;
  }
}

function openUpdateDialog(mode) {
  const report = findReport(state.selectedRef);
  if (!report) return;

  const dialog = document.getElementById("update-dialog");
  const statusSelect = document.getElementById("update-status");
  const teamSelect = document.getElementById("update-team");

  document.getElementById("dialog-title").textContent =
    mode === "assign" ? `Assign team · ${report.ref}` : `Post update · ${report.ref}`;

  // When assigning a team, move the status forward if it hasn't got there yet
  const earlyStatuses = ["received", "awaiting_dispatch"];
  statusSelect.value = mode === "assign" && earlyStatuses.includes(report.status) ? "team_assigned" : report.status;
  if ([...teamSelect.options].some((o) => o.value === report.team)) teamSelect.value = report.team;

  messageEditedByUser = false;
  refreshDialogMessage();

  const residents = groupSize(report);
  document.getElementById("dialog-note").textContent =
    `This message goes to ${residents} resident${residents === 1 ? "" : "s"} on WhatsApp` +
    (USE_SAMPLE_DATA ? " (simulated in this prototype; nothing is sent)." : ".");

  dialog.showModal();
  (mode === "assign" ? teamSelect : statusSelect).focus();
}

function refreshDialogMessage() {
  if (messageEditedByUser) return;
  const report = findReport(state.selectedRef);
  if (!report) return;
  const status = document.getElementById("update-status").value;
  const team = document.getElementById("update-team").value;
  document.getElementById("update-message").value = defaultMessage(report.ref, status, team);
}

async function submitUpdate(event) {
  event.preventDefault();

  const report = findReport(state.selectedRef);
  if (!report) return;

  const status = document.getElementById("update-status").value;
  const team = document.getElementById("update-team").value;
  const message = document.getElementById("update-message").value.trim();

  if (!message) {
    showToast("Please write a message for the residents.", true);
    return;
  }

  const sendButton = document.getElementById("dialog-send");
  sendButton.disabled = true;

  try {
    if (USE_SAMPLE_DATA) {
      applyUpdateLocally(report, status, team, message);
      showToast(`${report.ref} updated. WhatsApp message simulated for ${groupSize(report)} resident(s); nothing was sent.`);
    } else {
      const response = await fetch(`${API_BASE}/reports/${encodeURIComponent(report.ref)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status, team, message }),
      });
      if (!response.ok) throw new Error(`Server replied ${response.status}`);
      const result = await response.json();
      await loadReports();
      if (result.queued > 0) {
        showToast(`${report.ref} updated. ${result.sent} message(s) sent, ${result.queued} waiting to be sent (WhatsApp not connected yet).`, true);
      } else {
        showToast(`${report.ref} updated and ${result.sent} resident(s) notified.`);
      }
    }
    document.getElementById("update-dialog").close();
    renderAll();
  } catch (error) {
    console.error(error);
    showToast("The update was not saved. Please try again.", true);
  } finally {
    sendButton.disabled = false;
  }
}

// Sample mode only: change the report in memory so the demo behaves like the real thing
function applyUpdateLocally(report, status, team, message) {
  const now = new Date();
  const everyone = [report, ...linkedReports(report.ref)];

  everyone.forEach((r) => {
    r.status = status;
    r.team = team;
    r.updatedAt = now;
    if (!r.firstResponseAt) r.firstResponseAt = now;
    r.resolvedAt = status === "resolved" ? now : null;
  });

  report.updates.push({ at: now, text: `${STATUSES[status]} · ${team}. Message sent: “${message}”` });
}

// ---------- Export ----------

function exportSnapshot() {
  const rows = filteredReports();
  const header = ["Reference", "Category", "Title", "Location", "Ward", "Priority", "Status", "Team", "Reports", "Created", "Last update", "Overdue"];

  const lines = rows.map((r) => [
    r.ref,
    CATEGORIES[r.category] || r.category,
    r.title,
    [r.street, r.suburb].filter(Boolean).join(", "),
    r.ward,
    PRIORITIES[r.priority]?.label || r.priority,
    STATUSES[r.status] || r.status,
    r.team,
    groupSize(r),
    r.createdAt.toISOString(),
    r.updatedAt.toISOString(),
    isOverdue(r) ? "Yes" : "No",
  ]);

  const csv = [header, ...lines]
    .map((cells) => cells.map((c) => `"${String(c).replaceAll('"', '""')}"`).join(","))
    .join("\n");

  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  link.download = `report-ec-snapshot-${new Date().toISOString().slice(0, 10)}.csv`;
  link.click();
  URL.revokeObjectURL(link.href);
}

// ---------- Small UI pieces ----------

let toastTimer = null;

function showToast(text, isError = false) {
  const toast = document.getElementById("toast");
  toast.textContent = text;
  toast.classList.toggle("is-error", isError);
  toast.classList.add("is-visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("is-visible"), 4500);
}

function updateClock() {
  const now = new Date().toLocaleString("en-ZA", {
    timeZone: "Africa/Johannesburg",
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
  document.getElementById("clock").textContent = `${now} SAST`;
}

function setFilter(name, value) {
  state.filters[name] = value;
  state.page = 1;
  renderQueue();
  renderMap();
}

function setActiveNav(name) {
  document.querySelectorAll(".nav-item[data-nav]").forEach((item) => {
    item.classList.toggle("is-active", item.dataset.nav === name);
  });
}

// ---------- Events ----------

function wireEvents() {
  document.getElementById("filter-category").addEventListener("change", (e) => setFilter("category", e.target.value));
  document.getElementById("filter-priority").addEventListener("change", (e) => setFilter("priority", e.target.value));
  document.getElementById("filter-status").addEventListener("change", (e) => setFilter("status", e.target.value));
  document.getElementById("search-input").addEventListener("input", (e) => setFilter("search", e.target.value));

  document.getElementById("clear-filters-btn").addEventListener("click", () => {
    state.filters = { category: "all", priority: "all", status: "active", search: "" };
    document.getElementById("filter-category").value = "all";
    document.getElementById("filter-priority").value = "all";
    document.getElementById("filter-status").value = "active";
    document.getElementById("search-input").value = "";
    state.page = 1;
    setActiveNav("overview");
    renderQueue();
    renderMap();
  });

  document.getElementById("reset-view-btn").addEventListener("click", () => {
    fitMapTo(filteredReports().filter(hasLocation));
  });

  // Clicking or pressing Enter on a queue row opens that report
  const queueBody = document.getElementById("queue-body");
  queueBody.addEventListener("click", (e) => {
    const row = e.target.closest("tr[data-ref]");
    if (row) selectReport(row.dataset.ref);
  });
  queueBody.addEventListener("keydown", (e) => {
    const row = e.target.closest("tr[data-ref]");
    if (row && e.key === "Enter") selectReport(row.dataset.ref);
  });

  document.getElementById("prev-page").addEventListener("click", () => {
    state.page -= 1;
    renderQueue();
  });
  document.getElementById("next-page").addEventListener("click", () => {
    state.page += 1;
    renderQueue();
  });

  // Sidebar shortcuts
  document.querySelectorAll(".nav-item[data-nav]").forEach((item) => {
    item.addEventListener("click", (e) => {
      e.preventDefault();
      const nav = item.dataset.nav;
      const statusFilter = { overview: "active", all: "all", overdue: "overdue" }[nav];
      document.getElementById("filter-status").value = statusFilter;
      setFilter("status", statusFilter);
      setActiveNav(nav);
      if (nav !== "overview") document.getElementById("queue").scrollIntoView({ behavior: "smooth" });
    });
  });

  document.getElementById("export-btn").addEventListener("click", exportSnapshot);

  // Dialog
  const dialog = document.getElementById("update-dialog");
  document.getElementById("update-status").addEventListener("change", refreshDialogMessage);
  document.getElementById("update-team").addEventListener("change", refreshDialogMessage);
  document.getElementById("update-message").addEventListener("input", () => {
    messageEditedByUser = true;
  });
  document.getElementById("update-form").addEventListener("submit", submitUpdate);
  document.getElementById("dialog-cancel").addEventListener("click", () => dialog.close());
  document.getElementById("dialog-close").addEventListener("click", () => dialog.close());
}

// ---------- Start ----------

function start() {
  document.getElementById("data-mode").textContent = USE_SAMPLE_DATA ? "Sample reports" : "Live test reports";

  updateClock();
  setInterval(updateClock, 30000);

  wireEvents();
  initMap();
  loadReports();

  if (USE_SAMPLE_DATA) {
    // Refresh the "x min ago" labels once a minute
    setInterval(renderAll, 60000);
  } else {
    setInterval(loadReports, REFRESH_SECONDS * 1000);
  }
}

start();