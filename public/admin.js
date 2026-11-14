const adminLogin = document.querySelector("#admin-login");
const adminPassword = document.querySelector("#admin-password");
const adminLogout = document.querySelector("#admin-logout");
const adminStatus = document.querySelector("#admin-status");
const adminSubmissions = document.querySelector("#admin-submissions");
const adminConversations = document.querySelector("#admin-conversations");

function showSignedOutState(message) {
  if (adminLogin) adminLogin.hidden = false;
  if (adminLogout) adminLogout.hidden = true;
  if (adminPassword) adminPassword.value = "";
  if (adminSubmissions) adminSubmissions.replaceChildren();
  if (adminConversations) adminConversations.replaceChildren();
  if (adminStatus) adminStatus.textContent = message;
}

function renderLead(lead) {
  const article = document.createElement("article");
  article.className = "admin-lead";
  const heading = document.createElement("h2");
  heading.textContent = lead.name;
  const date = document.createElement("time");
  date.dateTime = lead.created_at;
  date.textContent = new Date(lead.created_at).toLocaleString();
  const email = document.createElement("p");
  email.textContent = lead.email;
  const company = document.createElement("p");
  company.textContent = lead.company || "No company provided";
  const message = document.createElement("p");
  message.className = "admin-lead-message";
  message.textContent = lead.message || "No message provided";
  const remove = document.createElement("button");
  remove.className = "button button-outline";
  remove.type = "button";
  remove.textContent = "Delete";
  remove.addEventListener("click", () => deleteLead(lead.id, article));
  article.append(heading, date, email, company, message, remove);
  return article;
}

async function loadLeads() {
  if (!adminStatus || !adminSubmissions) return;
  const response = await fetch("/api/admin/leads");
  const result = await response.json();
  if (response.status === 401) {
    showSignedOutState("Sign in to view submissions.");
    return false;
  }
  if (!response.ok) throw new Error(result.error || "Submissions could not be loaded.");
  adminSubmissions.replaceChildren();
  if (!Array.isArray(result.leads) || result.leads.length === 0) {
    adminStatus.textContent = "No consented submissions are currently stored.";
  } else {
    adminStatus.textContent = `${result.leads.length} submission${result.leads.length === 1 ? "" : "s"} · automatically deleted after 90 days.`;
    result.leads.forEach((lead) => adminSubmissions.append(renderLead(lead)));
  }
  return true;
}

function renderConversation(conversation) {
  const article = document.createElement("article");
  article.className = "admin-conversation";
  const heading = document.createElement("h3");
  heading.textContent = `Session ${conversation.session_id}`;
  const date = document.createElement("time");
  date.dateTime = conversation.updated_at;
  date.textContent = `Last activity ${new Date(conversation.updated_at).toLocaleString()}`;
  const transcript = document.createElement("div");
  transcript.className = "admin-conversation-transcript";
  if (Array.isArray(conversation.messages)) {
    conversation.messages.forEach((turn) => {
      if (!turn || !["user", "assistant"].includes(turn.role) || typeof turn.content !== "string") return;
      const message = document.createElement("p");
      const speaker = document.createElement("strong");
      speaker.textContent = turn.role === "user" ? "Visitor: " : "Digital twin: ";
      message.append(speaker, document.createTextNode(turn.content));
      transcript.append(message);
    });
  }
  const remove = document.createElement("button");
  remove.className = "button button-outline";
  remove.type = "button";
  remove.textContent = "Delete archive";
  remove.addEventListener("click", () => deleteConversation(conversation.session_id, article));
  article.append(heading, date, transcript, remove);
  return article;
}

async function loadConversations() {
  if (!adminStatus || !adminConversations) return false;
  const response = await fetch("/api/admin/conversations");
  const result = await response.json();
  if (response.status === 401) {
    showSignedOutState("Sign in to view archived conversations.");
    return false;
  }
  if (!response.ok) throw new Error(result.error || "Archived conversations could not be loaded.");
  adminConversations.replaceChildren();
  if (!Array.isArray(result.conversations) || result.conversations.length === 0) {
    const empty = document.createElement("p");
    empty.className = "admin-empty";
    empty.textContent = "No conversations have been archived yet.";
    adminConversations.append(empty);
    return true;
  }
  result.conversations.forEach((conversation) => adminConversations.append(renderConversation(conversation)));
  return true;
}

async function loadAdminData() {
  const authenticated = await loadLeads();
  if (!authenticated) return false;
  return loadConversations();
}

async function deleteLead(id, article) {
  if (!adminStatus || !window.confirm("Permanently delete this submission?")) return;
  adminStatus.textContent = "Deleting…";
  try {
    const response = await fetch(`/api/admin/leads/${encodeURIComponent(id)}`, { method: "DELETE" });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Submission could not be deleted.");
    article.remove();
    adminStatus.textContent = "Submission deleted.";
  } catch (error) {
    adminStatus.textContent = error.message || "Submission could not be deleted.";
  }
}

async function deleteConversation(sessionId, article) {
  if (!adminStatus || !window.confirm("Permanently delete this archived conversation?")) return;
  adminStatus.textContent = "Deleting archived conversation…";
  try {
    const response = await fetch(`/api/admin/conversations/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Conversation could not be deleted.");
    article.remove();
    adminStatus.textContent = "Archived conversation deleted.";
  } catch (error) {
    adminStatus.textContent = error.message || "Conversation could not be deleted.";
  }
}

adminLogin?.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!adminStatus || !adminPassword) return;
  adminStatus.textContent = "Signing in…";
  try {
    const response = await fetch("/api/admin/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: adminPassword.value }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Sign in failed.");
    adminPassword.value = "";
    adminLogin.hidden = true;
    if (adminLogout) adminLogout.hidden = false;
    await loadAdminData();
  } catch (error) {
    showSignedOutState(error.message || "Sign in failed.");
  }
});

adminLogout?.addEventListener("click", async () => {
  try {
    const response = await fetch("/api/admin/logout", { method: "POST" });
    if (!response.ok) throw new Error("Could not sign out. Please try again.");
    showSignedOutState("Signed out.");
  } catch (error) {
    if (adminStatus) adminStatus.textContent = error.message || "Could not sign out.";
  }
});

loadAdminData().then((authenticated) => {
  if (authenticated && adminLogin && adminLogout) {
    adminLogin.hidden = true;
    adminLogout.hidden = false;
  }
}).catch((error) => {
  if (adminStatus) adminStatus.textContent = error.message || "Submissions could not be loaded.";
});
