"use strict";

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const state = {
  user: null, authMode: "login", accounts: [], accountId: null, epoch: 0,
  chatRequest: 0, documentRequest: 0, historyRequest: 0, reviewRequest: 0,
  history: [], chatting: false, mode: null, operation: "deposit", moneyKey: null,
  moneyBusy: false, historyOffset: 0, historyHasNext: false, kyc: null,
  reviews: [], reviewAccountId: null, reviewBusy: false, demoMode: false
};
const labels = { deposit: "Deposit", withdraw: "Withdrawal", withdrawal: "Withdrawal", transfer_in: "Transfer received", transfer_out: "Transfer sent", full_name: "Full name", id_number: "ID number", document_type: "Document type" };
const money = (value) => new Intl.NumberFormat("en-PK", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Number(value));
const accountPath = (suffix = "") => "/accounts/" + encodeURIComponent(state.accountId) + suffix;
const jsonOptions = (method, payload) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function notice(selector, message = "", error = false) {
  const node = $(selector);
  node.textContent = message;
  node.hidden = !message;
  node.classList.toggle("error", error);
  node.classList.toggle("success", !error && !!message);
}

function globalError(message = "") { notice("#global-error", message, true); }

async function api(path, options = {}) {
  const { allowUnauthorized = false, ...fetchOptions } = options;
  const requestEpoch = state.epoch;
  let response;
  try { response = await fetch(path, { credentials: "same-origin", ...fetchOptions }); }
  catch {
    const error = new Error("Could not reach the server. Check your connection and try again.");
    error.network = true;
    throw error;
  }
  let body;
  try { body = await response.json(); } catch { body = null; }
  if (!response.ok) {
    const detail = body?.detail;
    const message = typeof detail === "string" ? detail : Array.isArray(detail)
      ? detail.map((item) => (item.loc?.slice(-1)[0] || "Field") + ": " + item.msg).join("; ")
      : "The request could not be completed. Please try again.";
    if (response.status === 401 && !allowUnauthorized && state.user && requestEpoch === state.epoch) {
      clearSession();
      notice("#auth-error", "Your session has expired. Please sign in again.", true);
    }
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }
  return body;
}

function parseDate(value) {
  if (!value) return null;
  const date = new Date(value.endsWith("Z") || /[+-]\d\d:\d\d$/.test(value) ? value : value + "Z");
  return Number.isNaN(date.getTime()) ? null : date;
}

function dateLabel(value, includeTime = false) {
  const date = parseDate(value);
  if (!date) return "—";
  return includeTime ? date.toLocaleString("en-GB", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" })
    : date.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

function setAuthMode(mode) {
  state.authMode = mode;
  const register = mode === "register";
  $("#register-name-field").hidden = !register;
  $("#auth-name").required = register;
  $("#auth-password").minLength = register ? 10 : 1;
  $("#auth-password").autocomplete = register ? "new-password" : "current-password";
  $("#password-help").hidden = !register;
  $("#auth-heading").textContent = register ? "Make yourself at home." : "Welcome back.";
  $("#auth-description").textContent = register ? "Create a customer profile, then open your demo account." : "Sign in to see your account and activity.";
  $("#auth-submit").textContent = register ? "Create profile →" : "Sign in →";
  $$("[data-auth-mode]").forEach((button) => {
    button.classList.toggle("selected", button.dataset.authMode === mode);
    button.setAttribute("aria-pressed", String(button.dataset.authMode === mode));
  });
  notice("#auth-error");
}

function showTab(tabName) {
  const reviewer = state.user?.role === "reviewer";
  if (reviewer && tabName !== "reviews") tabName = "reviews";
  if (!reviewer && tabName === "reviews") tabName = "banking";
  const descriptions = {
    banking: ["Your banking, in focus.", "A clear view of your account and every move."],
    assistant: ["A little clarity, on demand.", "Ask a question. Get a clearer picture of your account."],
    document: ["Confidence in the details.", "Prepare your identity fields and follow their review."],
    reviews: ["Good decisions start here.", "Review identity details and keep a clear record of every decision."]
  };
  for (const name of Object.keys(descriptions)) $("#" + name + "-panel").hidden = name !== tabName;
  $("#page-title").textContent = descriptions[tabName][0];
  $("#page-description").textContent = descriptions[tabName][1];
  $$("[data-tab]").forEach((button) => {
    button.classList.toggle("selected", button.dataset.tab === tabName);
    if (button.dataset.tab === tabName) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  if (tabName === "document" && state.accountId) loadKyc().catch((error) => { if (state.user) notice("#kyc-message", error.message, true); });
  if (tabName === "reviews") loadReviews().catch((error) => { if (state.user) globalError(error.message); });
}

function clearSession() {
  state.user = null;
  state.accounts = [];
  state.accountId = null;
  state.epoch += 1;
  state.reviewRequest += 1;
  state.historyRequest += 1;
  state.reviews = [];
  state.reviewAccountId = null;
  state.reviewBusy = false;
  state.moneyBusy = false;
  state.moneyKey = null;
  state.kyc = null;
  document.body.classList.add("signed-out");
  $("#auth-shell").hidden = false;
  $("#workspace").hidden = true;
  $("#sidebar").hidden = true;
  $("#auth-password").value = "";
  $("#profile-name").textContent = "";
  $("#profile-role").textContent = "";
  $("#account-select").replaceChildren(element("option", "", "No account selected"));
  $("#account-id-display").value = "";
  $("#balance-value").textContent = "—";
  $("#account-owner").textContent = "Open an account to begin";
  $("#account-status").textContent = "—";
  $("#transaction-list").replaceChildren();
  $("#history-list").replaceChildren();
  $("#review-list").replaceChildren();
  $("#review-detail").replaceChildren();
  $("#customer-audit").replaceChildren();
  notice("#money-message");
  notice("#kyc-message");
  $("#copy-message").textContent = "";
  $("#account-identity").textContent = "";
  $("#money-form").reset();
  $("#kyc-form").reset();
  if ($("#account-dialog").open) $("#account-dialog").close();
  resetChat();
  resetDocument();
  globalError();
}

async function enterWorkspace(user) {
  clearSession();
  state.user = user;
  const epoch = state.epoch;
  const reviewer = user.role === "reviewer";
  document.body.classList.remove("signed-out");
  $("#auth-shell").hidden = true;
  $("#sidebar").hidden = false;
  $("#workspace").hidden = false;
  $("#profile-name").textContent = user.full_name;
  $("#profile-role").textContent = reviewer ? "Reviewer" : "Customer";
  $("#account-area").hidden = reviewer;
  $("#overview").hidden = reviewer;
  $("#empty-account").hidden = true;
  $$("[data-role]").forEach((node) => { node.hidden = node.dataset.role !== user.role; });
  showTab(reviewer ? "reviews" : "banking");
  if (!reviewer) {
    try { await loadAccounts(); }
    catch (error) { if (epoch === state.epoch) globalError("Could not load your account: " + error.message); }
  }
}

function setMoneyBusy(busy) {
  state.moneyBusy = busy;
  $$("#money-form input, [data-operation]").forEach((node) => { node.disabled = busy; });
  $("#money-submit").disabled = busy || !state.accountId;
  $("#account-select").disabled = busy || state.accounts.length < 2;
}

function setOperation(operation) {
  if (state.moneyBusy) return;
  state.operation = operation;
  state.moneyKey = null;
  $("#recipient-field").hidden = operation !== "transfer";
  $("#recipient-id").required = operation === "transfer";
  $("#money-submit").textContent = ({ deposit: "Deposit funds →", withdraw: "Withdraw funds →", transfer: "Send transfer →" })[operation];
  $$("[data-operation]").forEach((button) => {
    button.classList.toggle("selected", button.dataset.operation === operation);
    button.setAttribute("aria-pressed", String(button.dataset.operation === operation));
  });
  notice("#money-message");
}

function renderOverview(account) {
  $("#balance-value").textContent = account ? money(account.balance) : "—";
  $("#account-owner").textContent = account?.owner_name || "Open an account to begin";
  $("#account-status").textContent = account?.status || "—";
  $("#account-id-display").value = account?.id || "";
  $("#copy-account-id").disabled = !account;
  $("#refresh-banking").disabled = !account;
  setMoneyBusy(state.moneyBusy);
}

function renderTransactions(transactions) {
  const list = $("#transaction-list");
  list.replaceChildren();
  if (!transactions.length) {
    list.append(element("p", "muted", "No transactions yet. A fresh start."));
    return;
  }
  transactions.slice(0, 3).forEach((transaction) => {
    const outgoing = ["withdrawal", "transfer_out"].includes(transaction.type);
    const row = element("div", "transaction-row");
    const icon = element("span", "transaction-icon", outgoing ? "↗" : "↙");
    icon.setAttribute("aria-hidden", "true");
    const info = element("div", "transaction-info");
    info.append(element("strong", "", transaction.note || labels[transaction.type] || transaction.type), element("span", "", dateLabel(transaction.created_at)));
    row.append(icon, info, element("span", "transaction-amount", (outgoing ? "−" : "+") + " " + money(transaction.amount)));
    list.append(row);
  });
}

async function refreshAccount() {
  if (!state.accountId) return;
  const epoch = state.epoch;
  const accountId = state.accountId;
  const [account, transactions] = await Promise.all([
    api("/accounts/" + encodeURIComponent(accountId)),
    api("/accounts/" + encodeURIComponent(accountId) + "/transactions?limit=3&offset=0")
  ]);
  if (epoch !== state.epoch) return;
  state.accounts = state.accounts.map((item) => item.id === account.id ? account : item);
  renderOverview(account);
  renderTransactions(transactions);
}

async function chooseAccount(accountId) {
  state.accountId = accountId || null;
  state.epoch += 1;
  const epoch = state.epoch;
  state.historyOffset = 0;
  state.moneyKey = null;
  state.moneyBusy = false;
  state.kyc = null;
  $("#account-select").value = state.accountId || "";
  $("#money-form").reset();
  $("#history-filter").value = "";
  $("#kyc-form").reset();
  $("#kyc-name").value = state.user?.full_name || "";
  $("#customer-audit").replaceChildren();
  notice("#money-message");
  notice("#kyc-message");
  $("#copy-message").textContent = "";
  renderOverview(state.accounts.find((account) => account.id === state.accountId));
  resetChat();
  resetDocument();
  applyKycStatus(null);
  if (!state.accountId) {
    renderTransactions([]);
    $("#history-list").replaceChildren(element("p", "muted", "Open an account to start your transaction history."));
    $("#history-prev").disabled = true;
    $("#history-next").disabled = true;
    return;
  }
  $("#transaction-list").replaceChildren(element("p", "muted", "Loading activity…"));
  const results = await Promise.allSettled([refreshAccount(), loadHistory(), loadKyc()]);
  if (epoch !== state.epoch) return;
  const failure = results.find((result) => result.status === "rejected");
  if (failure) globalError(failure.reason.message);
}

async function loadAccounts(preferredId = null) {
  const epoch = state.epoch;
  const accounts = await api("/accounts/");
  if (epoch !== state.epoch) return;
  state.accounts = accounts;
  const select = $("#account-select");
  select.replaceChildren();
  if (!accounts.length) select.append(element("option", "", "No account yet"));
  accounts.forEach((account) => {
    const option = element("option", "", account.owner_name);
    option.value = account.id;
    select.append(option);
  });
  $("#new-account").hidden = accounts.length > 0;
  $("#empty-account").hidden = accounts.length > 0;
  await chooseAccount(preferredId || accounts[0]?.id || null);
}

async function loadHistory() {
  const request = ++state.historyRequest;
  const epoch = state.epoch;
  $("#history-prev").disabled = true;
  $("#history-next").disabled = true;
  if (!state.accountId) return;
  $("#history-list").replaceChildren(element("p", "muted", "Loading transaction history…"));
  const query = new URLSearchParams({ limit: "20", offset: String(state.historyOffset) });
  if ($("#history-filter").value) query.set("type", $("#history-filter").value);
  try {
    const transactions = await api(accountPath("/transactions?") + query);
    if (epoch !== state.epoch || request !== state.historyRequest) return;
    const container = $("#history-list");
    container.replaceChildren();
    if (!transactions.length) container.append(element("p", "muted", state.historyOffset ? "No more activity on this page." : "No activity matches this filter."));
    else {
      const table = element("table", "history-table");
      table.setAttribute("aria-label", "Your transaction history");
      const head = element("thead");
      const heading = element("tr");
      ["Activity", "When", "Amount (PKR)", "Balance after"].forEach((title) => { const cell = element("th", "", title); cell.scope = "col"; heading.append(cell); });
      head.append(heading);
      const body = element("tbody");
      transactions.forEach((transaction) => {
        const row = element("tr");
        const description = element("td", "history-description");
        description.append(element("strong", "", labels[transaction.type] || transaction.type));
        if (transaction.note) description.append(element("span", "", transaction.note));
        if (transaction.related_account_id) description.append(element("span", "history-reference", "Account: " + transaction.related_account_id));
        const outgoing = ["withdrawal", "transfer_out"].includes(transaction.type);
        row.append(description, element("td", "history-date", dateLabel(transaction.created_at, true)), element("td", outgoing ? "history-amount outgoing" : "history-amount", (outgoing ? "−" : "+") + money(transaction.amount)), element("td", "history-amount", money(transaction.balance_after)));
        body.append(row);
      });
      table.append(head, body);
      container.append(table);
    }
    state.historyHasNext = transactions.length === 20;
    $("#history-page").textContent = "PAGE " + (state.historyOffset / 20 + 1);
    $("#history-prev").disabled = state.historyOffset === 0;
    $("#history-next").disabled = !state.historyHasNext;
  } catch (error) {
    if (epoch !== state.epoch || request !== state.historyRequest) return;
    $("#history-list").replaceChildren(element("p", "document-error", error.message));
    $("#history-prev").disabled = state.historyOffset === 0;
  }
}

function appendMessage(role, content, { sources = [], tools_used = [], mode = null, pending = false, error = false } = {}) {
  const row = element("div", "message " + role + (pending ? " pending" : "") + (error ? " error" : ""));
  const avatar = element("span", "message-avatar", role === "user" ? "YOU" : "v.");
  avatar.setAttribute("aria-hidden", "true");
  const body = element("div", "message-body");
  if (role !== "user") body.append(element("span", "message-name", "VAULT ASSISTANT"));
  body.append(element("p", "message-text", content));
  if (tools_used.length) body.append(element("span", "message-tag", "Tools used · " + tools_used.map((tool) => tool.replaceAll("_", " ")).join(", ")));
  if (mode === "demo") body.append(element("span", "message-tag", "Demo response"));
  if (sources.length) {
    const sourceList = element("div", "source-list");
    sources.forEach((source, index) => {
      const card = element("details", "source");
      card.append(element("summary", "", "[" + (source.id || index + 1) + "] " + (source.title || "Knowledge source")), element("p", "", source.text || ""));
      sourceList.append(card);
    });
    body.append(sourceList);
  }
  row.append(avatar, body);
  $("#chat-log").append(row);
  $("#chat-log").scrollTop = $("#chat-log").scrollHeight;
  return row;
}

function resetChat() {
  state.history = [];
  state.chatting = false;
  state.chatRequest += 1;
  $("#send-message").disabled = !state.user;
  $("#chat-input").value = "";
  $("#chat-input").style.height = "auto";
  $("#chat-log").replaceChildren();
  if (!state.user) return;
  const account = state.accounts.find((item) => item.id === state.accountId);
  appendMessage("assistant", account
    ? "Hello, " + state.user.full_name.split(" ")[0] + ". Let’s make sense of your banking.\n\nI can look up your balance and recent transactions, or help with questions about our sample banking policies."
    : "Welcome to Vault. I can answer questions about our sample banking policies.\n\nOpen your account to explore your balance and recent transactions, too.");
}

function showMode(mode) {
  state.mode = mode;
  const provider = mode === "gemini" ? "Gemini" : "OpenAI";
  $("#assistant-mode").textContent = mode === "demo" ? "Demo responses" : provider + " connected";
  $("#demo-tip").hidden = mode !== "demo";
}

async function sendChat(message) {
  if (!state.user || state.chatting || !message.trim()) return;
  const content = message.trim();
  state.chatting = true;
  const request = ++state.chatRequest;
  const epoch = state.epoch;
  const history = state.history.slice(-12).map((item) => ({ ...item, content: item.content.slice(0, 4000) }));
  $("#chat-input").value = "";
  $("#chat-input").style.height = "auto";
  $("#send-message").disabled = true;
  appendMessage("user", content);
  const pending = appendMessage("assistant", "Looking into that…", { pending: true });
  try {
    const reply = await api("/assistant/chat", jsonOptions("POST", { message: content, account_id: state.accountId, history }));
    if (epoch !== state.epoch || request !== state.chatRequest) return;
    pending.remove();
    appendMessage("assistant", reply.reply, reply);
    state.history.push({ role: "user", content }, { role: "assistant", content: reply.reply });
    showMode(reply.mode);
  } catch (error) {
    if (epoch !== state.epoch || request !== state.chatRequest) return;
    pending.remove();
    appendMessage("assistant", error.message, { error: true });
    if (state.mode && state.mode !== "demo" && [429, 503, 504].includes(error.status)) {
      const provider = state.mode === "gemini" ? "Gemini" : "OpenAI";
      $("#assistant-mode").textContent = provider + (error.status === 429 ? " quota reached" : error.status === 504 ? " timed out" : " unavailable");
    }
  } finally {
    if (epoch === state.epoch && request === state.chatRequest) {
      state.chatting = false;
      $("#send-message").disabled = false;
      $("#chat-input").focus();
    }
  }
}

function resetDocument() {
  state.documentRequest += 1;
  $("#document-form").reset();
  $("#file-label").textContent = "Choose a sample document";
  $("#document-progress").textContent = "";
  $("#analyze-document").disabled = !state.accountId;
  const placeholder = element("div", "review-placeholder");
  placeholder.append(element("span", "placeholder-icon", "▤"), element("h3", "", "The details will appear here."), element("p", "", "We’ll extract the name and ID, highlight differences, and prepare the fields for your review."), element("span", "small-label", "EXTRACT → CHECK → SUBMIT"));
  $("#document-output").replaceChildren(placeholder);
}

function renderDocument(result) {
  const output = element("div", "review-results");
  output.append(element("span", "review-status", result.recommendation === "ready_for_review" ? "Ready to check" : "Check the highlighted details"), element("h3", "", "Document comparison"), element("p", "", "Extraction supports review. It does not authenticate a document or approve KYC."));
  const statuses = { match: "Match", mismatch: "Mismatch", missing: "Missing", not_provided: "No comparison" };
  (result.checks || []).forEach((check) => {
    const row = element("div", "check-row");
    const heading = element("div", "check-heading");
    heading.append(element("strong", "", labels[check.field] || check.field), element("span", "check-status " + check.status, statuses[check.status] || check.status));
    const values = element("div", "check-details");
    values.append(element("span", "", "Expected"), element("span", "", check.expected || "Not provided"), element("span", "", "Extracted"), element("span", "", check.actual || "Not found"));
    row.append(heading, values);
    output.append(row);
  });
  if (result.extracted?.document_type) output.append(element("p", "", "Document type: " + result.extracted.document_type));
  if (result.flags?.length) {
    const flags = element("div", "review-flags");
    result.flags.forEach((flag) => flags.append(element("p", "", flag)));
    output.append(flags);
  }
  const details = element("details", "raw-text");
  details.append(element("summary", "", "View extracted text"), element("pre", "", result.raw_text || "No text was extracted."));
  output.append(details, element("p", "", "Extraction: " + (result.method || "unknown").replaceAll("_", " ")));
  $("#document-output").replaceChildren(output);
  if (!state.kyc || state.kyc.status === "rejected") {
    $("#kyc-name").value = result.extracted?.full_name || state.user?.full_name || "";
    $("#kyc-id").value = result.extracted?.id_number || "";
    $("#kyc-type").value = result.extracted?.document_type || "";
    notice("#kyc-message", "Extracted fields are ready below. Check and correct them before submitting.");
  }
}

function applyKycStatus(kyc) {
  const previous = state.kyc;
  const changed = !previous || !kyc || previous.id !== kyc.id || previous.status !== kyc.status || previous.submitted_at !== kyc.submitted_at;
  state.kyc = kyc;
  const locked = kyc?.status === "pending" || kyc?.status === "verified";
  $("#kyc-status").textContent = kyc ? ({ pending: "Awaiting review", verified: "Verified", rejected: "Rejected" })[kyc.status] || kyc.status : "Not submitted";
  $$("#kyc-form input").forEach((input) => { input.disabled = locked || !state.accountId; });
  $("#submit-kyc").disabled = locked || !state.accountId;
  $("#submit-kyc").textContent = kyc?.status === "rejected" ? "Resubmit for review →" : "Submit for review →";
  if (kyc) {
    if (changed || locked) {
      $("#kyc-name").value = kyc.full_name;
      $("#kyc-id").value = kyc.id_number;
      $("#kyc-type").value = kyc.document_type;
    }
    if (kyc.status === "pending") notice("#kyc-message", "Your submission is waiting for a reviewer’s decision.");
    if (kyc.status === "verified") notice("#kyc-message", "Your KYC submission has been verified.");
    if (kyc.status === "rejected" && changed) notice("#kyc-message", "Review rejected" + (kyc.review_reason ? ": " + kyc.review_reason : ".") + " Correct your fields and resubmit.", true);
  }
}

function renderAudit(container, reviews) {
  container.replaceChildren();
  if (!reviews.length) { container.append(element("p", "muted", "No review decisions recorded yet.")); return; }
  reviews.forEach((review) => {
    const row = element("div", "audit-entry");
    const heading = element("div", "audit-heading");
    heading.append(element("strong", "decision " + review.decision, review.decision === "verified" ? "Verified" : "Rejected"), element("span", "", dateLabel(review.created_at, true)));
    row.append(heading, element("p", "", review.reason || "No reason recorded."), element("span", "audit-reviewer", "Reviewer: " + (review.reviewer_name || review.reviewer_id || "—")));
    container.append(row);
  });
}

async function loadKyc() {
  if (!state.accountId) return;
  const epoch = state.epoch;
  let kyc;
  try { kyc = await api("/kyc/" + encodeURIComponent(state.accountId)); }
  catch (error) { if (error.status !== 404) throw error; kyc = null; }
  if (epoch !== state.epoch) return;
  applyKycStatus(kyc);
  if (!kyc) { renderAudit($("#customer-audit"), []); return; }
  const reviews = await api("/kyc/" + encodeURIComponent(state.accountId) + "/reviews");
  if (epoch === state.epoch) renderAudit($("#customer-audit"), reviews);
}

async function loadReviews() {
  if (state.user?.role !== "reviewer") return;
  const epoch = state.epoch;
  const request = ++state.reviewRequest;
  $("#refresh-reviews").disabled = true;
  $("#review-list").replaceChildren(element("p", "muted", "Loading submissions…"));
  try {
    const records = await api("/kyc/reviews/pending");
    if (epoch !== state.epoch || request !== state.reviewRequest) return;
    state.reviews = records;
    if (state.reviewAccountId && !records.some((record) => record.account_id === state.reviewAccountId)) {
      state.reviewAccountId = null;
      $("#review-detail").replaceChildren(element("p", "queue-empty", "That submission is no longer pending. Choose another submission to continue."));
    }
    $("#review-count").textContent = records.length + " PENDING";
    $("#review-list").replaceChildren();
    if (!records.length) {
      $("#review-list").append(element("p", "queue-empty", "All caught up. No pending identity submissions."));
      if (!state.reviewAccountId && !$("#review-detail .review-complete")) $("#review-detail").replaceChildren(element("div", "queue-empty", "New submissions will appear here when customers send their details."));
    }
    records.forEach((record) => {
      const button = element("button", "queue-item" + (record.account_id === state.reviewAccountId ? " selected" : ""));
      button.type = "button";
      button.dataset.reviewAccount = record.account_id;
      button.append(element("strong", "", record.owner_name || record.full_name), element("span", "", record.document_type + " · Submitted " + dateLabel(record.submitted_at)), element("span", "queue-account", record.account_id));
      button.addEventListener("click", () => chooseReview(record));
      $("#review-list").append(button);
    });
  } finally { if (epoch === state.epoch && request === state.reviewRequest) $("#refresh-reviews").disabled = false; }
}

async function chooseReview(record) {
  if (state.reviewBusy) return;
  state.reviewAccountId = record.account_id;
  const epoch = state.epoch;
  const accountId = record.account_id;
  $$(".queue-item").forEach((button) => { button.classList.toggle("selected", button.dataset.reviewAccount === accountId); });
  const container = $("#review-detail");
  const meta = element("div", "review-meta");
  meta.append(element("span", "small-label", "PENDING IDENTITY REVIEW"), element("h3", "", record.owner_name || record.full_name));
  const fields = element("dl", "review-fields");
  [["Account owner", record.owner_name || "—"], ["Submitted name", record.full_name], ["ID number", record.id_number], ["Document type", record.document_type], ["Account ID", record.account_id], ["Submitted", dateLabel(record.submitted_at, true)]].forEach(([label, value]) => { fields.append(element("dt", "", label), element("dd", "", value || "—")); });
  meta.append(fields, element("p", "muted", "Check the submitted fields against the account owner. This demo reviews text fields; uploaded images are processed locally and are not retained."));
  const form = element("form", "review-decision-form");
  form.id = "review-decision-form";
  const label = element("label", "field-label", "Reason for your decision");
  label.htmlFor = "review-reason";
  const reason = element("textarea");
  reason.id = "review-reason";
  reason.rows = 3;
  reason.minLength = 3;
  reason.maxLength = 500;
  reason.required = true;
  reason.placeholder = "Record the checks you made and explain the decision.";
  const message = element("p", "notice");
  message.id = "review-message";
  message.setAttribute("role", "status");
  message.hidden = true;
  const actions = element("div", "review-actions");
  const reject = element("button", "secondary-button danger-button", "Reject submission");
  reject.type = "submit";
  reject.dataset.approve = "false";
  const approve = element("button", "primary-button", "Approve submission ✓");
  approve.type = "submit";
  approve.dataset.approve = "true";
  actions.append(reject, approve);
  form.append(label, reason, message, actions);
  const audit = element("div", "audit-section");
  audit.append(element("h3", "", "Earlier review decisions"));
  const auditList = element("div", "audit-list");
  auditList.append(element("p", "muted", "Loading review history…"));
  audit.append(auditList);
  container.replaceChildren(meta, form, audit);
  form.addEventListener("submit", (event) => submitReview(event, record));
  try {
    const reviews = await api("/kyc/" + encodeURIComponent(accountId) + "/reviews");
    if (epoch === state.epoch && accountId === state.reviewAccountId) renderAudit(auditList, reviews);
  } catch (error) { if (epoch === state.epoch && accountId === state.reviewAccountId) auditList.replaceChildren(element("p", "document-error", error.message)); }
}

async function submitReview(event, record) {
  event.preventDefault();
  if (state.reviewBusy || !event.submitter) return;
  const reason = $("#review-reason").value.trim();
  if (reason.length < 3) { notice("#review-message", "Add a reason of at least three characters.", true); return; }
  const epoch = state.epoch;
  const accountId = record.account_id;
  const approve = event.submitter.dataset.approve === "true";
  state.reviewBusy = true;
  $$("#review-decision-form button, #review-decision-form textarea, .queue-item").forEach((node) => { node.disabled = true; });
  notice("#review-message", "Saving your decision…");
  try {
    await api("/kyc/" + encodeURIComponent(accountId) + "/verify", jsonOptions("PATCH", { approve, reason, expected_submitted_at: record.submitted_at }));
    if (epoch !== state.epoch) return;
    state.reviewAccountId = null;
    const completed = element("div", "review-complete");
    completed.append(element("span", "review-status", approve ? "Verified" : "Rejected"), element("h3", "", "Decision recorded."), element("p", "muted", reason));
    $("#review-detail").replaceChildren(completed);
    const reviews = await api("/kyc/" + encodeURIComponent(accountId) + "/reviews");
    if (epoch !== state.epoch) return;
    const audit = element("div", "audit-section");
    audit.append(element("h3", "", "Review history"));
    const auditList = element("div", "audit-list");
    renderAudit(auditList, reviews);
    audit.append(auditList);
    $("#review-detail").append(audit);
    await loadReviews();
  } catch (error) {
    if (epoch !== state.epoch) return;
    if ($("#review-message")) notice("#review-message", error.message, true);
    else globalError("Decision was recorded, but the refreshed view could not be loaded: " + error.message);
    if (error.status === 409) loadReviews().catch(() => {});
  } finally {
    if (epoch === state.epoch) {
      state.reviewBusy = false;
      $$("#review-decision-form button, #review-decision-form textarea, .queue-item").forEach((node) => { node.disabled = false; });
    }
  }
}

function openAccountDialog() {
  if (!state.user || state.accounts.length) return;
  notice("#account-form-error");
  $("#account-identity").textContent = "For " + state.user.full_name + " · " + state.user.email;
  $("#account-dialog").showModal();
}

$$("[data-auth-mode]").forEach((button) => button.addEventListener("click", () => setAuthMode(button.dataset.authMode)));
$$("[data-demo-email]").forEach((button) => button.addEventListener("click", () => {
  if (!state.demoMode) return;
  setAuthMode("login");
  $("#auth-email").value = button.dataset.demoEmail;
  $("#auth-password").value = "VaultDemo!2026";
  $("#auth-submit").focus();
}));
$("#auth-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("#auth-submit");
  if (button.disabled) return;
  $$("#auth-form input, [data-auth-mode], [data-demo-email]").forEach((node) => { node.disabled = true; });
  button.disabled = true;
  notice("#auth-error");
  const payload = { email: $("#auth-email").value.trim(), password: $("#auth-password").value };
  if (state.authMode === "register") payload.full_name = $("#auth-name").value.trim();
  try {
    const response = await api("/auth/" + state.authMode, { ...jsonOptions("POST", payload), allowUnauthorized: true });
    $("#auth-password").value = "";
    await enterWorkspace(response.user);
  } catch (error) { notice("#auth-error", error.message, true); }
  finally {
    $$("#auth-form input, [data-auth-mode], [data-demo-email]").forEach((node) => { node.disabled = false; });
    button.disabled = false;
  }
});
$("#logout").addEventListener("click", async () => {
  const button = $("#logout");
  if (button.disabled) return;
  button.disabled = true;
  try { await api("/auth/logout", { method: "POST" }); clearSession(); notice("#auth-error"); }
  catch (error) { if (state.user) globalError("Could not sign out: " + error.message); }
  finally { button.disabled = false; }
});
$$("[data-tab]").forEach((button) => button.addEventListener("click", () => showTab(button.dataset.tab)));
$("#account-select").addEventListener("change", (event) => chooseAccount(event.target.value));
$$("[data-operation]").forEach((button) => button.addEventListener("click", () => setOperation(button.dataset.operation)));
$$("#money-form input").forEach((input) => input.addEventListener("input", () => { state.moneyKey = null; notice("#money-message"); }));
$("#money-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.accountId || state.moneyBusy) return;
  const amount = $("#money-amount").value.trim();
  if (!/^\d+(?:\.\d{1,2})?$/.test(amount) || Number(amount) <= 0) { notice("#money-message", "Enter a positive amount with up to two decimal places.", true); return; }
  const epoch = state.epoch;
  const operation = state.operation;
  const payload = { amount, note: $("#money-note").value.trim() || null };
  if (operation === "transfer") {
    payload.from_account_id = state.accountId;
    payload.to_account_id = $("#recipient-id").value.trim();
    if (!payload.to_account_id) { notice("#money-message", "Enter the recipient’s account ID.", true); return; }
    if (payload.to_account_id === state.accountId) { notice("#money-message", "Choose a different account for the transfer.", true); return; }
  }
  if (!state.moneyKey) state.moneyKey = crypto.randomUUID();
  const options = jsonOptions("POST", payload);
  options.headers["Idempotency-Key"] = state.moneyKey;
  setMoneyBusy(true);
  notice("#money-message", "Recording your " + (operation === "withdraw" ? "withdrawal" : operation) + "…");
  try {
    await api(operation === "transfer" ? "/transfer" : accountPath("/" + operation), options);
    if (epoch !== state.epoch) return;
    state.moneyKey = null;
    $("#money-form").reset();
    state.historyOffset = 0;
    notice("#money-message", ({ deposit: "Deposit recorded.", withdraw: "Withdrawal recorded.", transfer: "Transfer sent." })[operation] + " PKR " + money(amount) + " in synthetic funds.");
    const results = await Promise.allSettled([refreshAccount(), loadHistory()]);
    if (epoch === state.epoch && results.some((result) => result.status === "rejected")) globalError("Your transaction was recorded. Refresh to load the updated account.");
  } catch (error) {
    if (epoch !== state.epoch) return;
    notice("#money-message", error.message + (error.network ? " Retry the unchanged form to safely check this same transaction." : ""), true);
  } finally { if (epoch === state.epoch) setMoneyBusy(false); }
});
$("#refresh-banking").addEventListener("click", async () => {
  const button = $("#refresh-banking");
  if (!state.accountId || button.disabled) return;
  const epoch = state.epoch;
  button.disabled = true;
  globalError();
  try { await Promise.all([refreshAccount(), loadHistory()]); }
  catch (error) { if (epoch === state.epoch) globalError(error.message); }
  finally { if (epoch === state.epoch) button.disabled = false; }
});
$("#copy-account-id").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText(state.accountId || ""); $("#copy-message").textContent = "Account ID copied."; }
  catch { $("#account-id-display").select(); $("#copy-message").textContent = "Select and copy the account ID above."; }
});
$("#history-filter").addEventListener("change", () => { state.historyOffset = 0; loadHistory(); });
$("#history-prev").addEventListener("click", () => { if (state.historyOffset > 0) { state.historyOffset -= 20; loadHistory(); } });
$("#history-next").addEventListener("click", () => { if (state.historyHasNext) { state.historyOffset += 20; loadHistory(); } });
$("#new-account").addEventListener("click", openAccountDialog);
$("#open-first-account").addEventListener("click", openAccountDialog);
$("#close-account-dialog").addEventListener("click", () => $("#account-dialog").close());
$("#account-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("#create-account-submit");
  if (!state.user || state.accounts.length || button.disabled) return;
  const epoch = state.epoch;
  button.disabled = true;
  notice("#account-form-error");
  try {
    const account = await api("/accounts/", jsonOptions("POST", { owner_name: state.user.full_name, email: state.user.email, opening_deposit: $("#opening-deposit").value.trim() }));
    if (epoch !== state.epoch) return;
    $("#account-dialog").close();
    $("#account-form").reset();
    await loadAccounts(account.id);
    globalError();
  } catch (error) {
    if (epoch !== state.epoch) return;
    if (error.status === 409) {
      try {
        await loadAccounts();
        if (state.accounts.length) { $("#account-dialog").close(); globalError(); return; }
      } catch { /* Keep the original account-creation error visible. */ }
    }
    if (state.user) notice("#account-form-error", error.message, true);
  }
  finally { button.disabled = false; }
});
$("#chat-form").addEventListener("submit", (event) => { event.preventDefault(); sendChat($("#chat-input").value); });
$("#chat-input").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); sendChat(event.target.value); }
});
$("#chat-input").addEventListener("input", (event) => { event.target.style.height = "auto"; event.target.style.height = Math.min(event.target.scrollHeight, 110) + "px"; });
$$("[data-question]").forEach((button) => button.addEventListener("click", () => sendChat(button.dataset.question)));
$("#document-file").addEventListener("change", (event) => { $("#file-label").textContent = event.target.files[0]?.name || "Choose a sample document"; });
$("#document-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.accountId) { $("#document-progress").textContent = "Open an account before analyzing a document."; return; }
  const button = $("#analyze-document");
  if (button.disabled) return;
  const file = $("#document-file").files[0];
  if (!file) return;
  if (file.size > 5 * 1024 * 1024) { $("#document-progress").textContent = "Choose a document smaller than 5 MB."; return; }
  const epoch = state.epoch;
  const request = ++state.documentRequest;
  const form = new FormData();
  form.append("file", file);
  if ($("#expected-id").value.trim()) form.append("expected_id_number", $("#expected-id").value.trim());
  button.disabled = true;
  $("#document-progress").textContent = "Extracting fields and checking details…";
  try {
    const result = await api("/kyc/" + encodeURIComponent(state.accountId) + "/analyze-document", { method: "POST", body: form });
    if (epoch !== state.epoch || request !== state.documentRequest) return;
    renderDocument(result);
    $("#document-progress").textContent = "Analysis complete. Check the fields below before submitting.";
  } catch (error) {
    if (epoch !== state.epoch || request !== state.documentRequest) return;
    $("#document-progress").textContent = "Analysis could not be completed.";
    $("#document-output").replaceChildren(element("p", "document-error", error.message));
  } finally { if (epoch === state.epoch && request === state.documentRequest) button.disabled = false; }
});
$("#kyc-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("#submit-kyc");
  if (!state.accountId || button.disabled) return;
  const epoch = state.epoch;
  button.disabled = true;
  notice("#kyc-message", "Submitting your identity fields…");
  try {
    const kyc = await api("/kyc/" + encodeURIComponent(state.accountId), jsonOptions("POST", { full_name: $("#kyc-name").value.trim(), id_number: $("#kyc-id").value.trim(), document_type: $("#kyc-type").value.trim() }));
    if (epoch !== state.epoch) return;
    applyKycStatus(kyc);
    await loadKyc();
  } catch (error) { if (epoch === state.epoch) notice("#kyc-message", error.message, true); }
  finally { if (epoch === state.epoch) button.disabled = state.kyc?.status === "pending" || state.kyc?.status === "verified"; }
});
$("#refresh-reviews").addEventListener("click", () => {
  if (state.reviewBusy) return;
  globalError();
  loadReviews().catch((error) => { if (state.user) globalError(error.message); });
});

async function initialize() {
  $("#auth-submit").disabled = true;
  setAuthMode("login");
  resetDocument();
  const results = await Promise.allSettled([
    api("/auth/config", { allowUnauthorized: true }),
    api("/auth/me", { allowUnauthorized: true }),
    api("/assistant/status", { allowUnauthorized: true })
  ]);
  const [config, session, assistant] = results;
  if (config.status === "fulfilled") {
    state.demoMode = config.value.demo_mode === true;
    $("#demo-logins").hidden = !state.demoMode;
  }
  if (assistant.status === "fulfilled") {
    showMode(assistant.value.mode);
    if (assistant.value.mode !== "demo") $("#assistant-mode").textContent = (assistant.value.mode === "gemini" ? "Gemini" : "OpenAI") + (assistant.value.provider_configured ? " configured" : " key needed");
  } else $("#assistant-mode").textContent = "Connection unavailable";
  if (session.status === "fulfilled") await enterWorkspace(session.value);
  else if (session.reason.status !== 401) notice("#auth-error", "Could not connect to your workspace: " + session.reason.message, true);
  $("#auth-submit").disabled = false;
}
initialize();
