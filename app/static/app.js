"use strict";

/* Dossier client — interface commerciale.
   Parcours : 1. Documents et entreprise → 2. Contact et besoin → 3. Vérification et création. */

const state = { config: null, draft: null, step: 1, matches: null, contacts: null, saving: null, preview: null, busy: false };
const $app = document.getElementById("app");

// ------------------------------------------------------------------ utilitaires
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(method, url, body, isForm = false) {
  const opts = { method, headers: { "X-Requested-With": "dossier-client" }, credentials: "same-origin" };
  if (body !== undefined) {
    if (isForm) opts.body = body;
    else { opts.body = JSON.stringify(body); opts.headers["Content-Type"] = "application/json"; }
  }
  const res = await fetch(url, opts);
  let data = null;
  try { data = await res.json(); } catch (_) { /* réponse vide */ }
  if (!res.ok) {
    if (res.status === 401 && data && data.login) { renderLogin(); }
    const err = new Error((data && data.errors || ["Erreur inattendue."]).join(" "));
    err.messages = (data && data.errors) || [err.message];
    err.status = res.status;
    throw err;
  }
  return data;
}

function toast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add("hidden"), 3500);
}

// Mêmes libellés que le formulaire « Nouveau compte » de Salesforce.
const LABELS = {
  company_name: "Nom du compte", common_name: "Nom commun", company_phone: "Téléphone",
  sole_proprietorship: "Entreprise individuelle", rc_number: "Numéro de RC",
  tax_id_type: "Type de numéro d'identification fiscale", tax_id: "Numéro d'identification fiscale",
  address: "Adresse de facturation", postal_code: "Code postal de facturation", city: "Ville de facturation",
  region: "Région/Province de facturation", ice: "Numéro ICE",
};
const HINT_LABELS = { legal_form: "Forme juridique", manager_name: "Gérant / dirigeant", activity: "Activité", capital: "Capital" };

function normalisePhone(v) {
  let p = String(v || "").replace(/[\s.\-()]/g, "");
  if (/^00212\d{9}$/.test(p)) p = "+" + p.slice(2);
  else if (/^212\d{9}$/.test(p)) p = "+" + p;
  else if (/^0[5-8]\d{8}$/.test(p)) p = "+212" + p.slice(1);
  return p || v;
}
const STATUS_LABELS = {
  draft: "En préparation", error: "À corriger", uncertain: "À vérifier", submitting: "Envoi en cours",
  records_created: "Pièces jointes à terminer", completed: "Créé dans Salesforce",
};

// ------------------------------------------------------------------ démarrage
async function boot() {
  state.config = await api("GET", "/api/config");
  const badge = document.getElementById("mode-badge");
  if (state.config.mode === "mock") { badge.textContent = "Démonstration · Salesforce simulé"; badge.classList.add("demo"); }
  else badge.textContent = "Connecté à Salesforce";
  if (!state.config.user) return renderLogin();
  document.getElementById("user-name").textContent = state.config.user.name;
  if (state.config.mode === "live") {
    const lo = document.getElementById("logout");
    lo.classList.remove("hidden");
    lo.onclick = async () => { await api("POST", "/auth/logout"); location.href = "/"; };
  }
  const id = new URLSearchParams(location.search).get("dossier");
  if (id) return openDraft(id);
  renderHome();
}

function renderLogin() {
  document.getElementById("mode-badge").textContent = "Non connecté";
  const failed = new URLSearchParams(location.search).get("login_error");
  $app.innerHTML = `
    <div class="card center" style="max-width:520px;margin:40px auto">
      <h1>Connexion</h1>
      <p class="lead">Connectez-vous avec votre compte Salesforce. Les fiches seront créées à votre nom, avec vos droits habituels.</p>
      ${failed ? `<div class="alert err">La connexion n'a pas abouti. Réessayez ou contactez l'équipe CRM.</div>` : ""}
      <a class="btn primary" href="/auth/login" style="display:inline-block;text-decoration:none">Se connecter avec Salesforce</a>
    </div>`;
}

// ------------------------------------------------------------------ accueil
async function renderHome() {
  history.replaceState(null, "", "/");
  state.draft = null;
  const drafts = await api("GET", "/api/drafts");
  const open = drafts.filter((d) => d.status !== "completed");
  const done = drafts.filter((d) => d.status === "completed").slice(0, 5);
  const row = (d) => `
    <li>
      <div><strong>${esc(d.company_name)}</strong><br><small class="muted">Modifié le ${esc(new Date(d.updated_at).toLocaleString("fr-FR"))}</small></div>
      <div class="actions"><span class="pill ${d.status === "completed" ? "ok" : ""}">${esc(STATUS_LABELS[d.status] || d.status)}</span>
      <button class="btn small" data-open="${esc(d.id)}">${d.status === "completed" ? "Voir" : "Reprendre"}</button></div>
    </li>`;
  $app.innerHTML = `
    <p class="eyebrow">Après accord du responsable</p>
    <h1>Préparer un dossier client</h1>
    <p class="lead">Déposez le RC et le document fiscal reçus sur WhatsApp, vérifiez les informations proposées,
      complétez le contact et le besoin : le compte, le contact, l'opportunité et les pièces jointes sont créés en une fois dans Salesforce.</p>
    <div class="actions" style="margin-bottom:18px"><button class="btn primary" id="new">Nouveau dossier</button></div>
    <div class="card"><h2>Dossiers en cours</h2>
      ${open.length ? `<ul class="list">${open.map(row).join("")}</ul>` : `<p class="muted">Aucun dossier en cours.</p>`}
    </div>
    ${done.length ? `<div class="card"><h2>Derniers dossiers créés</h2><ul class="list">${done.map(row).join("")}</ul></div>` : ""}`;
  document.getElementById("new").onclick = async () => {
    const d = await api("POST", "/api/drafts");
    openDraft(d.id);
  };
  $app.querySelectorAll("[data-open]").forEach((b) => (b.onclick = () => openDraft(b.dataset.open)));
}

async function openDraft(id) {
  try {
    state.draft = await api("GET", `/api/drafts/${encodeURIComponent(id)}`);
  } catch (e) {
    toast(e.message);
    return renderHome();
  }
  history.replaceState(null, "", `/?dossier=${encodeURIComponent(id)}`);
  state.matches = null;
  state.contacts = null;
  state.step = ["records_created", "completed", "error", "uncertain"].includes(state.draft.status) ? 3 : 1;
  render();
}

// ------------------------------------------------------------------ enregistrement automatique
function setSaveState(text) {
  const el = document.getElementById("save-state");
  if (el) el.textContent = text;
}

function scheduleSave() {
  setSaveState("Modifications non enregistrées…");
  clearTimeout(state.saving);
  state.saving = setTimeout(saveNow, 600);
}

async function saveNow() {
  clearTimeout(state.saving);
  const d = state.draft;
  if (!d || ["submitting", "records_created", "completed"].includes(d.status)) return;
  try {
    const fresh = await api("PUT", `/api/drafts/${d.id}`, {
      company: d.company, account_choice: d.account_choice, contact: d.contact, contact_choice: d.contact_choice,
      segment: d.segment, opportunity: d.opportunity, verified: d.verified,
    });
    state.draft.updated_at = fresh.updated_at;
    setSaveState("Enregistré · vous pouvez reprendre plus tard");
  } catch (e) {
    setSaveState("Échec de l'enregistrement : " + e.message);
  }
}

function bindInputs(section) {
  $app.querySelectorAll(`[data-section="${section}"]`).forEach((input) => {
    input.addEventListener("input", () => {
      state.draft[section][input.name] = input.value;
      input.classList.remove("from-doc");
      if (section === "company") delete state.draft.company_sources[input.name];
      scheduleSave();
    });
  });
}

// ------------------------------------------------------------------ rendu général
function render() {
  const s = state.step;
  const locked = ["records_created", "completed"].includes(state.draft.status);
  const stepClass = (n) => (n === s ? "active" : n < s || locked ? "done" : "");
  $app.innerHTML = `
    <ol class="steps">
      <li class="${stepClass(1)}">1. Documents et entreprise</li>
      <li class="${stepClass(2)}">2. Contact et besoin</li>
      <li class="${stepClass(3)}">3. Vérification</li>
    </ol>
    <div id="step"></div>
    <div class="footer-bar ${locked ? "hidden" : ""}"><div class="inner">
      <div class="actions">
        <button class="btn" id="pause">Enregistrer et quitter</button>
        <span class="save-state" id="save-state">${locked ? "" : "Enregistré automatiquement"}</span>
      </div>
      <div class="actions">
        ${s > 1 && !locked ? `<button class="btn" id="prev">Retour</button>` : ""}
        ${s < 3 ? `<button class="btn primary" id="next">${s === 1 ? "Continuer vers le contact" : "Vérifier le dossier"}</button>` : ""}
      </div>
    </div></div>`;
  document.getElementById("pause").onclick = async () => { await saveNow(); renderHome(); };
  const prev = document.getElementById("prev");
  if (prev) prev.onclick = () => go(s - 1);
  const next = document.getElementById("next");
  if (next) next.onclick = () => go(s + 1);
  ({ 1: renderStep1, 2: renderStep2, 3: renderStep3 })[s]();
}

async function go(step) {
  await saveNow();
  state.step = step;
  render();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

// ------------------------------------------------------------------ étape 1
function docBlock(kind, title, hint) {
  const doc = state.draft.documents[kind];
  const fields = doc ? Object.keys(doc.fields || {}) : [];
  const readable = fields.map((k) => LABELS[k] || HINT_LABELS[k] || k).join(", ");
  return `
    <div class="drop ${doc ? "has-file" : ""}" data-drop="${kind}">
      <h3>${title}</h3>
      ${doc ? `
        <div class="file-name">${esc(doc.filename)}</div>
        <small class="muted">${doc.pages} page(s)</small>
        ${doc.warning ? `<div class="alert warn">${esc(doc.warning)}</div>`
          : `<ul><li>${fields.length ? `Informations repérées : ${esc(readable)}` : "Aucune information reconnue automatiquement : saisie manuelle."}</li></ul>`}
        ${doc.warning && fields.length ? `<ul><li>Informations repérées : ${esc(readable)}</li></ul>` : ""}
        ${(doc.unreadable || []).length ? `<div class="alert warn small">Illisible dans le PDF, à saisir : ${esc(doc.unreadable.map((k) => LABELS[k] || HINT_LABELS[k] || k).join(", "))}</div>` : ""}
        <div class="actions" style="margin-top:8px"><button class="btn small" data-remove="${kind}">Retirer</button></div>`
      : `
        <p class="muted" style="margin:4px 0 10px">${hint}</p>
        <div class="actions">
          <label class="btn small">Choisir un PDF<input type="file" accept="application/pdf,.pdf" data-upload="${kind}" hidden></label>
          ${state.config.demo_samples ? `<button class="btn small" data-sample="${kind}">Exemple fictif</button>` : ""}
        </div>
        <small class="muted">PDF texte, ${state.config.max_upload_mb} Mo max. Vous pouvez aussi glisser le fichier ici.</small>`}
    </div>`;
}

function sourceBadge(src) {
  if (!src) return "";
  const where = src.method === "déduction" ? `${src.source} · déduit` : `${src.source}${src.page ? ` · p.${src.page}` : ""}${src.method === "OCR" ? " · OCR" : ""}`;
  return `<span class="src" title="${esc(src.snippet)}">${esc(where)} · à vérifier</span>`;
}

function companyField(key, opts = {}) {
  const d = state.draft;
  const src = d.company_sources[key];
  const sent = state.config.account_fields_sent[key] !== false;
  const unreadable = Object.values(d.documents || {}).some((doc) => (doc.unreadable || []).includes(key)) && !d.company[key];
  const badge = src ? sourceBadge(src)
    : unreadable ? `<span class="src warn" title="L'information existe dans le PDF mais ses caractères sont illisibles">illisible dans le PDF · à saisir</span>`
    : !sent ? `<span class="src off" title="Ce champ n'est pas encore configuré pour être envoyé à Salesforce">non envoyé</span>` : "";
  const value = d.company[key] ?? "";
  const common = `name="${key}" data-section="company" class="${src ? "from-doc" : ""}"`;
  let control;
  if (opts.options) {
    control = `<select ${common}><option value="">- Aucun -</option>
      ${opts.options.map((o) => `<option ${o === value ? "selected" : ""}>${esc(o)}</option>`).join("")}</select>`;
  } else if (opts.textarea) {
    control = `<textarea ${common} rows="2" placeholder="${esc(opts.placeholder || "")}">${esc(value)}</textarea>`;
  } else {
    control = `<input type="${opts.type || "text"}" ${common} value="${esc(value)}" placeholder="${esc(opts.placeholder || "")}"
      autocomplete="off" ${opts.inputmode ? `inputmode="${opts.inputmode}"` : ""}>`;
  }
  return `
    <label class="field ${opts.hidden ? "hidden" : ""}" data-field="${key}">
      <span class="label-row"><span>${opts.required ? '<span class="req">*</span>' : ""}${LABELS[key]}</span>${badge}</span>
      ${control}
      ${opts.after || ""}
    </label>`;
}

function addressCheckHtml() {
  const c = state.draft.address_check;
  if (!c || !c.message) return "";
  const cls = { ok: "ok", warning: "warn", error: "err" }[c.status] || "info";
  return `<div class="alert ${cls} small">${esc(c.message)}</div>`;
}

function renderStep1() {
  const d = state.draft;
  const hints = Object.entries(d.hints || {});
  const conflicts = d.conflicts || [];
  const acc = d.account_choice;
  document.getElementById("step").innerHTML = `
    <div class="card">
      <h2>Documents du client</h2>
      <p class="muted">Téléchargez les pièces reçues sur WhatsApp, puis déposez-les ici. Elles seront jointes au compte dans Salesforce.</p>
      <div class="docs">
        ${docBlock("rc", "Registre de commerce (RC)", "Extrait du RC envoyé par le client.")}
        ${docBlock("fiscal", "Document fiscal — si disponible", "Bilan, attestation IF/ICE… Facultatif : l'IF peut aussi être saisi à la main.")}
      </div>
      ${conflicts.length ? `<div class="alert warn"><strong>Les deux documents ne concordent pas :</strong><ul>
        ${conflicts.map((c) => `<li>${esc(LABELS[c.field] || c.field)} : RC « ${esc(c.rc)} » / document fiscal « ${esc(c.fiscal)} »</li>`).join("")}
        </ul>Vérifiez qu'il s'agit bien du même client avant de continuer.</div>` : ""}
    </div>

    <div class="card">
      <h2>Nouveau compte — vérifier les informations</h2>
      <p class="muted">Mêmes champs que le formulaire Salesforce. Les valeurs lues dans les documents sont en bleu avec leur source ;
        une information absente ou illisible reste vide pour être saisie à la main.</p>
      <h3 class="section-title">Informations du compte</h3>
      <div class="cols">
        <div class="stack">
          ${companyField("company_name", { required: true })}
          ${companyField("common_name")}
          ${companyField("company_phone", { type: "tel", placeholder: "+212…" })}
          ${companyField("sole_proprietorship", { required: true, options: ["Oui", "Non"] })}
        </div>
        <div class="stack">
          ${companyField("rc_number", { required: true, inputmode: "numeric" })}
          ${companyField("tax_id_type", { options: state.config.tax_id_types })}
          ${companyField("tax_id", { inputmode: "numeric", hidden: !d.company.tax_id_type && !d.company.tax_id })}
        </div>
      </div>
      <h3 class="section-title">Adresse</h3>
      <div class="cols">
        <div class="stack">
          <label class="field"><span class="label-row"><span><span class="req">*</span>Pays de facturation</span><span class="src off">fixe</span></span>
            <input type="text" value="${esc(state.config.country)}" disabled></label>
          ${companyField("address", { required: true, textarea: true, placeholder: "Rue, numéro, quartier",
            after: `<button type="button" class="link small-link" id="addr-analyse">Déduire la ville et le code postal de l'adresse</button>` })}
          ${companyField("postal_code", { required: true, inputmode: "numeric", placeholder: "5 chiffres" })}
          <div class="field-row">
            ${companyField("city", { required: true })}
            ${companyField("region")}
          </div>
          <div id="addr-check">${addressCheckHtml()}</div>
          ${companyField("ice", { inputmode: "numeric", placeholder: "15 chiffres" })}
        </div>
        <div class="stack"><p class="muted small">${esc(state.config.postal_reference.status)}</p></div>
      </div>
      ${hints.length ? `<div class="alert info"><strong>Lu aussi sur les documents (pour information) :</strong><ul>
        ${hints.map(([k, h]) => `<li>${esc(HINT_LABELS[k] || k)} : ${esc(h.value)}</li>`).join("")}</ul></div>` : ""}
    </div>

    <div class="card">
      <h2>Ce client existe-t-il déjà dans Salesforce ?</h2>
      ${acc.mode === "existing"
        ? `<div class="choice"><span>Compte existant utilisé : ${esc(acc.name)}</span><button class="link" id="acc-new">Créer un nouveau compte à la place</button></div>`
        : `<p class="muted">Avant de créer un compte, vérifiez qu'il n'existe pas déjà (même RC, même ICE ou nom proche).</p>
           <div class="actions"><button class="btn" id="search">Rechercher dans Salesforce</button></div>
           <div id="matches"></div>`}
    </div>

    <div class="card">
      <label class="check"><input type="checkbox" id="verified" ${d.verified ? "checked" : ""}>
        <span>J'ai vérifié que les pièces concernent le bon client et contrôlé les informations proposées.</span></label>
    </div>`;

  bindInputs("company");
  const typeSel = $app.querySelector('select[name="tax_id_type"]');
  if (typeSel) typeSel.addEventListener("input", () => {
    $app.querySelector('[data-field="tax_id"]').classList.toggle("hidden", !typeSel.value && !d.company.tax_id);
  });
  const phone = $app.querySelector('input[name="company_phone"]');
  if (phone) phone.addEventListener("blur", () => {
    const v = normalisePhone(phone.value);
    if (v !== phone.value) { phone.value = v; d.company.company_phone = v; scheduleSave(); }
  });
  ["city", "postal_code"].forEach((k) => {
    const el = $app.querySelector(`[name="${k}"]`);
    if (el) el.addEventListener("blur", checkAddress);
  });
  document.getElementById("addr-analyse").onclick = async () => {
    await saveNow();
    try {
      const res = await api("POST", `/api/drafts/${d.id}/address/analyse`);
      state.draft = res;
      toast(res.address_notes && res.address_notes.length ? res.address_notes.join(" ") : "Adresse analysée : vérifiez la ville et le code postal.");
    } catch (e) { toast(e.message); }
    render();
  };
  document.getElementById("verified").onchange = (e) => { d.verified = e.target.checked; scheduleSave(); };
  $app.querySelectorAll("[data-upload]").forEach((inp) => (inp.onchange = () => inp.files[0] && upload(inp.dataset.upload, inp.files[0])));
  $app.querySelectorAll("[data-sample]").forEach((b) => (b.onclick = () => sample(b.dataset.sample)));
  $app.querySelectorAll("[data-remove]").forEach((b) => (b.onclick = () => removeDoc(b.dataset.remove)));
  $app.querySelectorAll("[data-drop]").forEach((zone) => {
    zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("dragover"); });
    zone.addEventListener("dragleave", () => zone.classList.remove("dragover"));
    zone.addEventListener("drop", (e) => {
      e.preventDefault();
      zone.classList.remove("dragover");
      const f = e.dataTransfer.files[0];
      if (f) upload(zone.dataset.drop, f);
    });
  });
  const search = document.getElementById("search");
  if (search) search.onclick = searchAccounts;
  const accNew = document.getElementById("acc-new");
  if (accNew) accNew.onclick = () => { d.account_choice = { mode: "new" }; d.contact_choice = { mode: "new" }; scheduleSave(); render(); };
  if (state.matches) renderMatches();
}

async function checkAddress() {
  const c = state.draft.company;
  try {
    state.draft.address_check = await api("GET", `/api/address/check?${new URLSearchParams({ city: c.city || "", postal_code: c.postal_code || "" })}`);
  } catch (_) { return; }
  const box = document.getElementById("addr-check");
  if (box) box.innerHTML = addressCheckHtml();
}

async function upload(kind, file) {
  await saveNow();
  const form = new FormData();
  form.append("file", file);
  try {
    state.draft = await api("POST", `/api/drafts/${state.draft.id}/documents/${kind}`, form, true);
    toast("Document lu : vérifiez les informations proposées.");
  } catch (e) { toast(e.message); }
  render();
}

async function sample(kind) {
  await saveNow();
  try { state.draft = await api("POST", `/api/drafts/${state.draft.id}/documents/${kind}/sample`); }
  catch (e) { toast(e.message); }
  render();
}

async function removeDoc(kind) {
  await saveNow();
  try { state.draft = await api("DELETE", `/api/drafts/${state.draft.id}/documents/${kind}`); }
  catch (e) { toast(e.message); }
  render();
}

async function searchAccounts() {
  const c = state.draft.company;
  const q = new URLSearchParams({ company_name: c.company_name || "", rc_number: c.rc_number || "", ice: c.ice || "" });
  const box = document.getElementById("matches");
  box.innerHTML = `<p class="muted">Recherche…</p>`;
  try { state.matches = await api("GET", `/api/salesforce/accounts?${q}`); }
  catch (e) { box.innerHTML = `<div class="alert err">${esc(e.message)}</div>`; return; }
  renderMatches();
}

function renderMatches() {
  const box = document.getElementById("matches");
  if (!box) return;
  const m = state.matches;
  box.innerHTML = !m.length
    ? `<div class="alert ok">Aucun compte correspondant trouvé : un nouveau compte sera créé.</div>`
    : `<ul class="list">${m.map((a, i) => `
        <li><div><strong>${esc(a.name)}</strong><br><small class="muted">RC ${esc(a.rc_number || "—")} · ICE ${esc(a.ice || "—")} · ${esc(a.city || "")}</small></div>
          <div class="actions"><span class="pill ${a.strong ? "strong" : ""}">${esc(a.match)}</span>
          <button class="btn small" data-use="${i}">Utiliser ce compte</button></div></li>`).join("")}</ul>
       <p class="muted"><small>Si aucun de ces comptes ne correspond, continuez : un nouveau compte sera créé.</small></p>`;
  box.querySelectorAll("[data-use]").forEach((b) => (b.onclick = () => {
    const a = m[Number(b.dataset.use)];
    state.draft.account_choice = { mode: "existing", id: a.id, name: a.name };
    state.draft.contact_choice = { mode: "new" };
    state.contacts = null;
    scheduleSave();
    render();
  }));
}

// ------------------------------------------------------------------ étape 2
function input(section, key, label, type = "text", opts = {}) {
  const v = state.draft[section][key] ?? "";
  return `<label class="field ${opts.full ? "full" : ""}"><span>${label}${opts.required ? " *" : ""}</span>
    <input type="${type}" name="${key}" data-section="${section}" value="${esc(v)}" placeholder="${esc(opts.placeholder || "")}" autocomplete="off"></label>`;
}

function segmentField(f) {
  const v = state.draft.segment[f.key] ?? "";
  const label = `${esc(f.label)}${f.required ? " *" : ""}`;
  if (f.type === "select") {
    return `<label class="field"><span>${label}</span><select name="${esc(f.key)}" data-section="segment">
      <option value="">— Choisir —</option>
      ${(f.options || []).map((o) => `<option ${o === v ? "selected" : ""}>${esc(o)}</option>`).join("")}</select></label>`;
  }
  return `<label class="field"><span>${label}</span><input type="${f.type === "number" ? "number" : "text"}" min="0"
    name="${esc(f.key)}" data-section="segment" value="${esc(v)}"></label>`;
}

function renderStep2() {
  const d = state.draft;
  const existing = d.account_choice.mode === "existing";
  const cfg = state.config;
  const manager = d.hints && d.hints.manager_name;
  document.getElementById("step").innerHTML = `
    <div class="card">
      <h2>Personne à contacter</h2>
      ${existing ? `<div id="contacts"><p class="muted">Chargement des contacts du compte…</p></div>` : ""}
      <div id="contact-form" class="${d.contact_choice.mode === "existing" ? "hidden" : ""}">
        <p class="muted">La personne qui suit l'achat chez le client (pas forcément le gérant cité dans le RC).</p>
        ${manager && !d.contact.last_name ? `<div class="alert info">Gérant cité sur le RC : <strong>${esc(manager.value)}</strong>.
          <button class="link" id="use-manager">C'est aussi notre interlocuteur</button></div>` : ""}
        <div class="grid">
          ${input("contact", "first_name", "Prénom")}
          ${input("contact", "last_name", "Nom", "text", { required: true })}
          ${input("contact", "title", "Fonction", "text", { placeholder: "Gérant, responsable achats…" })}
          ${input("contact", "mobile", "Téléphone / WhatsApp", "tel", { placeholder: "+212 6…" })}
          ${d.company.company_phone && !d.contact.mobile && !existing ? `<p class="small full" style="margin:-6px 0 0"><button type="button" class="link" id="same-phone">Même numéro que l'entreprise (${esc(d.company.company_phone)})</button></p>` : ""}
          ${input("contact", "email", "E-mail", "email", { full: true })}
        </div>
        <details style="margin-top:12px"><summary><strong>Coller un message WhatsApp</strong> pour repérer le téléphone et l'e-mail</summary>
          <textarea id="wa" placeholder="Collez ici le message reçu du client…" style="margin-top:8px"></textarea>
          <div class="actions" style="margin-top:6px"><button class="btn small" id="wa-parse">Repérer téléphone et e-mail</button></div>
        </details>
      </div>
    </div>

    ${cfg.segment.enabled && !existing ? `<div class="card"><h2>Segmentation</h2><div class="grid">${cfg.segment.fields.map(segmentField).join("")}</div></div>` : ""}

    ${cfg.opportunity.enabled ? `<div class="card"><h2>Besoin du client (opportunité)</h2>
      <div class="grid">
        <label class="field full"><span class="label-row"><span>Nom de l'opportunité *</span><button class="link" id="suggest-name" type="button">Proposer un nom</button></span>
          <input type="text" name="name" data-section="opportunity" value="${esc(d.opportunity.name || "")}" autocomplete="off"></label>
        ${input("opportunity", "quantity", "Nombre de véhicules", "number")}
        ${input("opportunity", "close_date", "Date de clôture estimée", "date", { required: true })}
        <label class="field full"><span>Besoin exprimé</span>
          <textarea name="description" data-section="opportunity" placeholder="Modèle, usage, délais, financement évoqué…">${esc(d.opportunity.description || "")}</textarea></label>
      </div></div>` : ""}`;

  ["contact", "segment", "opportunity"].forEach(bindInputs);
  const mob = $app.querySelector('input[name="mobile"]');
  if (mob) mob.addEventListener("blur", () => {
    const v = normalisePhone(mob.value);
    if (v !== mob.value) { mob.value = v; d.contact.mobile = v; scheduleSave(); }
  });
  const samePhone = document.getElementById("same-phone");
  if (samePhone) samePhone.onclick = () => { d.contact.mobile = d.company.company_phone; scheduleSave(); render(); };
  $app.querySelectorAll('select[data-section="segment"]').forEach((s) => (s.onchange = () => { d.segment[s.name] = s.value; scheduleSave(); }));

  const um = document.getElementById("use-manager");
  if (um) um.onclick = () => {
    const parts = manager.value.replace(/^(m\.|mr|mme|mlle|monsieur|madame)\s+/i, "").trim().split(/\s+/);
    d.contact.last_name = parts.pop() || "";
    d.contact.first_name = parts.join(" ");
    d.contact.title = d.contact.title || "Gérant";
    scheduleSave();
    render();
  };
  const wa = document.getElementById("wa-parse");
  if (wa) wa.onclick = () => {
    const text = document.getElementById("wa").value;
    const email = (text.match(/[^\s@<>()]+@[^\s@<>()]+\.[a-z]{2,}/i) || [])[0];
    const phone = (text.match(/(?:\+212|00212|0)\s*[5-7](?:[\s.-]*\d){8}/) || [])[0];
    if (email) d.contact.email = email;
    if (phone) d.contact.mobile = phone.replace(/[\s.-]/g, "").replace(/^00212/, "+212");
    toast(email || phone ? "Informations repérées : vérifiez-les." : "Aucun téléphone ni e-mail repéré.");
    scheduleSave();
    render();
  };
  const sn = document.getElementById("suggest-name");
  if (sn) sn.onclick = () => {
    const company = d.account_choice.mode === "existing" ? d.account_choice.name : (d.company.common_name || d.company.company_name || "Client");
    const qty = d.opportunity.quantity ? ` - ${d.opportunity.quantity} véhicule(s)` : "";
    d.opportunity.name = `${company}${qty} - ${new Date().toLocaleDateString("fr-FR", { month: "2-digit", year: "numeric" })}`;
    if (!d.opportunity.close_date) {
      const dt = new Date(); dt.setDate(dt.getDate() + 90);
      d.opportunity.close_date = dt.toISOString().slice(0, 10);
    }
    scheduleSave();
    render();
  };
  if (existing) loadContacts();
}

async function loadContacts() {
  const d = state.draft;
  const box = document.getElementById("contacts");
  if (!state.contacts) {
    try { state.contacts = await api("GET", `/api/salesforce/accounts/${encodeURIComponent(d.account_choice.id)}/contacts`); }
    catch (e) { box.innerHTML = `<div class="alert err">${esc(e.message)}</div>`; return; }
  }
  const cur = d.contact_choice;
  box.innerHTML = `
    <p class="muted">Contacts déjà enregistrés sur ${esc(d.account_choice.name)} :</p>
    <ul class="list">
      ${state.contacts.map((c) => `<li><label style="display:flex;gap:10px;align-items:center">
        <input type="radio" name="cchoice" value="${esc(c.id)}" ${cur.mode === "existing" && cur.id === c.id ? "checked" : ""}>
        <span><strong>${esc([c.first_name, c.last_name].filter(Boolean).join(" "))}</strong> <small class="muted">${esc(c.title || "")} ${esc(c.mobile || "")} ${esc(c.email || "")}</small></span></label></li>`).join("")}
      <li><label style="display:flex;gap:10px;align-items:center"><input type="radio" name="cchoice" value="new" ${cur.mode !== "existing" ? "checked" : ""}>
        <strong>Nouveau contact</strong></label></li>
    </ul>`;
  box.querySelectorAll('input[name="cchoice"]').forEach((r) => (r.onchange = () => {
    if (r.value === "new") d.contact_choice = { mode: "new" };
    else {
      const c = state.contacts.find((x) => x.id === r.value);
      d.contact_choice = { mode: "existing", id: c.id, name: [c.first_name, c.last_name].filter(Boolean).join(" ") };
    }
    document.getElementById("contact-form").classList.toggle("hidden", d.contact_choice.mode === "existing");
    scheduleSave();
  }));
}

// ------------------------------------------------------------------ étape 3
async function renderStep3() {
  const d = state.draft;
  const box = document.getElementById("step");
  if (["records_created", "completed"].includes(d.status)) return renderResult(box);

  box.innerHTML = `<div class="card"><p class="muted">Vérification du dossier…</p></div>`;
  try { state.preview = await api("GET", `/api/drafts/${d.id}/preview`); }
  catch (e) { box.innerHTML = `<div class="alert err">${esc(e.message)}</div>`; return; }
  const p = state.preview;
  const previous = d.result && d.result.errors;
  box.innerHTML = `
    ${previous ? `<div class="alert ${d.status === "uncertain" ? "warn" : "err"}"><strong>Tentative précédente :</strong><ul>${previous.map((m) => `<li>${esc(m)}</li>`).join("")}</ul></div>` : ""}
    <div class="card">
      <h2>Ce qui va être fait dans Salesforce</h2>
      <ul class="list summary">${p.summary.map((s) => `<li>${esc(s)}</li>`).join("")}</ul>
    </div>
    ${(p.warnings || []).length ? `<div class="alert warn"><strong>Points d'attention (n'empêchent pas l'envoi) :</strong><ul>${p.warnings.map((m) => `<li>${esc(m)}</li>`).join("")}</ul></div>` : ""}
    ${p.errors.length ? `<div class="alert err"><strong>À compléter avant l'envoi :</strong><ul>${p.errors.map((m) => `<li>${esc(m)}</li>`).join("")}</ul></div>` : ""}
    <div class="card">
      <div class="actions">
        <button class="btn primary" id="submit" ${p.errors.length ? "disabled" : ""}>Créer dans Salesforce</button>
        <span class="muted"><small>Tout est créé en une seule fois : en cas d'erreur, rien n'est enregistré à moitié.</small></span>
      </div>
      <div id="submit-msg"></div>
    </div>`;
  document.getElementById("submit").onclick = submit;
}

async function submit() {
  if (state.busy) return;
  state.busy = true;
  const btn = document.getElementById("submit");
  btn.disabled = true;
  btn.textContent = "Création en cours…";
  await saveNow();
  try {
    state.draft = await api("POST", `/api/drafts/${state.draft.id}/submit`);
  } catch (e) {
    const box = document.getElementById("submit-msg");
    if (box) box.innerHTML = `<div class="alert err"><ul>${e.messages.map((m) => `<li>${esc(m)}</li>`).join("")}</ul></div>`;
    try { state.draft = await api("GET", `/api/drafts/${state.draft.id}`); } catch (_) { /* ignoré */ }
    btn.disabled = false;
    btn.textContent = "Réessayer";
    state.busy = false;
    return;
  }
  state.busy = false;
  render();
}

function renderResult(box) {
  const r = state.draft.result;
  const fileErrors = r.file_errors || [];
  box.innerHTML = `
    <div class="card">
      <div class="alert ok"><strong>Dossier créé dans Salesforce.</strong></div>
      <ul class="list summary">${(r.summary || []).map((s) => `<li>${esc(s)}</li>`).join("")}</ul>
      ${fileErrors.length ? `<div class="alert warn"><strong>Pièces jointes non envoyées :</strong><ul>${fileErrors.map((m) => `<li>${esc(m)}</li>`).join("")}</ul>
        <button class="btn small" id="retry-files">Réessayer l'envoi des pièces</button></div>` : ""}
      <div class="actions" style="margin-top:14px">
        ${r.links.opportunity ? `<a class="btn primary" href="${esc(r.links.opportunity)}" target="_blank" rel="noopener">Ouvrir l'opportunité</a>` : ""}
        <a class="btn" href="${esc(r.links.account)}" target="_blank" rel="noopener">Ouvrir le compte</a>
        ${r.links.contact ? `<a class="btn" href="${esc(r.links.contact)}" target="_blank" rel="noopener">Ouvrir le contact</a>` : ""}
      </div>
      ${state.config.mode === "mock" ? `<p class="muted"><small>Démonstration : les liens pointent vers une org fictive et ne s'ouvrent pas.</small></p>` : ""}
    </div>
    <div class="card">
      <h2>Étape suivante (manuelle)</h2>
      <p>Dans l'opportunité, cliquez sur <strong>« Créer/afficher un devis VSS »</strong> pour préparer le devis dans VSS4.</p>
    </div>
    <div class="actions"><button class="btn" id="home">Retour aux dossiers</button></div>`;
  document.getElementById("home").onclick = renderHome;
  const rf = document.getElementById("retry-files");
  if (rf) rf.onclick = async () => {
    rf.disabled = true;
    try { state.draft = await api("POST", `/api/drafts/${state.draft.id}/submit`); } catch (e) { toast(e.message); }
    render();
  };
}

boot().catch((e) => { $app.innerHTML = `<div class="alert err">${esc(e.message)}</div>`; });
