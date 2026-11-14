const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

async function readJsonResponse(response) {
  try {
    return await response.json();
  } catch {
    const status = Number.isInteger(response.status) ? ` (HTTP ${response.status})` : "";
    const message = response.status >= 500
      ? `The server encountered an error${status}. Check the Vercel Function logs.`
      : `The server returned an invalid response${status}.`;
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }
}

function updatePortal() {
  const track = document.querySelector(".portal-track");
  const stage = document.querySelector(".portal-stage");
  if (!track || !stage) return;

  const distance = track.offsetHeight - window.innerHeight;
  const progress = distance > 0
    ? clamp(-track.getBoundingClientRect().top / distance, 0, 1)
    : 0;
  const eased = progress * progress * (3 - 2 * progress);

  stage.style.setProperty("--progress", progress.toFixed(4));
  stage.style.setProperty("--panel-left", `${-eased * 205}%`);
  stage.style.setProperty("--panel-right", `${eased * 205}%`);
  stage.style.setProperty("--photo-scale", (1.12 - eased * 0.12).toFixed(4));
  stage.style.setProperty("--wash-opacity", (eased * 0.2).toFixed(4));
  stage.style.setProperty("--dot-opacity", clamp(progress * 5, 0, 1).toFixed(4));
  stage.style.setProperty("--dot-travel-x", `${eased * 38}vw`);
  stage.style.setProperty("--dot-travel-y", `${eased * 34}vh`);
  stage.style.setProperty("--title-scale", (1 + eased * 0.12).toFixed(4));
  stage.style.setProperty("--title-tracking", `${-0.025 - eased * 0.012}em`);
  stage.style.setProperty("--title-left", `${-eased * 34}vw`);
  stage.style.setProperty("--title-right", `${eased * 34}vw`);

  const orbit = document.querySelector(".statement-orbit");
  if (orbit) {
    const rect = orbit.parentElement.getBoundingClientRect();
    const viewportProgress = clamp((window.innerHeight - rect.top) / (window.innerHeight + rect.height), 0, 1);
    orbit.style.setProperty("--orbit-y", `${(viewportProgress - 0.5) * -65}px`);
    orbit.style.setProperty("--orbit-rotation", `${-12 + viewportProgress * 24}deg`);
  }
}

let scrollFrame = 0;
function requestMotionUpdate() {
  if (scrollFrame) return;
  scrollFrame = window.requestAnimationFrame(() => {
    updatePortal();
    scrollFrame = 0;
  });
}

updatePortal();
window.addEventListener("scroll", requestMotionUpdate, { passive: true });
window.addEventListener("resize", requestMotionUpdate);

if (!reduceMotion.matches && "IntersectionObserver" in window) {
  document.documentElement.classList.add("motion-ready");
  const observer = new IntersectionObserver((entries, currentObserver) => {
    for (const entry of entries) {
      if (entry.isIntersecting) {
        entry.target.classList.add("is-visible");
        currentObserver.unobserve(entry.target);
      }
    }
  }, { threshold: 0.12 });
  document.querySelectorAll(".reveal").forEach((element) => observer.observe(element));
}

const deck = document.querySelector(".project-deck");
const previousButton = document.querySelector(".deck-previous");
const nextButton = document.querySelector(".deck-next");
const dots = Array.from(document.querySelectorAll(".progress-dot"));
const position = document.querySelector(".deck-position");
const cards = deck ? Array.from(deck.querySelectorAll(".project-card")) : [];
let pointerStart = null;
let currentIndex = 0;

function renderDeck() {
  if (!deck || cards.length === 0) return;
  cards.forEach((card, index) => {
    const depth = (index - currentIndex + cards.length) % cards.length;
    card.style.zIndex = String(cards.length - depth);
    card.style.setProperty("--stack-x", `${depth * 7}px`);
    card.style.setProperty("--stack-y", `${depth * -6}px`);
    card.style.setProperty("--stack-scale", String(1 - depth * 0.035));
    card.style.setProperty("--stack-rotation", `${depth % 2 === 0 ? 1 : -1 * (depth + 1)}deg`);
    card.setAttribute("aria-hidden", depth === 0 ? "false" : "true");
  });
  dots.forEach((dot, index) => {
    dot.classList.toggle("is-active", index === currentIndex);
    if (index === currentIndex) dot.setAttribute("aria-current", "true");
    else dot.removeAttribute("aria-current");
  });
  if (position) {
    position.textContent = `${String(currentIndex + 1).padStart(2, "0")} / ${String(cards.length).padStart(2, "0")}`;
  }
}

function moveDeck(direction) {
  if (!deck || cards.length < 2) return;
  const topCard = cards[currentIndex];
  if (topCard.classList.contains("is-throwing")) return;
  topCard.classList.add("is-throwing");
  topCard.style.transform = `translate(${direction * 110}%, -8%) rotate(${direction * 13}deg)`;
  topCard.style.opacity = "0";
  window.setTimeout(() => {
    topCard.classList.remove("is-throwing");
    topCard.style.opacity = "";
    topCard.style.transform = "";
    currentIndex = (currentIndex + 1) % cards.length;
    renderDeck();
  }, 430);
}

function restoreCard(card) {
  card.classList.remove("is-dragging");
  card.style.transform = "";
}

if (deck) {
  renderDeck();
  deck.addEventListener("pointerdown", (event) => {
    const card = event.target.closest(".project-card");
    if (!card || card !== cards[currentIndex] || !event.isPrimary || (event.pointerType === "mouse" && event.button !== 0)) return;
    pointerStart = { x: event.clientX, y: event.clientY, id: event.pointerId, card };
    card.classList.add("is-dragging");
    card.setPointerCapture(event.pointerId);
  });

  deck.addEventListener("pointermove", (event) => {
    if (!pointerStart || event.pointerId !== pointerStart.id) return;
    const distance = event.clientX - pointerStart.x;
    pointerStart.card.style.transform = `translate(${distance}px, -2px) rotate(${distance / 18}deg) scale(1.025)`;
  });

  function finishPointer(event, shouldThrow) {
    if (!pointerStart || event.pointerId !== pointerStart.id) return;
    const { card, x } = pointerStart;
    const distance = event.clientX - x;
    pointerStart = null;
    card.classList.remove("is-dragging");
    if (shouldThrow && Math.abs(distance) > deck.clientWidth * 0.1) {
      moveDeck(distance > 0 ? 1 : -1);
    } else {
      restoreCard(card);
    }
  }

  deck.addEventListener("pointerup", (event) => finishPointer(event, true));
  deck.addEventListener("pointercancel", (event) => finishPointer(event, false));
  deck.addEventListener("lostpointercapture", (event) => finishPointer(event, false));
  deck.addEventListener("keydown", (event) => {
    if (event.key === "ArrowRight") {
      event.preventDefault();
      moveDeck(1);
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      moveDeck(-1);
    }
  });
}

previousButton?.addEventListener("click", () => moveDeck(-1));
nextButton?.addEventListener("click", () => moveDeck(1));

const year = document.querySelector("#year");
if (year) year.textContent = String(new Date().getFullYear());

const newChatButton = document.querySelector("#new-chat");
const chatForm = document.querySelector("#chat-form");
const chatInput = document.querySelector("#chat-input");
const chatMessages = document.querySelector("#chat-messages");
const chatStatus = document.querySelector("#chat-status");
const welcomeMessage = "Hi! I’m Subham’s AI digital twin. What would you like to know about my work and experience?";
const chatHistory = [];
let conversationSessionId = "";
let serverInstanceId = "";
let sessionReady = false;
const visitorIntroduction = {
  started: false,
  stage: "idle",
  name: "",
  company: "",
};

function isGreetingMessage(message) {
  return /^(hi(?: there)?|hello|hey|hiya|howdy|good morning|good afternoon|good evening)[!.?,\s]*$/i.test(message.trim());
}

function isSkippedIntroduction(message) {
  return /^(skip|pass|no|no thanks|prefer not to say|i prefer not to say|rather not|rather not share|i'd rather not|i would rather not|not comfortable|i'm not comfortable|i am not comfortable|don't want to say|do not want to say)[.!,\s]*$/i.test(message.trim());
}

function isLikelyQuestion(message) {
  return message.includes("?") || /^(what|how|why|when|where|who|can|could|would|do|does|did|is|are|tell|explain|show|describe|share|help|i want to know|i'd like to know|i would like to know|please tell)\b/i.test(message.trim());
}

function addSessionOnlyTurn(role, content) {
  chatHistory.push({ role, content, sessionOnly: true });
  if (chatHistory.length > 12) chatHistory.splice(0, chatHistory.length - 12);
}

function replyDuringIntroduction(message) {
  const stage = visitorIntroduction.stage;
  const skipped = isSkippedIntroduction(message);
  if (!skipped && isLikelyQuestion(message)) {
    visitorIntroduction.stage = "complete";
    return null;
  }

  if (stage === "name") {
    if (!skipped && message.trim().length > 80) {
      addSessionOnlyTurn("user", message);
      return "Could you share a shorter name, or type “skip” if you prefer not to say?";
    }
    if (!skipped) {
      visitorIntroduction.name = message.trim();
    }
    addSessionOnlyTurn("user", message);
    visitorIntroduction.stage = "company";
    return skipped
      ? "No problem. If you’re comfortable sharing, what company are you with? You can skip this too."
      : `Nice to meet you, ${visitorIntroduction.name}. If you’re comfortable sharing, what company are you with? You can skip this too.`;
  }

  if (stage === "company") {
    if (!skipped && message.trim().length > 120) {
      addSessionOnlyTurn("user", message);
      return "Could you share a shorter company name, or type “skip” if you prefer not to say?";
    }
    if (!skipped) {
      visitorIntroduction.company = message.trim();
    }
    addSessionOnlyTurn("user", message);
    visitorIntroduction.stage = "complete";
    return skipped
      ? "No problem. How can I help you today?"
      : `Thanks${visitorIntroduction.name ? `, ${visitorIntroduction.name}` : ""}. How can I help you today?`;
  }

  return null;
}

function addSessionOnlyReply(reply) {
  addChatMessage(reply, "assistant");
  addSessionOnlyTurn("assistant", reply);
}

function setChatInputEnabled(enabled) {
  if (chatInput) chatInput.disabled = !enabled;
  chatForm?.querySelector("button[type='submit']")?.toggleAttribute("disabled", !enabled);
}

function addChatMessage(text, sender) {
  if (!chatMessages) return null;
  const message = document.createElement("p");
  message.className = `chat-message chat-message-${sender}`;
  message.textContent = text;
  chatMessages.append(message);
  chatMessages.scrollTop = chatMessages.scrollHeight;
  return message;
}

function resetConversationView() {
  chatHistory.length = 0;
  visitorIntroduction.started = false;
  visitorIntroduction.stage = "idle";
  visitorIntroduction.name = "";
  visitorIntroduction.company = "";
  if (chatMessages) {
    chatMessages.replaceChildren();
    addChatMessage(welcomeMessage, "assistant");
  }
}

async function initializeConversationSession(reset = false) {
  setChatInputEnabled(false);
  const response = await fetch("/api/conversations/session", { cache: "no-store" });
  const result = await readJsonResponse(response);
  if (!response.ok || typeof result.session_id !== "string" || typeof result.server_instance_id !== "string") {
    throw new Error(result.error || "A new conversation session could not be started.");
  }
  conversationSessionId = result.session_id;
  serverInstanceId = result.server_instance_id;
  sessionReady = true;
  if (reset) resetConversationView();
  setChatInputEnabled(true);
  if (chatStatus) chatStatus.textContent = "";
}

async function archiveConversationMessages(messages) {
  if (!sessionReady) throw new Error("The conversation session is still starting. Please try again.");
  const response = await fetch(
    `/api/conversations/${encodeURIComponent(conversationSessionId)}/messages`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ server_instance_id: serverInstanceId, messages }),
    },
  );
  const result = await readJsonResponse(response);
  if (!response.ok) {
    const error = new Error(result.error || "This conversation could not be archived.");
    error.status = response.status;
    throw error;
  }
}

async function startNewConversation(message = "A fresh conversation is ready.") {
  sessionReady = false;
  setChatInputEnabled(false);
  if (newChatButton) newChatButton.disabled = true;
  if (chatStatus) chatStatus.textContent = "Starting a fresh session…";
  try {
    await initializeConversationSession(true);
    if (chatStatus) chatStatus.textContent = message;
  } catch (error) {
    if (chatStatus) chatStatus.textContent = error.message || "A new conversation could not be started.";
  } finally {
    if (sessionReady) setChatInputEnabled(true);
    if (newChatButton) newChatButton.disabled = false;
  }
}

async function checkServerInstance() {
  if (!sessionReady) return;
  try {
    const response = await fetch("/api/server-instance", { cache: "no-store" });
    const result = await readJsonResponse(response);
    if (response.ok && result.server_instance_id !== serverInstanceId) {
      await startNewConversation("The server restarted. This is a fresh conversation.");
    }
  } catch (error) {
    console.warn("Could not check the chat server session:", error);
  }
}

if (chatInput && chatForm) {
  setChatInputEnabled(false);
  initializeConversationSession().catch((error) => {
    if (chatStatus) chatStatus.textContent = error.message || "A new conversation could not be started.";
  });
  window.setInterval(checkServerInstance, 30_000);
}

newChatButton?.addEventListener("click", () => startNewConversation());

/*
 * Chat history is intentionally kept only in this page's memory. Each page
 * load creates a new archive ID; previous sessions are visible only to admin.
 */
if (chatHistory.length > 0 && chatMessages) {
  chatMessages.replaceChildren();
  chatHistory.forEach((message) => addChatMessage(message.content, message.role));
}

document.querySelectorAll(".chat-suggestion").forEach((button) => {
  button.addEventListener("click", () => {
    if (!chatInput) return;
    chatInput.value = button.textContent.trim();
    chatInput.focus();
    chatForm?.requestSubmit();
  });
});

chatForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!chatInput || !chatStatus || !sessionReady) return;
  const message = chatInput.value.trim();
  if (!message) return;
  const submittedSessionId = conversationSessionId;

  chatInput.value = "";
  setChatInputEnabled(false);
  if (newChatButton) newChatButton.disabled = true;
  chatStatus.textContent = "Thinking…";
  addChatMessage(message, "user");

  if (visitorIntroduction.stage === "name" || visitorIntroduction.stage === "company") {
    const introductionReply = replyDuringIntroduction(message);
    if (introductionReply) {
      addSessionOnlyReply(introductionReply);
      try {
        await archiveConversationMessages([
          { role: "user", content: message },
          { role: "assistant", content: introductionReply },
        ]);
        chatStatus.textContent = "";
      } catch (error) {
        if (error.status === 409) {
          await startNewConversation("The server restarted. Please send your message again.");
          return;
        }
        chatStatus.textContent = error.message || "The conversation could not be archived.";
      }
      setChatInputEnabled(sessionReady);
      if (newChatButton) newChatButton.disabled = false;
      chatInput.focus();
      return;
    }
  }

  if (!visitorIntroduction.started && isGreetingMessage(message)) {
    visitorIntroduction.started = true;
    visitorIntroduction.stage = "name";
    addSessionOnlyTurn("user", message);
    const introductionReply = "Hi, I’m Subham’s digital twin. How can I help you today? May I know your name? You can skip this if you prefer.";
    addSessionOnlyReply(introductionReply);
    try {
      await archiveConversationMessages([
        { role: "user", content: message },
        { role: "assistant", content: introductionReply },
      ]);
      chatStatus.textContent = "";
    } catch (error) {
      if (error.status === 409) {
        await startNewConversation("The server restarted. Please send your message again.");
        return;
      }
      chatStatus.textContent = error.message || "The conversation could not be archived.";
    }
    setChatInputEnabled(sessionReady);
    if (newChatButton) newChatButton.disabled = false;
    chatInput.focus();
    return;
  }

  const pendingMessage = addChatMessage("One moment…", "assistant");

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message,
        session_id: conversationSessionId,
        server_instance_id: serverInstanceId,
        history: chatHistory
          .filter((entry) => !entry.sessionOnly)
          .map(({ role, content }) => ({ role, content })),
      }),
    });
    const result = await readJsonResponse(response);
    if (submittedSessionId !== conversationSessionId) return;
    if (!response.ok) {
      const error = new Error(result.error || "The chat is temporarily unavailable.");
      error.status = response.status;
      throw error;
    }
    if (pendingMessage) pendingMessage.textContent = result.reply;
    chatHistory.push(
      { role: "user", content: message },
      { role: "assistant", content: result.reply },
    );
    if (chatHistory.length > 12) chatHistory.splice(0, chatHistory.length - 12);
    chatStatus.textContent = "";
  } catch (error) {
    if (error.status === 409) {
      await startNewConversation("The server restarted. Please send your message again.");
      return;
    }
    pendingMessage?.remove();
    addChatMessage(error.message || "I couldn’t reach the chat service. Please try again.", "assistant");
    chatStatus.textContent = "Message wasn’t sent.";
  } finally {
    setChatInputEnabled(sessionReady);
    if (newChatButton) newChatButton.disabled = false;
    chatInput.focus();
  }
});

const githubStatus = document.querySelector("#github-status");
const githubProjectList = document.querySelector("#github-project-list");

async function loadGithubProjects() {
  if (!githubStatus || !githubProjectList) return;
  try {
    const response = await fetch("/api/projects");
    const result = await readJsonResponse(response);
    if (!response.ok) throw new Error(result.error || "GitHub projects could not be loaded.");
    if (!result.configured) {
      githubStatus.textContent = "GitHub projects are not configured yet.";
      return;
    }
    if (!Array.isArray(result.projects) || result.projects.length === 0) {
      githubStatus.textContent = "No public repositories with a README were found.";
      return;
    }

    githubStatus.textContent = "";
    result.projects.forEach((project) => {
      if (typeof project.name !== "string" || typeof project.url !== "string" || typeof project.readme !== "string") return;
      const article = document.createElement("article");
      article.className = "github-project";
      const heading = document.createElement("h3");
      const link = document.createElement("a");
      link.href = project.url;
      link.target = "_blank";
      link.rel = "noreferrer";
      link.textContent = project.name;
      heading.append(link);
      const readme = document.createElement("pre");
      readme.className = "github-readme";
      if (project.readme) {
        readme.textContent = project.readme;
        article.append(heading, readme);
      } else {
        const missingReadme = document.createElement("p");
        missingReadme.className = "github-readme-unavailable";
        missingReadme.textContent = "No root README is available for this repository.";
        article.append(heading, missingReadme);
      }
      githubProjectList.append(article);
    });
  } catch (error) {
    githubStatus.textContent = error.message || "GitHub projects could not be loaded.";
  }
}

loadGithubProjects();

const leadForm = document.querySelector("#lead-form");
const leadStatus = document.querySelector("#lead-status");

leadForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!leadStatus || !(leadForm instanceof HTMLFormElement)) return;
  const submitButton = leadForm.querySelector("button[type='submit']");
  const formData = new FormData(leadForm);
  const payload = {
    name: formData.get("name"),
    email: formData.get("email"),
    company: formData.get("company"),
    message: formData.get("message"),
    consent: formData.get("consent") === "on",
    website: formData.get("website"),
  };
  if (submitButton) submitButton.disabled = true;
  leadStatus.textContent = "Sending…";

  try {
    const response = await fetch("/api/leads", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const result = await readJsonResponse(response);
    if (!response.ok) throw new Error(result.error || "Your details could not be sent.");
    leadForm.reset();
    leadStatus.textContent = result.message;
  } catch (error) {
    leadStatus.textContent = error.message || "Your details could not be sent. Please email Subham directly.";
  } finally {
    if (submitButton) submitButton.disabled = false;
  }
});
