const CHAT_MEMORY_KEY = "nearbygo-chat-memory-v1";
const MAX_SAVED_MESSAGES = 24;
const MAX_SAVED_MESSAGE_LENGTH = 6000;

function loadChatMemory() {
  try {
    const saved = JSON.parse(localStorage.getItem(CHAT_MEMORY_KEY) || "null");
    if (!saved || typeof saved !== "object") return { conversationId: "", history: [] };

    const history = Array.isArray(saved.history)
      ? saved.history
          .filter(
            (item) =>
              item &&
              ["user", "assistant"].includes(item.role) &&
              typeof item.text === "string" &&
              item.text.trim(),
          )
          .slice(-MAX_SAVED_MESSAGES)
          .map((item) => ({
            role: item.role,
            text: item.text.slice(0, MAX_SAVED_MESSAGE_LENGTH),
          }))
      : [];

    return {
      conversationId: typeof saved.conversationId === "string" ? saved.conversationId : "",
      history,
    };
  } catch {
    localStorage.removeItem(CHAT_MEMORY_KEY);
    return { conversationId: "", history: [] };
  }
}

const savedChat = loadChatMemory();
const state = {
  position: null,
  conversationId: savedChat.conversationId,
  history: savedChat.history,
  user: localStorage.getItem("nearbygo-user") || crypto.randomUUID(),
  busy: false,
  recorder: null,
  recognition: null,
  recordingChunks: [],
};
localStorage.setItem("nearbygo-user", state.user);

const messages = document.querySelector("#messages");
const composer = document.querySelector("#composer");
const input = document.querySelector("#queryInput");
const sendButton = document.querySelector("#sendButton");
const clearChatButton = document.querySelector("#clearChatButton");
const locationButton = document.querySelector("#locationButton");
const locationLabel = document.querySelector("#locationLabel");
const voiceButton = document.querySelector("#voiceButton");
const welcomeMessage = messages.firstElementChild.cloneNode(true);

const { escapeHtml, renderMarkdown } = window.NearbyGoMarkdown;

function formatAnswer(value) {
  return renderMarkdown(value);
}

function stripReasoning(value) {
  let result = value.replace(/<think\b[^>]*>[\s\S]*?<\/think\s*>/gi, "");

  const openThink = result.search(/<think\b[^>]*>/i);
  if (openThink >= 0) result = result.slice(0, openThink);

  result = result.replace(
    /<!--\s*dify-deepseek-reasoning\s*-->[\s\S]*?<!--\s*\/dify-deepseek-reasoning\s*-->/gi,
    "",
  );
  const openMarker = result.search(/<!--\s*dify-deepseek-reasoning\s*-->/i);
  if (openMarker >= 0) result = result.slice(0, openMarker);

  result = result
    .replace(/<\/?think\b[^>]*>/gi, "")
    .replace(/<!--\s*\/?dify-deepseek-reasoning\s*-->/gi, "");

  const lowered = result.toLowerCase();
  for (const token of ["<think", "<!--dify-deepseek-reasoning"]) {
    const maxLength = Math.min(token.length, result.length);
    for (let length = maxLength; length > 0; length -= 1) {
      if (lowered.endsWith(token.slice(0, length))) {
        return result.slice(0, -length).trimStart();
      }
    }
  }

  return result.trimStart();
}

function addMessage(role, text = "") {
  const article = document.createElement("article");
  article.className = `message ${role}`;
  if (role === "assistant") {
    const avatar = document.createElement("div");
    avatar.className = "avatar";
    avatar.textContent = "游";
    article.append(avatar);
  }
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.innerHTML = role === "assistant" ? formatAnswer(text) : `<p>${escapeHtml(text)}</p>`;
  article.append(bubble);
  messages.append(article);
  messages.scrollTop = messages.scrollHeight;
  return bubble;
}

function saveChatMemory() {
  try {
    localStorage.setItem(
      CHAT_MEMORY_KEY,
      JSON.stringify({
        conversationId: state.conversationId,
        history: state.history.slice(-MAX_SAVED_MESSAGES),
      }),
    );
  } catch {
    // Storage can be unavailable or full in private/in-app browsers.
  }
}

function rememberTurn(query, answer) {
  state.history.push(
    { role: "user", text: query.slice(0, MAX_SAVED_MESSAGE_LENGTH) },
    { role: "assistant", text: answer.slice(0, MAX_SAVED_MESSAGE_LENGTH) },
  );
  state.history = state.history.slice(-MAX_SAVED_MESSAGES);
  saveChatMemory();
}

function restoreChat() {
  if (!state.history.length) return;
  messages.replaceChildren();
  state.history.forEach(({ role, text }) => addMessage(role, text));
}

function clearChatMemory() {
  if (state.busy || !window.confirm("清空当前设备上的聊天记录并开始新对话？")) return;
  state.conversationId = "";
  state.history = [];
  localStorage.removeItem(CHAT_MEMORY_KEY);
  messages.replaceChildren(welcomeMessage.cloneNode(true));
}

restoreChat();

function locate() {
  locationButton.className = "location-button";
  locationLabel.textContent = "正在定位";
  if (!navigator.geolocation) {
    locationButton.classList.add("failed");
    locationLabel.textContent = "使用清华默认位置";
    return;
  }
  navigator.geolocation.getCurrentPosition(
    ({ coords }) => {
      state.position = {
        longitude: coords.longitude,
        latitude: coords.latitude,
        accuracy: coords.accuracy,
      };
      locationButton.classList.add("ready");
      locationLabel.textContent = `已定位 · ±${Math.round(coords.accuracy)}m`;
    },
    () => {
      state.position = null;
      locationButton.classList.add("failed");
      locationLabel.textContent = "使用清华默认位置";
    },
    { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 },
  );
}

function handleEvent(event) {
  if (event.conversation_id) state.conversationId = event.conversation_id;
  if (["message", "agent_message"].includes(event.event) && event.answer) return event.answer;
  if (event.event === "workflow_finished" && event.data?.status === "failed") {
    throw new Error(event.data.error || "Dify 工作流执行失败");
  }
  if (event.event === "error") throw new Error(event.message || "Dify 调用失败");
  return "";
}

function progressText(event) {
  if (!["node_started", "workflow_started"].includes(event.event)) return "";
  const title = String(event.data?.title || event.data?.node_title || "");
  if (title.includes("需求分流")) return "正在理解你的需求…";
  if (title.includes("提取推荐条件")) return "正在整理预算、距离和偏好…";
  if (title.includes("高德附近推荐")) return "正在查询附近地点和实时路线…";
  if (title.includes("结果可信度审计")) return "正在核对地点与路线信息…";
  if (title.includes("生成推荐说明")) return "已找到结果，正在生成推荐…";
  if (event.event === "workflow_started") return "正在理解你的需求…";
  return "";
}

async function sendQuery(query) {
  if (state.busy || !query.trim()) return;
  state.busy = true;
  sendButton.disabled = true;
  clearChatButton.disabled = true;
  voiceButton.disabled = true;
  input.value = "";
  addMessage("user", query);
  const answerBubble = addMessage("assistant", "");
  answerBubble.classList.add("typing");
  let rawAnswer = "";
  let answer = "";

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query,
        ...(state.position || {}),
        coordinate_system: "gps",
        conversation_id: state.conversationId,
        user: state.user,
      }),
    });
    if (!response.ok || !response.body) throw new Error(`聊天服务返回 ${response.status}`);

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done }).replaceAll("\r\n", "\n");
      const chunks = buffer.split("\n\n");
      buffer = chunks.pop() || "";
      for (const chunk of chunks) {
        const line = chunk.split("\n").find((item) => item.startsWith("data:"));
        if (!line) continue;
        const payload = JSON.parse(line.slice(5).trim());
        const progress = progressText(payload);
        if (progress && !rawAnswer) answerBubble.textContent = progress;
        rawAnswer += handleEvent(payload);
        answer = stripReasoning(rawAnswer);
        if (answer) {
          answerBubble.classList.remove("typing");
          answerBubble.innerHTML = formatAnswer(answer);
        }
        messages.scrollTop = messages.scrollHeight;
      }
      if (done) break;
    }
    if (!answer) {
      answerBubble.classList.remove("typing");
      answerBubble.innerHTML = "<p>暂时没有取得推荐，请稍后重试。</p>";
    } else {
      rememberTurn(query, answer);
    }
  } catch (error) {
    answerBubble.classList.remove("typing");
    answerBubble.innerHTML = `<p>请求失败：${escapeHtml(error.message)}</p>`;
  } finally {
    state.busy = false;
    sendButton.disabled = false;
    clearChatButton.disabled = false;
    voiceButton.disabled = false;
    input.focus();
  }
}

function preferredRecordingType() {
  const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"];
  return candidates.find((type) => window.MediaRecorder?.isTypeSupported(type)) || "";
}

function browserRecognitionConstructor() {
  return window.SpeechRecognition || window.webkitSpeechRecognition || null;
}

function resetVoiceButton() {
  voiceButton.classList.remove("recording");
  voiceButton.setAttribute("aria-label", "按下语音输入");
  voiceButton.textContent = "🎙️";
}

function startBrowserRecognition(Recognition) {
  const recognition = new Recognition();
  state.recognition = recognition;
  recognition.lang = "zh-CN";
  recognition.continuous = false;
  recognition.interimResults = true;

  recognition.addEventListener("start", () => {
    voiceButton.classList.add("recording");
    voiceButton.setAttribute("aria-label", "停止语音识别");
  });
  recognition.addEventListener("result", (event) => {
    let transcript = "";
    for (let index = 0; index < event.results.length; index += 1) {
      transcript += event.results[index][0]?.transcript || "";
    }
    input.value = transcript.trim();
    input.dispatchEvent(new Event("input"));
  });
  recognition.addEventListener("error", (event) => {
    if (!["aborted", "no-speech"].includes(event.error)) {
      window.alert(`语音识别失败：${event.error || "请检查麦克风权限"}`);
    }
  });
  recognition.addEventListener("end", () => {
    state.recognition = null;
    resetVoiceButton();
    input.focus();
  }, { once: true });
  recognition.start();
}

async function transcribeRecording(blob) {
  voiceButton.disabled = true;
  voiceButton.textContent = "…";
  const data = new FormData();
  const extension = blob.type.includes("mp4") ? "m4a" : "webm";
  data.append("audio", blob, `voice.${extension}`);
  try {
    const response = await fetch("/api/audio-to-text", {
      method: "POST",
      headers: { "X-NearbyGo-User": state.user },
      body: data,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || `语音识别返回 ${response.status}`);
    input.value = String(payload.text || "");
    input.dispatchEvent(new Event("input"));
    input.focus();
  } catch (error) {
    window.alert(`录音识别失败：${error.message}`);
  } finally {
    voiceButton.disabled = false;
    voiceButton.textContent = "🎙️";
  }
}

async function toggleRecording() {
  if (state.busy) return;
  if (state.recognition) {
    state.recognition.stop();
    return;
  }
  if (state.recorder?.state === "recording") {
    state.recorder.stop();
    return;
  }
  const Recognition = browserRecognitionConstructor();
  if (Recognition) {
    try {
      startBrowserRecognition(Recognition);
    } catch {
      resetVoiceButton();
      window.alert("无法启动语音识别，请检查浏览器麦克风权限。");
    }
    return;
  }
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    window.alert("当前浏览器不支持语音输入，请使用 Chrome、Edge 或 Safari 新版本。");
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const mimeType = preferredRecordingType();
    state.recordingChunks = [];
    state.recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    state.recorder.addEventListener("dataavailable", (event) => {
      if (event.data.size) state.recordingChunks.push(event.data);
    });
    state.recorder.addEventListener("stop", () => {
      resetVoiceButton();
      stream.getTracks().forEach((track) => track.stop());
      const blob = new Blob(state.recordingChunks, { type: state.recorder.mimeType || "audio/webm" });
      void transcribeRecording(blob);
    }, { once: true });
    state.recorder.start();
    voiceButton.classList.add("recording");
    voiceButton.setAttribute("aria-label", "停止录音并识别");
    window.setTimeout(() => {
      if (state.recorder?.state === "recording") state.recorder.stop();
    }, 60000);
  } catch {
    window.alert("无法使用麦克风，请检查浏览器权限。");
  }
}

composer.addEventListener("submit", (event) => {
  event.preventDefault();
  sendQuery(input.value);
});

input.addEventListener("input", () => {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 132)}px`;
});

input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    composer.requestSubmit();
  }
});

document.querySelectorAll("[data-prompt]").forEach((button) => {
  button.addEventListener("click", () => sendQuery(button.dataset.prompt));
});
messages.addEventListener("click", (event) => {
  const link = event.target.closest("a[data-amap-navigation]");
  if (!link || !/MicroMessenger/i.test(navigator.userAgent)) return;
  window.alert("微信内可能无法直接唤起高德 App；若停留在当前页，请使用右上角菜单选择“在浏览器打开”。");
});
locationButton.addEventListener("click", locate);
clearChatButton.addEventListener("click", clearChatMemory);
voiceButton.addEventListener("click", toggleRecording);
locate();
