const byId = id => document.getElementById(id);
const actorSelect = byId("actors");
const queryInput = byId("query");
const dialog = byId("document-dialog");
const state = { token: "", profile: null, epoch: 0, search: 0, document: 0, demo: false };

function element(tag, value = "", className = "") {
  const node = document.createElement(tag);
  if (value) node.textContent = value;
  if (className) node.className = className;
  return node;
}

function alert(id, value = "") {
  const node = byId(id);
  node.textContent = value;
  node.hidden = !value;
}

async function api(path, options = {}) {
  const headers = { Accept: "application/json" };
  const token = options.token ?? state.token;
  if (token) headers.Authorization = `Bearer ${token}`;
  if (options.body) headers["Content-Type"] = "application/json";
  const response = await fetch(path, {
    method: options.method || "GET",
    headers,
    body: options.body ? JSON.stringify(options.body) : undefined,
    cache: "no-store",
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(typeof payload.detail === "string" ? payload.detail : "The request could not be completed.");
  }
  return payload;
}

function closeDocument() {
  state.document += 1;
  if (dialog.open) dialog.close();
  for (const id of ["document-title", "document-collection", "document-body"]) byId(id).textContent = "";
}

function clearWorkspace() {
  closeDocument();
  byId("results-list").replaceChildren();
  byId("access-groups").replaceChildren();
  byId("audit-list").replaceChildren();
  byId("answer-content").replaceChildren(element("p", "Run a search to see the most relevant passages and their sources."));
  byId("results-summary").textContent = "Search your workspace to get started.";
  byId("access-status").textContent = "";
  byId("cache-label").hidden = true;
  alert("search-error");
  alert("access-error");
}

function startSession() {
  state.epoch += 1;
  state.search += 1;
  state.token = "";
  state.profile = null;
  byId("navigation").hidden = true;
  byId("workspace").hidden = true;
  byId("workspace-app").hidden = true;
  byId("account").hidden = true;
  byId("page-title").textContent = "Search";
  byId("top-context").textContent = "Workspace";
  clearWorkspace();
  return state.epoch;
}

async function applyToken(token, epoch) {
  const profile = await api("/v1/me", { token });
  if (state.epoch !== epoch) return;
  state.token = token;
  state.profile = profile;
  byId("workspace-name").textContent = profile.tenant_name;
  byId("workspace-initial").textContent = profile.tenant_name[0].toUpperCase();
  byId("account-name").textContent = profile.name;
  const label = actorSelect.selectedOptions[0]?.dataset.description;
  byId("account-role").textContent = state.demo ? (label || "Demo account") :
    (profile.role === "admin" ? "Administrator" : "Member");
  byId("avatar").textContent = profile.name.split(/\s+/).slice(0, 2).map(part => part[0].toUpperCase()).join("");
  byId("top-context").textContent = profile.tenant_name;
  byId("account").hidden = false;
  byId("workspace").hidden = false;
  byId("navigation").hidden = false;
  byId("workspace-app").hidden = false;
  byId("nav-access").hidden = profile.role !== "admin";
  byId("logout").hidden = state.demo;
  byId("signin").hidden = true;
  byId("suggestions").hidden = !state.demo;
  setView("search");
  if (state.demo) {
    queryInput.value = "vendor policy";
    await search();
  }
}

async function signInDemo(actor) {
  const epoch = startSession();
  try {
    const result = await api("/v1/demo/session", { method: "POST", body: { actor }, token: "" });
    if (epoch === state.epoch) await applyToken(result.token, epoch);
  } catch (error) {
    if (epoch === state.epoch) {
      byId("signin").hidden = false;
      alert("signin-error", error.message);
    }
  }
}

function setView(name) {
  const isSearch = name === "search";
  byId("search-view").hidden = !isSearch;
  byId("access-view").hidden = isSearch;
  byId("page-title").textContent = isSearch ? "Search" : "Access review";
  byId(isSearch ? "nav-search" : "nav-access").setAttribute("aria-current", "page");
  byId(isSearch ? "nav-access" : "nav-search").removeAttribute("aria-current");
}

async function showDocument(slug) {
  closeDocument();
  const epoch = state.epoch;
  const requestId = state.document;
  try {
    const data = await api(`/v1/documents/${encodeURIComponent(slug)}`);
    if (epoch !== state.epoch || requestId !== state.document) return;
    byId("document-title").textContent = data.title;
    byId("document-collection").textContent = data.collection;
    byId("document-body").textContent = data.body;
    dialog.showModal();
  } catch (error) {
    if (epoch === state.epoch && requestId === state.document) alert("search-error", error.message);
  }
}

function documentButton(value, slug, className) {
  const button = element("button", value, className);
  button.type = "button";
  button.addEventListener("click", () => showDocument(slug));
  return button;
}

function renderSearch(payload) {
  const total = payload.results.length;
  byId("results-summary").textContent = `${total} ${total === 1 ? "result" : "results"} for “${payload.query}”`;
  const cache = byId("cache-label");
  cache.hidden = !state.demo;
  cache.textContent = payload.cache === "hit" ? "Cached search" : "Fresh search";
  const rows = payload.results.map(hit => {
    const row = element("li", "", "result");
    const icon = element("span", "▤", "file-icon");
    icon.setAttribute("aria-hidden", "true");
    const description = element("div");
    description.append(documentButton(hit.title, hit.slug, "result-title"), element("p", hit.excerpt));
    const metadata = element("div", "", "metadata");
    metadata.append(element("span", hit.collection));
    if (hit.group_slug) metadata.append(element("span", hit.group_slug, "group-label"));
    description.append(metadata);
    row.append(icon, description);
    return row;
  });
  byId("results-list").replaceChildren(...rows);
}

function renderAnswer(payload) {
  const target = byId("answer-content");
  if (!payload.citations.length) {
    target.replaceChildren(element("p", "No accessible documents answer this search."));
    return;
  }
  const quote = element("blockquote", payload.citations[0].excerpt);
  const list = element("ul");
  for (const hit of payload.citations) {
    const row = element("li");
    row.append(documentButton(hit.title, hit.slug, "citation"));
    list.append(row);
  }
  const mode = payload.mode === "local-model" ? "Source selection by the local model" : "Selected from search results";
  target.replaceChildren(quote, list, element("p", mode, "answer-mode"));
}

async function search() {
  const value = queryInput.value.trim();
  if (!value || !state.profile) return;
  const epoch = state.epoch;
  const number = ++state.search;
  const current = () => epoch === state.epoch && number === state.search;
  alert("search-error");
  byId("results-list").replaceChildren();
  byId("cache-label").hidden = true;
  byId("results-summary").textContent = "Searching…";
  byId("answer-content").replaceChildren(element("p", "Finding accessible sources…"));
  try {
    const result = await api(`/v1/search?${new URLSearchParams({ q: value })}`);
    if (!current()) return;
    renderSearch(result);
    const cited = await api("/v1/answer", { method: "POST", body: { query: value } });
    if (current()) renderAnswer(cited);
  } catch (error) {
    if (!current()) return;
    alert("search-error", error.message);
    if (byId("results-summary").textContent === "Searching…") byId("results-summary").textContent = "Search unavailable.";
    byId("answer-content").replaceChildren(element("p", "A cited answer is unavailable for this request."));
  }
}

async function showAccess() {
  if (state.profile?.role !== "admin") return;
  const epoch = state.epoch;
  setView("access");
  alert("access-error");
  try {
    const data = await api("/v1/admin/access");
    if (epoch === state.epoch) renderAccess(data);
  } catch (error) {
    if (epoch === state.epoch) alert("access-error", error.message);
  }
}

function renderAccess(data) {
  const groups = data.groups.map(group => {
    const card = element("article", "", "group-card");
    card.append(element("h3", group.name));
    for (const user of data.users) {
      const enabled = user.groups.includes(group.slug);
      const row = element("div", "", "person-row");
      const nextAction = enabled ? "Revoke" : "Grant";
      const action = element("button", nextAction, "permission-button");
      action.type = "button";
      action.dataset.action = nextAction.toLowerCase();
      action.setAttribute("aria-label", `${nextAction} ${group.name} access for ${user.name}`);
      action.addEventListener("click", () => updateAccess(group, user, !enabled, action));
      row.append(element("span", user.name), action);
      card.append(row);
    }
    return card;
  });
  byId("access-groups").replaceChildren(...(groups.length ? groups : [element("p", "This workspace has no groups.")]));
  const entries = data.audit.map(item => element("li", `${item.actor_handle} · ${item.action.replaceAll("_", " ")} · ${item.target}`));
  byId("audit-list").replaceChildren(...(entries.length ? entries : [element("li", "No permission changes yet.")]));
}

async function updateAccess(group, user, enabled, action) {
  const epoch = state.epoch;
  action.disabled = true;
  try {
    const path = `/v1/admin/groups/${encodeURIComponent(group.slug)}/members/${encodeURIComponent(user.handle)}`;
    await api(path, { method: "PUT", body: { enabled } });
    if (epoch !== state.epoch) return;
    byId("access-status").textContent = `${group.name} access ${enabled ? "granted to" : "revoked for"} ${user.name}.`;
    await showAccess();
    if (epoch !== state.epoch) return;
    state.search += 1;
    byId("results-list").replaceChildren();
    byId("answer-content").replaceChildren(element("p", "Run a new search after the permission change."));
    byId("results-summary").textContent = "Search your workspace to refresh results.";
    byId("cache-label").hidden = true;
  } catch (error) {
    if (epoch === state.epoch) alert("access-error", error.message);
  } finally {
    action.disabled = false;
  }
}

byId("signin-form").addEventListener("submit", async event => {
  event.preventDefault();
  const token = byId("token").value.trim();
  const epoch = startSession();
  try {
    await applyToken(token, epoch);
    byId("token").value = "";
  } catch (error) {
    if (epoch === state.epoch) alert("signin-error", error.message);
  }
});
byId("logout").addEventListener("click", () => {
  startSession();
  queryInput.value = "";
  byId("signin").hidden = false;
});
byId("search-form").addEventListener("submit", event => {
  event.preventDefault();
  search();
});
byId("suggestions").addEventListener("click", event => {
  if (event.target instanceof HTMLButtonElement && event.target.dataset.query) {
    queryInput.value = event.target.dataset.query;
    search();
  }
});
actorSelect.addEventListener("change", () => signInDemo(actorSelect.value));
byId("nav-search").addEventListener("click", () => setView("search"));
byId("nav-access").addEventListener("click", showAccess);
byId("close-document").addEventListener("click", closeDocument);
dialog.addEventListener("cancel", closeDocument);
document.addEventListener("keydown", event => {
  if (event.key === "/" && !dialog.open && !byId("search-view").hidden && state.profile &&
      !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)) {
    event.preventDefault();
    queryInput.focus();
  }
});

async function initialize() {
  try {
    const data = await api("/v1/demo/actors", { token: "" });
    if (!data.actors.length) throw new Error("No demo accounts are available.");
    state.demo = true;
    for (const actor of data.actors) {
      const option = element("option", `${actor.name} · ${actor.workspace}`);
      option.value = actor.actor;
      option.dataset.description = actor.role;
      actorSelect.append(option);
    }
    byId("demo-select").hidden = false;
    await signInDemo(actorSelect.value);
  } catch {
    byId("signin").hidden = false;
  }
}
initialize();
