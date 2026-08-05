(function () {
  "use strict";

  const conversations = {
    policy: {
      title: "Remote work policy",
      question: "How many days can employees work remotely, and who approves exceptions?",
      answer: [
        "Employees may work remotely for up to <strong>three days per week</strong>, provided their team maintains agreed coverage and the arrangement is documented with their manager <button class=\"inline-citation\" type=\"button\" data-source=\"S1\">[S1]</button>.",
        "Exceptions beyond three days require approval from both the employee's department head and People Operations. The policy recommends reviewing every exception after 90 days <button class=\"inline-citation\" type=\"button\" data-source=\"S2\">[S2]</button>.",
      ],
      sources: ["S1", "S2"],
    },
    security: {
      title: "Security onboarding",
      question: "Which security training must new employees complete?",
      answer: [
        "New employees must complete <strong>security awareness, phishing simulation, and data-handling training</strong> within their first ten business days <button class=\"inline-citation\" type=\"button\" data-source=\"S3\">[S3]</button>.",
        "People with administrative access must complete the privileged-access module before credentials are issued.",
      ],
      sources: ["S3"],
    },
    expenses: {
      title: "Travel expenses",
      question: "What receipts are required for travel expenses?",
      answer: [
        "An itemized receipt is required for any single travel expense above <strong>$25</strong>. Lodging receipts are required at every amount <button class=\"inline-citation\" type=\"button\" data-source=\"S4\">[S4]</button>.",
      ],
      sources: ["S4"],
    },
  };

  const sources = {
    S1: { title: "Remote Work Policy.pdf", page: "Page 4", path: "/Company policies/Remote Work Policy.pdf", section: "Flexible work arrangements", quote: "Eligible employees may work remotely for up to three days in a standard work week when team coverage is maintained and the arrangement is documented with their manager." },
    S2: { title: "People Handbook.pdf", page: "Page 18", path: "/Company policies/People Handbook.pdf", section: "Employment practices", quote: "Remote arrangements above three days per week require documented approval from the employee's department head and People Operations, with a review after 90 days." },
    S3: { title: "Security Onboarding.pdf", page: "Page 6", path: "/Security/Security Onboarding.pdf", section: "First ten days", quote: "All new starters complete security awareness, phishing simulation, and data-handling training within ten business days." },
    S4: { title: "Travel & Expenses.pdf", page: "Page 7", path: "/Company policies/Travel & Expenses.pdf", section: "Receipts and declarations", quote: "Itemized receipts are required for individual expenses above $25. Lodging receipts are always required." },
  };

  const transcript = document.querySelector("[data-transcript]");
  const aboutDialog = document.querySelector("[data-about-dialog]");
  const sourceDrawer = document.querySelector("[data-source-drawer]");
  const toast = document.querySelector("[data-toast]");
  let toastTimer;

  function escapeHtml(value) {
    return value.replace(/[&<>'\"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '\"': "&quot;" })[character]);
  }

  function showToast(message) {
    clearTimeout(toastTimer);
    toast.textContent = message;
    toast.classList.add("is-visible");
    toastTimer = setTimeout(() => toast.classList.remove("is-visible"), 2800);
  }

  function sourceCards(ids) {
    if (!ids.length) return "";
    return `<section class="source-list" aria-label="Citations"><div class="source-list__heading"><h3>Citations</h3></div><ol>${ids.map((id) => {
      const source = sources[id];
      return `<li><button class="source-card" type="button" data-source="${id}"><span class="source-card__label">${id}</span><span class="source-card__body"><strong>${source.title}</strong><span>${source.path} · ${source.section}</span></span><span class="source-card__page">${source.page}</span></button></li>`;
    }).join("")}</ol></section>`;
  }

  function renderConversation(key) {
    const item = conversations[key];
    if (!item) return;
    document.querySelector("[data-chat-title]").textContent = item.title;
    transcript.innerHTML = `<article class="chat-turn"><div class="chat-turn__question"><p>${escapeHtml(item.question)}</p></div><div class="chat-turn__answer"><div class="chat-turn__status"><span class="trust-status trust-status--verified"><svg aria-hidden="true" viewBox="0 0 24 24"><path d="m7 12 3 3 7-7"></path></svg>Verified</span></div><div class="chat-turn__output markdown-output">${item.answer.map((paragraph) => `<p>${paragraph}</p>`).join("")}</div>${sourceCards(item.sources)}</div></article>`;
    document.querySelectorAll("[data-conversation]").forEach((button) => {
      if (button.dataset.conversation === key) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    });
  }

  function showView(view) {
    const title = view === "knowledge" ? "Knowledge Base" : view === "system" ? "System" : "Chat";
    document.querySelectorAll("[data-view-panel]").forEach((panel) => { panel.hidden = panel.dataset.viewPanel !== view; });
    document.querySelectorAll("[data-sidebar-panel]").forEach((panel) => { panel.hidden = panel.dataset.sidebarPanel !== view; });
    document.querySelectorAll("[data-view]").forEach((button) => {
      if (button.dataset.view === view) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    });
    document.querySelector("[data-sidebar-title]").textContent = title;
    document.querySelector("[data-mobile-title]").textContent = title;
  }

  function openSource(id) {
    const source = sources[id];
    if (!source) return;
    sourceDrawer.querySelector("[data-source-label]").textContent = `${id} · ${source.title}`;
    sourceDrawer.querySelector("[data-source-page]").textContent = source.page;
    sourceDrawer.querySelector("[data-source-path]").textContent = source.path;
    sourceDrawer.querySelector("[data-source-section]").textContent = source.section;
    sourceDrawer.querySelector("[data-source-quote]").textContent = source.quote;
    sourceDrawer.showModal();
  }

  document.addEventListener("click", (event) => {
    const view = event.target.closest("[data-view]");
    if (view) showView(view.dataset.view);
    const conversation = event.target.closest("[data-conversation]");
    if (conversation) { showView("chat"); renderConversation(conversation.dataset.conversation); }
    const source = event.target.closest("[data-source]");
    if (source) openSource(source.dataset.source);
    const action = event.target.closest("[data-demo-action]");
    if (action) showToast(action.dataset.demoAction);
    if (event.target.closest("[data-new-chat]")) { showView("chat"); document.querySelector("[data-question-input]").focus(); showToast("Ask one of the sample topics: remote work, security, or expenses."); }
    if (event.target.closest("[data-open-about]")) aboutDialog.showModal();
    if (event.target.closest("[data-close-about]")) aboutDialog.close();
    if (event.target.closest("[data-close-source]")) sourceDrawer.close();
    if (event.target.closest("[data-open-knowledge]")) { sourceDrawer.close(); showView("knowledge"); }
    if (event.target.closest("[data-open-mobile]")) showToast("Use the Preview button to learn about this static tour.");
  });

  const input = document.querySelector("[data-question-input]");
  const send = document.querySelector(".chat-composer__send");
  const help = document.querySelector("[data-composer-help]");
  input.addEventListener("input", () => {
    const length = Array.from(input.value.trim()).length;
    help.textContent = `Enter to send · Shift+Enter for a new line · ${length.toLocaleString()}/2,000`;
    send.disabled = length === 0 || length > 2000;
  });

  function answerQuestion() {
    const question = input.value.trim();
    if (!question) return;
    const normalized = question.toLowerCase();
    const key = normalized.includes("security") || normalized.includes("training") ? "security" : normalized.includes("receipt") || normalized.includes("expense") || normalized.includes("travel") ? "expenses" : "policy";
    renderConversation(key);
    input.value = "";
    input.dispatchEvent(new Event("input"));
  }

  document.querySelector("[data-composer]").addEventListener("submit", (event) => { event.preventDefault(); answerQuestion(); });
  input.addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); answerQuestion(); } });

  document.querySelector("[data-chat-search]").addEventListener("input", (event) => {
    const query = event.target.value.toLowerCase();
    let shown = 0;
    document.querySelectorAll(".conversation-item").forEach((item) => {
      const matches = item.textContent.toLowerCase().includes(query);
      item.hidden = !matches;
      if (matches) shown += 1;
    });
    document.querySelector("[data-chat-empty]").hidden = shown !== 0;
  });

  document.querySelector("[data-theme-toggle]").addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("local-rag-preview-theme", next);
  });

  requestAnimationFrame(() => {
    transcript.scrollTop = transcript.scrollHeight;
  });
})();
