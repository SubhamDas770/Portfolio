const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const script = readFileSync(join(__dirname, "..", "public", "script.js"), "utf8");
let nextSession = 0;

class FakeElement {
  constructor() {
    this.children = [];
    this.listeners = {};
    this.attributes = {};
    this.classList = {
      add() {},
      remove() {},
      toggle() {},
      contains() { return false; },
    };
    this.style = { setProperty() {} };
    this.hidden = false;
    this.disabled = false;
    this.value = "";
    this.textContent = "";
    this.scrollHeight = 0;
  }

  addEventListener(name, callback) {
    this.listeners[name] = callback;
  }

  append(...elements) {
    this.children.push(...elements);
  }

  replaceChildren(...elements) {
    this.children = [...elements];
  }

  querySelector(selector) {
    return selector === "button[type='submit']" ? this.submitButton : null;
  }

  setAttribute(name, value) {
    this.attributes[name] = value;
  }

  toggleAttribute(name, force) {
    if (force) this.attributes[name] = "";
    else delete this.attributes[name];
  }

  removeAttribute(name) {
    delete this.attributes[name];
  }

  remove() {
    this.removed = true;
  }

  focus() {}
}

async function createChat({ sessionError = false, chatError = false } = {}) {
  const form = new FakeElement();
  const submitButton = new FakeElement();
  form.submitButton = submitButton;
  const input = new FakeElement();
  const messages = new FakeElement();
  const status = new FakeElement();
  const newChatButton = new FakeElement();
  const chatPanel = new FakeElement();
  const elements = new Map([
    ["#chat-form", form],
    ["#chat-input", input],
    ["#chat-messages", messages],
    ["#chat-status", status],
    ["#new-chat", newChatButton],
    ["#digital-twin-chat", chatPanel],
  ]);
  const requests = [];
  const archivedMessages = [];
  let serverInstanceId = "server-1";
  let pollServerInstance;
  const document = {
    documentElement: new FakeElement(),
    querySelector(selector) {
      return elements.get(selector) || null;
    },
    querySelectorAll() {
      return [];
    },
    createElement() {
      return new FakeElement();
    },
  };
  const window = {
    innerHeight: 800,
    matchMedia() {
      return { matches: true };
    },
    addEventListener() {},
    requestAnimationFrame(callback) {
      callback();
    },
    setTimeout(callback) {
      callback();
    },
    setInterval(callback) {
      pollServerInstance = callback;
      return 1;
    },
  };
  const context = vm.createContext({
    document,
    window,
    console,
    fetch: async (url, options = {}) => {
      requests.push({ url, options });
      if (url === "/api/conversations/session") {
        nextSession += 1;
        return {
          ok: !sessionError,
          status: sessionError ? 500 : 200,
          async json() {
            if (sessionError) throw new SyntaxError("Unexpected token");
            return {
              session_id: `session-${nextSession}`,
              server_instance_id: serverInstanceId,
            };
          },
        };
      }
      if (url === "/api/server-instance") {
        return {
          ok: true,
          async json() {
            return { server_instance_id: serverInstanceId };
          },
        };
      }
      if (url.endsWith("/messages")) {
        archivedMessages.push(JSON.parse(options.body));
        return { ok: true, async json() { return { saved: true }; } };
      }
      if (url === "/api/chat") {
        return {
          ok: !chatError,
          status: chatError ? 500 : 200,
          async json() {
            if (chatError) throw new SyntaxError("Unexpected token");
            return { reply: "Here is an answer." };
          },
        };
      }
      throw new Error(`Unexpected request: ${url}`);
    },
  });
  vm.runInContext(script, context);
  await new Promise(setImmediate);

  return {
    archivedMessages,
    form,
    input,
    messages,
    newChatButton,
    requests,
    status,
    state(expression) {
      return vm.runInContext(expression, context);
    },
    setServerInstance(value) {
      serverInstanceId = value;
    },
    async checkServerInstance() {
      await pollServerInstance();
      await new Promise(setImmediate);
    },
    async submit(message) {
      input.value = message;
      await form.listeners.submit({ preventDefault() {} });
    },
    async startNewConversation() {
      await newChatButton.listeners.click();
      await new Promise(setImmediate);
    },
    sessionId() {
      return this.state("conversationSessionId");
    },
    visibleReplies() {
      return messages.children
        .filter((element) => element.className === "chat-message chat-message-assistant")
        .map((element) => element.textContent);
    },
  };
}

test("non-JSON session errors show the Vercel status instead of a JSON parse exception", async () => {
  const chat = await createChat({ sessionError: true });

  assert.match(chat.status.textContent, /server encountered an error \(HTTP 500\)/i);
  assert.match(chat.status.textContent, /Vercel Function logs/);
});

test("non-JSON chat errors show the Vercel status instead of a JSON parse exception", async () => {
  const failedChat = await createChat({ chatError: true });
  await failedChat.submit("Tell me about your RAG work.");
  assert.match(failedChat.visibleReplies().at(-1), /server encountered an error \(HTTP 500\)/i);
});

test("each page load gets a fresh session and archived intro details stay out of chat context", async () => {
  const firstVisit = await createChat();
  const firstSessionId = firstVisit.sessionId();
  const secondVisit = await createChat();

  assert.notEqual(firstSessionId, secondVisit.sessionId());
  assert.equal(firstVisit.state("chatHistory.length"), 0);

  await firstVisit.submit("Hi");
  assert.match(firstVisit.visibleReplies().at(-1), /Subham’s digital twin/);
  assert.match(firstVisit.visibleReplies().at(-1), /May I know your name/);
  await firstVisit.submit("Avery Example");
  assert.match(firstVisit.visibleReplies().at(-1), /what company are you with/i);
  await firstVisit.submit("Acme Research");
  assert.match(firstVisit.visibleReplies().at(-1), /How can I help you today/);

  assert.equal(firstVisit.state("visitorIntroduction.name"), "Avery Example");
  assert.equal(firstVisit.state("visitorIntroduction.company"), "Acme Research");
  assert.equal(firstVisit.archivedMessages.length, 3);
  assert.equal(firstVisit.archivedMessages[0].messages[0].content, "Hi");
  assert.equal(firstVisit.archivedMessages[1].messages[0].content, "Avery Example");
  assert.equal(firstVisit.archivedMessages[2].messages[0].content, "Acme Research");

  await firstVisit.submit("Tell me about your RAG work.");
  const chatRequest = firstVisit.requests.find((request) => request.url === "/api/chat");
  assert.ok(chatRequest);
  const sentRequest = JSON.parse(chatRequest.options.body);
  assert.equal(sentRequest.session_id, firstSessionId);
  assert.deepEqual(sentRequest.history, []);
  assert.doesNotMatch(JSON.stringify(sentRequest.history), /Avery Example|Acme Research/);
});

test("a new conversation and a server restart both clear the active transcript", async () => {
  const chat = await createChat();
  const firstSessionId = chat.sessionId();
  await chat.submit("hi");
  assert.ok(chat.messages.children.length > 1);

  await chat.startNewConversation();
  const secondSessionId = chat.sessionId();
  assert.notEqual(secondSessionId, firstSessionId);
  assert.equal(chat.messages.children.length, 1);
  assert.equal(chat.state("visitorIntroduction.name"), "");
  assert.equal(chat.state("chatHistory.length"), 0);

  await chat.submit("Tell me about your RAG work.");
  chat.setServerInstance("server-2");
  await chat.checkServerInstance();

  assert.notEqual(chat.sessionId(), secondSessionId);
  assert.equal(chat.state("serverInstanceId"), "server-2");
  assert.equal(chat.messages.children.length, 1);
  assert.match(chat.status.textContent, /server restarted/i);
  assert.equal(chat.state("chatHistory.length"), 0);
});

test("visitors may skip introduction and questions can interrupt without leaking intro history", async () => {
  const chat = await createChat();

  await chat.submit("hello!");
  await chat.submit("skip");
  assert.match(chat.visibleReplies().at(-1), /what company are you with/i);
  await chat.submit("prefer not to say");
  assert.match(chat.visibleReplies().at(-1), /How can I help you today/);
  assert.equal(chat.state("visitorIntroduction.name"), "");
  assert.equal(chat.state("visitorIntroduction.company"), "");

  await chat.startNewConversation();
  await chat.submit("hi");
  await chat.submit("What projects has Subham worked on?");
  const chatRequest = chat.requests.find((request) => request.url === "/api/chat");
  assert.ok(chatRequest);
  const sentRequest = JSON.parse(chatRequest.options.body);
  assert.deepEqual(sentRequest.history, []);
  assert.equal(sentRequest.message, "What projects has Subham worked on?");
});
