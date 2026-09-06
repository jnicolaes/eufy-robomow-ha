/* Eufy Mower Card — uses Home Assistant entities and authenticated image proxy. */
export const CARD_VERSION = "0.7.1";
const unavailable = new Set(["unknown", "unavailable", ""]);
export const escapeHTML = (value) => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
export function ageSeconds(value, now = Date.now()) {
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? Math.max(0, (now - parsed) / 1000) : Infinity;
}
export function controls(state, now = Date.now()) {
  const a = state?.attributes ?? {};
  const fresh = state && !unavailable.has(state.state) && ageSeconds(a.telemetry_updated_at, now) < 45;
  const writable = a.operating_mode === "control" && fresh;
  const busy = ["sending", "pending"].includes(a.command?.state);
  const features = a.supported_features ?? 0;
  return {
    start: Boolean(writable && !busy && (features & 1) && ["docked", "paused"].includes(state.state)),
    pause: Boolean(writable && (features & 2) && ["mowing", "returning"].includes(state.state)),
    dock: Boolean(writable && (features & 4) && ["mowing", "paused", "returning"].includes(state.state)),
    writable, fresh,
  };
}
export function mapHealth(image, active, now = Date.now()) {
  if (!image || unavailable.has(image.state)) return {label: "Kaart niet beschikbaar", kind: "warn"};
  const a = image.attributes;
  const stale = ageSeconds(a.acquisition_last_success, now) > (active ? 30 : 660);
  if (stale || a.acquisition_status !== "healthy") return {label: "Bewaarde kaart · geen actuele verbinding", kind: "warn"};
  return {label: active ? "Live kaart" : "Laatste kaart", kind: "good"};
}
const labels = {docked:"Inactief", mowing:"Aan het maaien", paused:"Gepauzeerd", returning:"Terug naar het laadstation", unavailable:"Geen verbinding", unknown:"Status onbekend", idle:"Geen actieve sessie", charging:"Laadfase tijdens sessie"};
const commandLabels = {sending:"Opdracht wordt verzonden…", pending:"Verzonden · wachten op de maaier…", timeout:"Geen bevestiging ontvangen. Controleer de maaier voordat je opnieuw probeert.", rejected:"De maaier heeft de opdracht geweigerd.", failed:"Verzenden mislukt. Controleer de verbinding.", interrupted:"Bevestiging onderbroken; controleer de maaier.", superseded:"Vervangen door een volgende opdracht."};
const evidenceLabels = {mowing_reported:"Maaier meldt maaien", pause_reported:"Pauze bevestigd door de maaier", returning_reported:"Maaier meldt terugkeer", task_inactive:"Maaier meldt een inactieve taak; aankomst bij het laadstation is niet bevestigd."};
const icon = (name) => `<ha-icon icon="mdi:${name}"></ha-icon>`;
const stamp = (value) => value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString("nl-NL", {day:"numeric", month:"short", hour:"2-digit", minute:"2-digit"}) : "—";
const minutes = value => (Number(value) || 0) < 60 ? `${Math.round(Number(value) || 0)} sec` : `${Math.round(Number(value) / 60)} min`;
const numeric = value => typeof value === "number" && Number.isFinite(value) ? value.toLocaleString("nl-NL", {maximumFractionDigits:1}) : "—";
const validEntity = value => typeof value === "string" && /^[a-z_]+\.[a-z0-9_]+$/.test(value);

export function cardView(value = "all") {
  if (!["all", "overview", "history", "planning", "settings"].includes(value)) throw new Error("Onbekende paneelweergave.");
  return value;
}

export class EufyMowerCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({mode:"open"});
    this._zoom = 1;
    this._localBusy = false;
  }
  setConfig(config) {
    if (!validEntity(config.entity) || !config.entity.startsWith("lawn_mower.")) throw new Error("Kies een lawn_mower-entiteit.");
    for (const key of ["map", "session", "battery", "progress", "distance", "area", "planner_reason"]) {
      if (config[key] && !validEntity(config[key])) throw new Error(`Ongeldige entiteit: ${key}`);
    }
    if (config.settings && (!Array.isArray(config.settings) || !config.settings.every(validEntity))) throw new Error("Instellingen moeten entiteitnamen zijn.");
    if (config.planner_entities && (!Array.isArray(config.planner_entities) || !config.planner_entities.every(validEntity))) throw new Error("Planning moet entiteitnamen bevatten.");
    this.config = {...config, view:cardView(config.view)};
    this.dataset.view = this.config.view;
    this._renderShell();
    this._update();
  }
  set hass(value) { this._hass = value; this._update(); }
  connectedCallback() {
    clearInterval(this._clock);
    this._clock = setInterval(() => this._update(), 5000);
    this._update();
  }
  disconnectedCallback() { clearInterval(this._clock); }
  getCardSize() { return 12; }
  getGridOptions() { return {columns:"full", min_columns:6}; }
  static getStubConfig(hass) { return {entity:Object.keys(hass.states).find(id => id.startsWith("lawn_mower.")) ?? "lawn_mower.maaier"}; }
  _set(id, text) { const el = this.shadowRoot.getElementById(id); if (el && el.textContent !== String(text)) el.textContent = text; }
  _state(key) { return this._hass?.states[this.config[key]]; }
  _metric(key) {
    const state = this._state(key);
    if (!state || unavailable.has(state.state)) return "—";
    const value = Number(state.state);
    const unit = state.attributes.unit_of_measurement ?? "";
    return `${Number.isFinite(value) ? numeric(value) : state.state} ${unit === "units" ? "ruwe eenh." : unit}`.trim();
  }
  _renderShell() {
    this._historySignature = null;
    this.shadowRoot.innerHTML = `<style>${styles}</style>
    <ha-card><main>
      <header><div class="brand">${icon("robot-mower")}<div><p class="eyebrow">TUIN / EUFY E15</p><h1>${escapeHTML(this.config.title || "Grasmaaier")}</h1></div></div><span id="connection" class="badge"></span></header>
      <div class="layout" data-section="overview"><section class="map-section" aria-label="Maaikaart">
        <div class="map-head"><span id="map-health" class="map-label"></span><span id="map-time"></span></div>
        <div id="map-viewport"><div id="map-canvas"><img id="map-image" alt="Maaikaart met maaierpositie, laadstation en gemaaide banen" draggable="false"></div><div id="map-empty">${icon("map-outline")}<h2>Je tuin in beeld</h2><p>De kaart verschijnt zodra een gevalideerde momentopname beschikbaar is.</p></div></div>
        <div class="map-footer"><div><h2>${escapeHTML(this.config.title || "Grasmaaier")}</h2><span>Kaart en maaierpositie</span></div><div class="zoom"><button data-zoom="out" aria-label="Uitzoomen">−</button><button data-zoom="reset" id="zoom-label" aria-label="Zoom herstellen">100%</button><button data-zoom="in" aria-label="Inzoomen">+</button></div></div>
      </section><aside>
        <div class="aside-top"><span>Status</span><span id="connection-status" class="badge"></span></div><h2 id="activity">Status ophalen…</h2><p id="telemetry" class="muted"></p><div class="actions"><button id="start" class="primary" data-command="start_mowing">${icon("play")}<span id="start-label">Maaien</span></button><button id="pause" data-command="pause">${icon("pause")}Pauze</button><button id="dock" data-command="dock">${icon("home-import-outline")}Naar laadstation</button></div>
        <div class="metrics"><div><span>Batterij</span><strong id="battery">—</strong></div><div><span id="progress-label">Voortgang</span><strong id="progress">—</strong></div></div>
        <div class="progress-track" role="progressbar" aria-label="Maaivoortgang" aria-valuemin="0" aria-valuemax="100"><span id="progress-bar"></span></div>
        <div class="session-metrics"><div><span>Afstand</span><b id="distance">—</b></div><div><span>Oppervlakte*</span><b id="area">—</b></div><div><span>Waargenomen maaitijd</span><b id="duration">—</b></div></div>
        <p class="footnote">* De schaal naar m² is nog niet gevalideerd.</p>

        <p id="mode" class="muted"></p><p id="command" role="status" aria-live="polite"></p><p id="error" role="alert"></p>
      </aside></div>
      <div class="lower"><section class="panel" data-section="planning"><div class="section-heading"><h2>${icon("calendar-clock")}Planning</h2><span class="tag">HOME ASSISTANT</span></div><p id="planner-reason"></p><details id="planner-details"><summary>Planning instellen</summary><div id="planner-settings"></div></details><p class="footnote">Automatische starts volgen alleen de ingeschakelde HA-planning. Een schema in de Eufy-app blijft een afzonderlijke bron.</p></section>
      <section class="panel" data-section="settings"><div class="section-heading"><h2>${icon("tune-variant")}Instellingen</h2></div><p class="muted">Maaihoogte, rijsnelheid en maaipatroon.</p><p id="settings-status" class="muted" role="status"></p><details id="settings-details"><summary>Instellingen openen</summary><div id="settings"></div></details></section></div>
      <section class="history panel" data-section="history"><div class="section-heading"><h2>${icon("history")}Maaigeschiedenis</h2><span class="tag">LAATSTE 20 SESSIES</span></div><div id="history"></div><p class="footnote">Tijden beginnen bij de eerste waarneming. Onderbrekingen in de verbinding worden gemarkeerd; een beëindigde taak bewijst niet dat het hele gazon klaar is.</p></section>
      <footer>EUFY MOWER <span>Home Assistant · ${CARD_VERSION}</span></footer>
    </main></ha-card>`;
    this.shadowRoot.querySelectorAll("[data-section]").forEach(section => {
      section.hidden = this.config.view !== "all" && section.dataset.section !== this.config.view;
    });
    this.shadowRoot.querySelector(".lower").hidden = !["all", "planning", "settings"].includes(this.config.view);
    this.shadowRoot.getElementById("planner-details").open = this.config.view === "planning";
    this.shadowRoot.getElementById("settings-details").open = this.config.view === "settings";
    this.shadowRoot.querySelectorAll("[data-command]").forEach(button => button.addEventListener("click", () => this._command(button.dataset.command)));
    this.shadowRoot.querySelectorAll("[data-zoom]").forEach(button => button.addEventListener("click", () => {
      this._zoom = button.dataset.zoom === "reset" ? 1 : Math.max(1, Math.min(4, this._zoom + (button.dataset.zoom === "in" ? .5 : -.5)));
      this._applyZoom();
    }));
    const viewport = this.shadowRoot.getElementById("map-viewport");
    viewport.addEventListener("pointerdown", e => {
      if (this._zoom <= 1) return;
      viewport.setPointerCapture(e.pointerId);
      this._drag = {x:e.clientX, y:e.clientY, left:viewport.scrollLeft, top:viewport.scrollTop};
    });
    viewport.addEventListener("pointermove", e => { if (this._drag) { viewport.scrollLeft = this._drag.left + this._drag.x - e.clientX; viewport.scrollTop = this._drag.top + this._drag.y - e.clientY; } });
    for (const type of ["pointerup", "pointercancel", "lostpointercapture"]) viewport.addEventListener(type, () => { this._drag = null; });
    this.shadowRoot.getElementById("map-image").addEventListener("error", () => { this._set("map-health", "Kaart kon niet geladen worden"); });
    if (["all", "settings"].includes(this.config.view)) this._makeEntities("settings", this.config.settings);
    if (["all", "planning"].includes(this.config.view)) this._makeEntities("planner-settings", this.config.planner_entities);
  }
  _applyZoom() {
    const canvas = this.shadowRoot.getElementById("map-canvas");
    canvas.style.width = `${this._zoom * 100}%`;
    canvas.style.height = `${this._zoom * 100}%`;
    this.shadowRoot.getElementById("map-viewport").style.touchAction = this._zoom > 1 ? "none" : "pan-y";
    this._set("zoom-label", `${this._zoom * 100}%`);
  }
  async _makeEntities(target, entities) {
    const container = this.shadowRoot.getElementById(target);
    if (!entities?.length) { container.textContent = "Geen entiteiten geconfigureerd."; return; }
    try {
      const helpers = await window.loadCardHelpers();
      if (this.shadowRoot.getElementById(target) !== container) return;
      const settingNames = {cut_height:"Maaihoogte", travel_speed:"Rijsnelheid", blade_speed:"Messnelheid", path_distance:"Baanafstand", pad_direction:"Maairichting"};
      const rows = target === "settings" ? entities.map(entity => {
        const suffix = Object.keys(settingNames).find(key => entity.endsWith(`_${key}`));
        return suffix ? {entity, name:settingNames[suffix]} : entity;
      }) : entities;
      const card = helpers.createCardElement({type:"entities", entities:rows, show_header_toggle:false});
      card.hass = this._hass;
      container.replaceChildren(card);
    } catch { container.textContent = "Instellingen konden niet worden geladen."; }
  }
  _update() {
    if (!this._hass || !this.config || !this.shadowRoot.getElementById("activity")) return;
    const mower = this._state("entity");
    const state = mower?.state ?? "unavailable";
    const c = controls(mower);
    const session = this._state("session")?.attributes;
    const current = session?.current_session;
    const active = ["mowing", "paused", "returning"].includes(state);
    this._set("activity", current?.phase === "charging" ? labels.charging : (labels[state] ?? state));
    this._set("connection", c.fresh ? "● Verbonden" : "○ Geen actuele status");
    this._set("telemetry", `Status bijgewerkt ${stamp(mower?.attributes.telemetry_updated_at)}`);
    this._set("battery", this._metric("battery"));
    this._set("progress-label", current ? "Huidige sessie" : "Laatste sessiemeting");
    const progress = current ? current.progress : Number(this._state("progress")?.state);
    this._set("progress", current ? (current.progress == null ? "—" : `${numeric(current.progress)} %`) : this._metric("progress"));
    this._set("distance", current ? (current.distance_m == null ? "—" : `${numeric(current.distance_m)} m`) : this._metric("distance"));
    this._set("area", current ? (current.area_raw == null ? "—" : `${numeric(current.area_raw)} ruwe eenh.`) : this._metric("area"));
    this._set("duration", current ? minutes(current.mowing_seconds) : "—");
    this.shadowRoot.getElementById("progress-bar").style.width = `${Number.isFinite(progress) ? Math.max(0, Math.min(100, progress)) : 0}%`;
    const track = this.shadowRoot.querySelector(".progress-track");
    if (Number.isFinite(progress)) track.setAttribute("aria-valuenow", String(progress)); else track.removeAttribute("aria-valuenow");
    for (const name of ["start", "pause", "dock"]) this.shadowRoot.getElementById(name).disabled = !c[name] || (name === "start" && this._localBusy);
    this._set("start-label", state === "paused" ? "Hervatten" : "Maaien");
    this._set("mode", mower?.attributes.operating_mode === "observe_only" ? "Alleen observeren · bediening uitgeschakeld" : (!c.fresh ? "Bediening wacht op actuele maaierstatus." : ""));
    this._set("connection-status", c.fresh ? "Verbonden" : "Geen actuele status");
    this._set("settings-status", c.writable ? "" : "Instellingen zijn beschikbaar zodra bediening is ingeschakeld en de maaierstatus actueel is.");
    const command = mower?.attributes.command;
    this._set("command", command ? (command.state === "confirmed" ? evidenceLabels[command.evidence] ?? "Nieuwe status ontvangen" : commandLabels[command.state] ?? command.state) : "");
    const map = this._state("map");
    const health = mapHealth(map, active);
    this._set("map-health", health.label);
    this.shadowRoot.getElementById("map-health").className = `map-label ${health.kind}`;
    this._set("map-time", stamp(map?.attributes.acquisition_last_success));
    const image = this.shadowRoot.getElementById("map-image");
    const picture = map?.attributes.entity_picture;
    // Use only HA's same-origin image proxy; no arbitrary or credential-bearing external URL.
    const safePicture = ["all", "overview"].includes(this.config.view) && typeof picture === "string" && picture.startsWith("/api/image_proxy/");
    if (safePicture && image.getAttribute("src") !== picture) image.src = picture;
    image.hidden = !safePicture;
    this.shadowRoot.getElementById("map-empty").hidden = Boolean(safePicture);
    this._set("planner-reason", this._state("planner_reason")?.state ?? "Koppel een planningssensor om regen, beregening en het volgende maaimoment hier te zien.");
    this.shadowRoot.getElementById("settings-details").hidden = !c.writable;
    this.shadowRoot.querySelectorAll("hui-entities-card").forEach(card => { card.hass = this._hass; });
    this._history(session?.recent_sessions ?? []);
  }
  _history(rows) {
    const signature = JSON.stringify(rows);
    if (signature === this._historySignature) return;
    this._historySignature = signature;
    const target = this.shadowRoot.getElementById("history");
    if (!rows.length) { target.innerHTML = '<div class="empty-history">De volgende maaibeurt verschijnt hier automatisch. Eerdere sessies worden niet gereconstrueerd uit losse tellerstanden.</div>'; return; }
    target.innerHTML = `<div class="table-scroll"><table><thead><tr><th>Sessie</th><th>Maaitijd</th><th>Afstand</th><th>Oppervlakte*</th><th>Onderbrekingen</th></tr></thead><tbody>${rows.slice(0,20).map(row => `<tr><td><b>${escapeHTML(stamp(row.started_at))}</b><small>${escapeHTML(!row.start_observed || !row.end_observed || row.observation_gap ? "Onvolledige waarneming" : "Taak beëindigd")}</small></td><td>${escapeHTML(minutes(row.mowing_seconds))}</td><td>${escapeHTML(numeric(row.distance_m))} m</td><td>${escapeHTML(numeric(row.area_raw))} ruwe eenh.</td><td>${escapeHTML(row.pause_count)} · ${escapeHTML(minutes(row.paused_seconds))}</td></tr>`).join("")}</tbody></table></div>`;
  }
  async _command(service) {
    const control = {start_mowing:"start", pause:"pause", dock:"dock"}[service];
    if (!controls(this._state("entity"))[control]) return;
    if (service === "start_mowing" && !window.confirm("Maaien starten of hervatten? Controleer dat het gazon vrij is en je de maaier kunt stoppen.")) return;
    this._set("error", "");
    this._localBusy = true;
    this._update();
    try { await this._hass.callService("lawn_mower", service, {entity_id:this.config.entity}); }
    catch (error) { this._set("error", error.message ?? "De opdracht kon niet worden bevestigd."); }
    finally { this._localBusy = false; this._update(); }
  }
}

const styles = `
:host{display:block;min-width:0;--ink:var(--primary-text-color,#212121);--muted:var(--secondary-text-color,#727272);--paper:var(--ha-card-background,var(--card-background-color,#fff));--line:var(--ha-card-border-color,var(--divider-color,#ddd));--accent:var(--primary-color,#03a9f4);font-family:var(--primary-font-family,system-ui,sans-serif);color:var(--ink)}
*{box-sizing:border-box}[hidden]{display:none!important}ha-card{display:block;background:transparent;border:0;box-shadow:none;color:var(--ink)}main{padding:8px;max-width:1600px;margin:auto}
header,.brand,.section-heading,.map-head,.map-footer,.aside-top{display:flex;align-items:center;justify-content:space-between;gap:12px}header{padding:16px 8px}.brand{justify-content:flex-start}.brand>ha-icon{--mdc-icon-size:28px}.eyebrow,footer{display:none}:host(:not([data-view="all"])) header{display:none}
h1{font-size:24px;margin:0;font-weight:500}h2{font-size:20px;margin:0;font-weight:500}p{line-height:1.5}.badge,.tag{font-size:12px;color:var(--muted);white-space:nowrap}.badge:before{content:'●';font-size:10px;color:var(--accent);margin-right:7px}
.layout{display:grid;grid-template-columns:minmax(0,1.65fr) minmax(300px,1fr);gap:8px;align-items:start}.map-section,aside,.panel{border:1px solid var(--line);border-radius:16px;overflow:hidden;background:var(--paper);min-width:0}
.map-section{display:flex;flex-direction:column}.map-head{font-size:12px;padding:12px 16px;color:var(--muted)}.map-label:before{content:'●';margin-right:7px;color:var(--accent)}.map-label.warn:before{color:var(--warning-color,#ffa600)}.map-head>span:last-child{font-size:12px}
#map-viewport{height:clamp(360px,65vh,680px);position:relative;overflow:auto;background:#10161e;cursor:grab}#map-viewport:active{cursor:grabbing}#map-canvas{width:100%;height:100%;min-height:100%}#map-image{width:100%;height:100%;object-fit:contain;display:block}#map-empty{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;padding:24px;color:#c9d1d9}#map-empty ha-icon{--mdc-icon-size:48px;margin-bottom:16px}#map-empty p{font-size:14px;max-width:320px}
.map-footer{padding:16px;min-height:88px}.map-footer h2{font-size:16px;font-weight:600;margin-bottom:6px}.map-footer>div>span{font-size:12px;color:var(--muted)}button{font:inherit;font-size:14px;cursor:pointer;border:0;border-radius:8px;background:var(--secondary-background-color,#eee);color:var(--ink);padding:11px 14px;display:inline-flex;gap:8px;align-items:center;justify-content:center;min-height:44px}button:hover:not(:disabled){filter:brightness(.92)}button:disabled{opacity:.42;cursor:not-allowed}button:focus-visible,summary:focus-visible{outline:3px solid var(--accent);outline-offset:3px}.zoom{display:flex;gap:4px}.zoom button{padding:8px 11px}.zoom button:nth-child(2){font-size:12px;min-width:54px}
aside{padding:20px}.aside-top{font-size:14px;color:var(--muted)}aside>h2{font-size:28px;line-height:1.2;margin:20px 0 8px}.muted{font-size:13px;color:var(--muted)}.muted:empty,#command:empty,#error:empty{display:none}.metrics{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:26px}.metrics span,.session-metrics span{display:block;color:var(--muted);font-size:13px;margin-bottom:8px}.metrics strong{font-size:28px;font-weight:500}.progress-track{height:4px;border-radius:8px;background:var(--divider-color,#ddd);margin:20px 0 24px;overflow:hidden}.progress-track span{display:block;height:100%;background:var(--accent);transition:width .4s}.session-metrics{display:grid;gap:16px}.session-metrics>div{display:flex;align-items:center;justify-content:space-between;gap:12px}.session-metrics span{margin:0}.session-metrics b{font-size:14px;font-weight:500}.footnote{font-size:12px;color:var(--muted);line-height:1.6}.actions{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:24px}.actions button:last-child{grid-column:1/-1}.actions .primary{background:var(--accent);color:var(--text-primary-color,#fff)}.actions ha-icon{--mdc-icon-size:20px}#command{font-size:13px;color:var(--muted);margin-bottom:0}#error{font-size:13px;color:var(--error-color,#db4437)}
.lower{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:8px}.panel{padding:20px}.section-heading{margin-bottom:16px}.section-heading h2{display:flex;align-items:center;gap:10px;font-size:20px}.section-heading ha-icon{--mdc-icon-size:22px}#planner-reason{font-size:16px}details{border-top:1px solid var(--line);margin-top:16px}summary{font-size:14px;cursor:pointer;padding:16px 0;color:var(--accent)}.history{margin-top:8px}.empty-history{padding:24px 0;color:var(--muted);font-size:14px}.table-scroll{overflow:auto}table{width:100%;text-align:left;border-collapse:collapse;font-size:14px;white-space:nowrap}th{font-size:13px;color:var(--muted);font-weight:500;padding:12px 20px 12px 0}td{padding:18px 20px 18px 0;border-top:1px solid var(--line)}td small{display:block;color:var(--muted);margin-top:6px;font-size:12px}
:host([data-view="planning"]) main,:host([data-view="settings"]) main{max-width:850px}:host([data-view="planning"]) .lower,:host([data-view="settings"]) .lower{grid-template-columns:1fr;margin-top:0}:host([data-view="history"]) .history{margin-top:0}:host([data-view="planning"]) #planner-details,:host([data-view="settings"]) #settings-details{border:0;margin:0}:host([data-view="planning"]) #planner-details>summary,:host([data-view="settings"]) #settings-details>summary{display:none}hui-entities-card{--ha-card-border-width:0;--ha-card-box-shadow:none}
@media(max-width:800px){.layout{grid-template-columns:1fr}.lower{grid-template-columns:1fr}#map-viewport{height:45vh;min-height:300px;max-height:480px}aside,.panel{padding:16px}aside>h2{font-size:25px}.map-head{flex-wrap:wrap}.badge{font-size:12px}.metrics{margin-top:20px}.map-footer{padding:12px 16px}.section-heading{flex-wrap:wrap}.tag{font-size:11px}}
`;

if (!customElements.get("eufy-mower-card")) customElements.define("eufy-mower-card", EufyMowerCard);
window.customCards = window.customCards || [];
window.customCards.push({type:"eufy-mower-card", name:"Eufy Mower", description:"Kaart, bediening, planning en sessiegeschiedenis voor Eufy Robomow", preview:true});
