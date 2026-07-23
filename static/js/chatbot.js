/**
 * chatbot.js
 * BMY Marketer AI Assistant - frontend widget logic.
 * Talks only to the local Flask backend (/api/chat/*). No third-party AI API calls.
 */

(function () {
  const API_BASE = "https://bmy-chatbot.onrender.com";

  const el = {
    widget: document.getElementById("bmy-chat-widget"),
    toggle: document.getElementById("bmy-chat-toggle"),
    window: document.getElementById("bmy-chat-window"),
    close: document.getElementById("bmy-chat-close"),
    welcomeScreen: document.getElementById("bmy-welcome-screen"),
    chatBody: document.getElementById("bmy-chat-body"),
    startChatBtn: document.getElementById("bmy-start-chat"),
    quickActions: document.querySelectorAll(".bmy-quick-action"),
    messages: document.getElementById("bmy-chat-messages"),
    typing: document.getElementById("bmy-typing-indicator"),
    form: document.getElementById("bmy-chat-form"),
    input: document.getElementById("bmy-chat-input"),
    attachBtn: document.getElementById("bmy-chat-attach"),
    fileInput: document.getElementById("bmy-chat-file-input"),
    micBtn: document.getElementById("bmy-chat-mic"),
    voiceToggle: document.getElementById("bmy-voice-toggle"),
  };

  const STORAGE_KEY = "bmy_chat_session_id";
  const VOICE_PREF_KEY = "bmy_chat_voice_replies";
  const THEME_CACHE_KEY = "bmy_chat_theme";
  let conversationId = null;
  let sessionPromise = null;
  let conversationEnded = false;

  // ---------- Theme: apply whichever color scheme is set in the admin
  // dashboard's Settings tab. Cached value applies instantly (no flash of
  // the wrong color); then we confirm/update against the server in case
  // it was changed since the last visit. ----------

  function applyTheme(themeName) {
    if (!themeName || !el.widget) return;
    el.widget.setAttribute("data-bmy-theme", themeName);
  }

  (function loadTheme() {
    const cached = localStorage.getItem(THEME_CACHE_KEY);
    if (cached) applyTheme(cached);

    fetch(`${API_BASE}/api/theme`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (data && data.theme) {
          applyTheme(data.theme);
          localStorage.setItem(THEME_CACHE_KEY, data.theme);
        }
      })
      .catch(() => {
        /* offline or endpoint unavailable - cached/default theme stands */
      });
  })();

  function getOrCreateSessionId() {
    let id = localStorage.getItem(STORAGE_KEY);
    if (!id) {
      id = "sess_" + Math.random().toString(36).slice(2) + Date.now();
      localStorage.setItem(STORAGE_KEY, id);
    }
    return id;
  }

  function appendMessage(sender, text, opts) {
    opts = opts || {};
    const bubble = document.createElement("div");
    bubble.className = "bmy-msg " + (sender === "bot" ? "bot" : "customer");
    bubble.textContent = text;

    const time = document.createElement("div");
    time.className = "bmy-msg-time";
    time.textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    bubble.appendChild(time);

    el.messages.appendChild(bubble);
    el.messages.scrollTop = el.messages.scrollHeight;

    if (sender === "bot" && !opts.silent) {
      speakText(text);
      notifyBotReply();
    }
  }

  // Simulates a realistic typing delay so bot replies don't feel instant -
  // scales with reply length, floored at 0.8s and capped at 3s.
  function calculateTypingDelay(messageText) {
    const minDelay = 800; // Minimum 0.8 seconds
    const speedPerChar = 25; // 25ms per character
    return Math.min(minDelay + (messageText.length * speedPerChar), 3000); // Max 3 seconds
  }

  function showTyping(show) {
    el.typing.classList.toggle("bmy-hidden", !show);
    if (show) el.messages.scrollTop = el.messages.scrollHeight;
  }

  // ---------- In-chat option buttons (service list, budget, etc.) ----------
  // Matches the "icon" keys used in knowledge/purchase_flow.json.
  // Real inline SVG line-icons (not emoji) so the widget doesn't render
  // differently across OS/browser emoji fonts and doesn't look like a
  // placeholder. Single shared style: 24x24 viewBox, stroke-based, sized
  // via CSS to 1em so each spot's existing font-size still controls scale.
  const svgIcon = (inner) =>
    `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ` +
    `stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${inner}</svg>`;

  const ICON_MAP = {
    "check-square": svgIcon('<path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>'),
    "x-square": svgIcon('<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 9l6 6M15 9l-6 6"/>'),
    "wrench": svgIcon('<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94z"/>'),
    "search": svgIcon('<circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/>'),
    "map-pin": svgIcon('<path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/>'),
    "smartphone": svgIcon('<rect x="5" y="2" width="14" height="20" rx="2"/><path d="M12 18h.01"/>'),
    "megaphone": svgIcon('<path d="M3 11v2a1 1 0 0 0 1 1h2l4 4V6L6 10H4a1 1 0 0 0-1 1z"/><path d="M16 8a3 3 0 0 1 0 8"/><path d="M19 5a7 7 0 0 1 0 14"/>'),
    "palette": svgIcon('<path d="M12 22a10 10 0 1 1 0-20 8 8 0 0 1 8 8 4 4 0 0 1-4 4h-1a2 2 0 0 0-1 3.7A2 2 0 0 1 12 22z"/><circle cx="7.5" cy="10.5" r="1.1"/><circle cx="10.5" cy="7" r="1.1"/><circle cx="14.5" cy="7" r="1.1"/><circle cx="16.8" cy="10.5" r="1.1"/>'),
    "clapperboard": svgIcon('<path d="M20.2 6L3 11l-.9-2.4c-.3-1.1.3-2.2 1.3-2.5l13.5-4c1.1-.3 2.2.3 2.5 1.3z"/><path d="M6.2 5.3L7 8M12.4 3.4l.8 2.7"/><path d="M3 11h18v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>'),
    "bot": svgIcon('<rect x="3" y="11" width="18" height="10" rx="2"/><circle cx="12" cy="5" r="2"/><path d="M12 7v4"/><circle cx="8" cy="16" r="1"/><circle cx="16" cy="16" r="1"/>'),
    "money-bag": svgIcon('<rect x="2" y="6" width="20" height="12" rx="2"/><circle cx="12" cy="12" r="2.2"/><path d="M6 12h.01M18 12h.01"/>'),
    "dollar-sign": svgIcon('<path d="M12 1v22"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/>'),
    "help-circle": svgIcon('<circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><path d="M12 17h.01"/>'),
    "users": svgIcon('<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>'),
    "phone": svgIcon('<path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.13.96.36 1.9.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.91.34 1.85.57 2.81.7A2 2 0 0 1 22 16.92z"/>'),
    "message-circle": svgIcon('<path d="M21 11.5a8.38 8.38 0 0 1-4.8 7.6 8.5 8.5 0 0 1-9.05-.9L3 21l1.9-5.7a8.5 8.5 0 0 1 3.8-11.9 8.38 8.38 0 0 1 12.3 8.1z"/>'),
    "mail": svgIcon('<rect x="2" y="4" width="20" height="16" rx="2"/><path d="M22 6l-10 7L2 6"/>'),
    "shopping-cart": svgIcon('<circle cx="9" cy="21" r="1"/><circle cx="20" cy="21" r="1"/><path d="M1 1h4l2.68 13.39a2 2 0 0 0 2 1.61h9.72a2 2 0 0 0 2-1.61L23 6H6"/>'),
    "globe": svgIcon('<circle cx="12" cy="12" r="10"/><path d="M2 12h20"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>'),
    "bar-chart": svgIcon('<line x1="12" y1="20" x2="12" y2="10"/><line x1="18" y1="20" x2="18" y2="4"/><line x1="6" y1="20" x2="6" y2="16"/>'),
    "calendar": svgIcon('<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>'),
    "clock": svgIcon('<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>'),
  };
  const ICON_FALLBACK = svgIcon('<circle cx="12" cy="12" r="3"/>');

  function appendButtonMessage(questionText, options) {
    if (questionText) appendMessage("bot", questionText);

    const wrap = document.createElement("div");
    wrap.className = "bmy-msg-options";

    options.forEach((opt) => {
      const label = typeof opt === "string" ? opt : opt.label;
      const icon = typeof opt === "string" ? null : opt.icon;

      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "bmy-msg-option";
      if (icon) {
        const iconSpan = document.createElement("span");
        iconSpan.className = "bmy-msg-option-icon";
        iconSpan.innerHTML = ICON_MAP[icon] || ICON_FALLBACK;
        btn.appendChild(iconSpan);
      }
      const labelSpan = document.createElement("span");
      labelSpan.className = "bmy-msg-option-label";
      labelSpan.textContent = label;
      btn.appendChild(labelSpan);

      btn.addEventListener("click", () => {
        // Lock all buttons in this group once one is picked, then send the choice.
        wrap.querySelectorAll(".bmy-msg-option").forEach((b) => (b.disabled = true));
        sendMessage(label);
      });

      wrap.appendChild(btn);
    });

    el.messages.appendChild(wrap);
    el.messages.scrollTop = el.messages.scrollHeight;
  }

  function startSessionOnce() {
    if (sessionPromise) return sessionPromise;

    const payload = {
      session_id: getOrCreateSessionId(),
      browser_info: navigator.userAgent,
      device_info: /Mobi|Android/i.test(navigator.userAgent) ? "mobile" : "desktop",
    };

    sessionPromise = fetch(`${API_BASE}/api/chat/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })
      .then((res) => res.json())
      .then((data) => {
        conversationId = data.conversation_id;
        if (Array.isArray(data.messages) && data.messages.length) {
          // Resuming an existing conversation - restore the full sequence
          // instead of just the greeting, so a page reload/reopen shows
          // everything the visitor already said, not just 1-2 messages.
          data.messages.forEach((m) => appendMessage(m.sender, m.message, { silent: true }));
        } else if (data.greeting) {
          appendMessage("bot", data.greeting);
        }
      })
      .catch((err) => {
        appendMessage("bot", "Sorry, I couldn't connect to the assistant service. Please try again shortly.");
        console.error("BMY chat start failed:", err);
      });

    return sessionPromise;
  }

  function setInputLocked(locked) {
    if (el.input) {
      el.input.disabled = locked;
      el.input.placeholder = locked ? "This conversation has ended" : "";
    }
    const sendBtn = document.getElementById("bmy-chat-send");
    if (sendBtn) sendBtn.disabled = locked;
    if (el.attachBtn) el.attachBtn.disabled = locked;
    if (el.micBtn) el.micBtn.disabled = locked;
  }

  /**
   * Ends the conversation for real: tells the backend (so it stops
   * accepting further /api/chat/message calls for this conversation_id)
   * and locks the input on this end. The next time the widget is opened,
   * a brand new conversation is started rather than resuming this one.
   */
  function endConversation() {
    if (!conversationId || conversationEnded) return;
    conversationEnded = true;
    setInputLocked(true);
    appendMessage("bot", "This conversation has ended. Reopen the chat anytime to start a new one.", { silent: true });
    fetch(`${API_BASE}/api/chat/end`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conversation_id: conversationId }),
    }).catch(() => {});
  }

  async function sendMessage(text) {
    if (conversationEnded) return;
    appendMessage("customer", text);
    showTyping(true);

    try {
      const res = await fetch(`${API_BASE}/api/chat/message`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ conversation_id: conversationId, message: text }),
      });
      const data = await res.json();

      if (data.error) {
        showTyping(false);
        if (data.code === "conversation_ended") {
          conversationEnded = true;
          setInputLocked(true);
          appendMessage("bot", data.error);
          return;
        }
        appendMessage("bot", "Something went wrong: " + data.error);
        return;
      }

      // Hold the typing indicator a beat longer for longer replies, so the
      // bot doesn't feel like it "instantly" produced a long answer.
      const delay = calculateTypingDelay(data.answer || "");
      setTimeout(() => {
        showTyping(false);
        if (Array.isArray(data.options) && data.options.length) {
          appendButtonMessage(data.answer, data.options);
        } else {
          appendMessage("bot", data.answer);
        }
        // The bot's "closing_thanks" reply is its sign-off after the
        // escalation message ("I'll flag this to a member of our team...")
        // and after the guided flow's own completion message - once it's
        // been shown, there's nothing left for the visitor to do here, so
        // end the conversation for real instead of leaving it open to fall
        // through to the generic fallback on the next stray message.
        if (data.intent === "closing_thanks") {
          endConversation();
        }
      }, delay);
    } catch (err) {
      showTyping(false);
      appendMessage("bot", "I'm having trouble responding right now. Please try again.");
      console.error("BMY chat message failed:", err);
    }
  }

  async function uploadFile(file) {
    appendMessage("customer", "Attached: " + file.name);
    showTyping(true);

    const formData = new FormData();
    formData.append("conversation_id", conversationId);
    formData.append("file", file);

    try {
      const res = await fetch(`${API_BASE}/api/chat/upload`, { method: "POST", body: formData });
      const data = await res.json();
      showTyping(false);
      if (data.error) {
        appendMessage("bot", "Sorry, I couldn't accept that file: " + data.error);
        return;
      }
      appendMessage("bot", `Got it - I've received "${data.filename}". Our team will take a look.`);
    } catch (err) {
      showTyping(false);
      appendMessage("bot", "Sorry, that upload failed. Please try again.");
      console.error("BMY file upload failed:", err);
    }
  }

  // ---------- Welcome screen transitions ----------

  function revealChatBody() {
    el.welcomeScreen.classList.add("bmy-hidden");
    el.chatBody.classList.remove("bmy-hidden");
  }

  async function beginChat(promptText) {
    revealChatBody();
    await startSessionOnce();
    if (promptText) {
      await sendMessage(promptText);
    } else {
      el.input.focus();
    }
  }

  el.startChatBtn.addEventListener("click", () => beginChat());

  el.quickActions.forEach((btn) => {
    btn.addEventListener("click", () => beginChat(btn.dataset.prompt));
  });

  // ---------- Event wiring ----------

  function resetForNewConversation() {
    conversationId = null;
    sessionPromise = null;
    conversationEnded = false;
    setInputLocked(false);
    if (el.messages) el.messages.innerHTML = "";
    el.chatBody.classList.add("bmy-hidden");
    el.welcomeScreen.classList.remove("bmy-hidden");
  }

  el.toggle.addEventListener("click", () => {
    if (conversationEnded) resetForNewConversation();
    el.window.classList.toggle("bmy-hidden");
    if (!el.window.classList.contains("bmy-hidden") && conversationId && !conversationEnded) {
      // Already mid-conversation from earlier in this page visit - skip the welcome screen.
      revealChatBody();
      el.input.focus();
    }
  });

  el.close.addEventListener("click", () => {
    endConversation();
    el.window.classList.add("bmy-hidden");
  });

  el.form.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = el.input.value.trim();
    if (!text) return;
    el.input.value = "";
    sendMessage(text);
  });

  el.attachBtn.addEventListener("click", () => {
    if (!conversationId) {
      appendMessage("bot", "Please wait a moment while I set up our conversation...");
      return;
    }
    el.fileInput.click();
  });

  el.fileInput.addEventListener("change", () => {
    const file = el.fileInput.files[0];
    if (file) uploadFile(file);
    el.fileInput.value = "";
  });

  // ---------- Voice: speech-to-text input (mic) ----------

  const SpeechRecognitionAPI = window.SpeechRecognition || window.webkitSpeechRecognition;
  let recognizer = null;
  let listening = false;

  if (SpeechRecognitionAPI && el.micBtn) {
    recognizer = new SpeechRecognitionAPI();
    recognizer.continuous = false;
    recognizer.interimResults = false;
    recognizer.lang = "en-US";

    recognizer.addEventListener("start", () => {
      listening = true;
      el.micBtn.classList.add("listening");
    });

    recognizer.addEventListener("end", () => {
      listening = false;
      el.micBtn.classList.remove("listening");
    });

    recognizer.addEventListener("result", (event) => {
      const transcript = event.results[0][0].transcript.trim();
      if (!transcript) return;
      el.input.value = transcript;
      sendMessage(transcript);
      el.input.value = "";
    });

    recognizer.addEventListener("error", () => {
      listening = false;
      el.micBtn.classList.remove("listening");
    });

    el.micBtn.addEventListener("click", async () => {
      if (!conversationId) {
        await beginChat();
      }
      if (listening) {
        recognizer.stop();
        return;
      }
      try {
        recognizer.start();
      } catch (err) {
        // Already started / not allowed - ignore, button state stays as-is.
      }
    });
  } else if (el.micBtn) {
    // Browser doesn't support speech recognition (e.g. older Firefox) - hide the control.
    el.micBtn.style.display = "none";
  }

  // ---------- Voice: text-to-speech output (spoken replies) ----------

  const synth = window.speechSynthesis;
  let voiceRepliesEnabled = localStorage.getItem(VOICE_PREF_KEY) === "1";

  const VOICE_ICON_ON =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/></svg>';
  const VOICE_ICON_OFF =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><line x1="23" y1="9" x2="17" y2="15"/><line x1="17" y1="9" x2="23" y2="15"/></svg>';

  function updateVoiceToggleUI() {
    if (!el.voiceToggle) return;
    el.voiceToggle.innerHTML = voiceRepliesEnabled ? VOICE_ICON_ON : VOICE_ICON_OFF;
    el.voiceToggle.classList.toggle("active", voiceRepliesEnabled);
    el.voiceToggle.setAttribute("aria-pressed", String(voiceRepliesEnabled));
    el.voiceToggle.title = voiceRepliesEnabled ? "Spoken replies on - click to mute" : "Read replies aloud";
  }

  function speakText(text) {
    if (!voiceRepliesEnabled || !synth || !text) return;
    synth.cancel(); // don't stack overlapping replies
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.rate = 1;
    utterance.pitch = 1;
    synth.speak(utterance);
  }

  if (el.voiceToggle) {
    if (!synth) {
      el.voiceToggle.style.display = "none";
    } else {
      updateVoiceToggleUI();
      el.voiceToggle.addEventListener("click", () => {
        voiceRepliesEnabled = !voiceRepliesEnabled;
        localStorage.setItem(VOICE_PREF_KEY, voiceRepliesEnabled ? "1" : "0");
        if (!voiceRepliesEnabled) synth.cancel();
        updateVoiceToggleUI();
      });
    }
  }

  window.addEventListener("beforeunload", () => {
    if (conversationId && navigator.sendBeacon) {
      navigator.sendBeacon(
        `${API_BASE}/api/chat/end`,
        new Blob([JSON.stringify({ conversation_id: conversationId })], { type: "application/json" })
      );
    }
  });

  // ---------- Proactive greeting bubble (shows once per day, doesn't open the chat) ----------

  // localStorage (not sessionStorage) so this persists across tabs/reloads -
  // we store *when* it last showed and gate on elapsed time, not a one-shot flag.
  const NOTIFY_LAST_SHOWN_KEY = "bmy_chat_notify_last_shown";
  const NOTIFY_INTERVAL_MS = 24 * 60 * 60 * 1000; // 24 hours
  const notifyEl = document.getElementById("bmy-chat-notify");
  const notifyCloseBtn = document.getElementById("bmy-chat-notify-close");
  const toggleDot = document.getElementById("bmy-chat-toggle-dot");

  function playNotifySound() {
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      const ctx = new Ctx();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.setValueAtTime(880, ctx.currentTime);
      osc.frequency.setValueAtTime(1175, ctx.currentTime + 0.1);
      gain.gain.setValueAtTime(0.001, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.15, ctx.currentTime + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.35);
      osc.connect(gain).connect(ctx.destination);
      osc.start();
      osc.stop(ctx.currentTime + 0.35);
    } catch (err) {
      // Autoplay blocked or unsupported - fail silently, bubble still shows.
    }
  }

  function hideNotifyBubble() {
    if (notifyEl) notifyEl.classList.add("bmy-hidden");
  }

  function clearUnreadIndicator() {
    if (toggleDot) toggleDot.classList.add("bmy-hidden");
    el.toggle.classList.remove("bmy-toggle-bounce");
  }

  // Called on every bot reply. Sound always plays; the bounce + red dot are
  // only for messages the customer hasn't seen yet (chat window closed).
  function notifyBotReply() {
    playNotifySound();

    const isClosed = !el.window || el.window.classList.contains("bmy-hidden");
    if (!isClosed) return;

    if (toggleDot) toggleDot.classList.remove("bmy-hidden");
    // Restart the bounce even if it's already mid-animation from a previous message.
    el.toggle.classList.remove("bmy-toggle-bounce");
    void el.toggle.offsetWidth; // force reflow so the animation re-triggers
    el.toggle.classList.add("bmy-toggle-bounce");
  }

  function showNotifyBubble() {
    if (!notifyEl) return;

    const lastShown = Number(localStorage.getItem(NOTIFY_LAST_SHOWN_KEY) || 0);
    if (Date.now() - lastShown < NOTIFY_INTERVAL_MS) return;

    // Don't interrupt someone already mid-conversation.
    if (conversationId || (el.window && !el.window.classList.contains("bmy-hidden"))) return;

    notifyEl.classList.remove("bmy-hidden");
    if (toggleDot) toggleDot.classList.remove("bmy-hidden");
    playNotifySound();
    localStorage.setItem(NOTIFY_LAST_SHOWN_KEY, String(Date.now()));

    // Auto-dismiss after 10s if ignored (stays a passive teaser, not a nag).
    setTimeout(hideNotifyBubble, 10000);
  }

  if (notifyEl) {
    notifyEl.addEventListener("click", () => {
      hideNotifyBubble();
      clearUnreadIndicator();
      el.window.classList.remove("bmy-hidden");
    });
    if (notifyCloseBtn) {
      notifyCloseBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        hideNotifyBubble();
      });
    }
    notifyEl.querySelectorAll(".bmy-notify-service").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        // Stop this from also triggering notifyEl's own click handler above
        // (which would open the chat a second time, to the welcome screen).
        e.stopPropagation();
        hideNotifyBubble();
        clearUnreadIndicator();
        el.window.classList.remove("bmy-hidden");
        beginChat(btn.dataset.prompt);
      });
    });
    setTimeout(showNotifyBubble, 3000);
  }

  el.toggle.addEventListener("click", () => {
    hideNotifyBubble();
    clearUnreadIndicator();
  });
})();

// -----------------------------------------------------------------------
// Optional: cache full chat transcript in localStorage.
//
// NOT wired up above. Two things worth knowing before enabling it:
//
// 1. This app already restores conversation history from the SERVER on
//    load (see the "Resuming an existing conversation" branch in
//    startChat() above) - it fetches the full message list keyed by the
//    id already saved in localStorage (STORAGE_KEY). Adding a second,
//    client-only cache risks the two sources disagreeing (e.g. showing
//    a stale local copy before the server response arrives and then
//    doubling messages), and there's no renderSavedMessages() function
//    in this file to call - you'd need to write one that de-dupes
//    against the server-restored messages.
// 2. Chat transcripts can contain a lead's name, email, and phone
//    number. The rest of this app treats that data carefully (see
//    crypto_utils.py / encrypted DB columns) - writing full transcripts
//    to localStorage puts that same PII in plain text, readable by any
//    script on the page or anyone with device/browser access.
//
// If you still want a client-side cache (e.g. purely as an offline
// fallback), call saveChatToLocal(messages) after each new message and
// merge with server data on load rather than replacing it outright.

function saveChatToLocal(messages) {
  localStorage.setItem("chat_history", JSON.stringify(messages));
}