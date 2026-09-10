const LEGACY_CHAT_KEY = "nearbygo-chat-memory-v1";
const SESSIONS_KEY = "nearbygo-sessions-v1";
const RADIUS_KEY = "nearbygo-radius";
const SETTINGS_KEY = "nearbygo-settings-v1";
const MAX_SAVED_MESSAGES = 24;
const MAX_SAVED_MESSAGE_LENGTH = 8000;
const MAX_SAVED_SESSIONS = 30;

function sanitizeHistory(history) {
  return Array.isArray(history)
    ? history
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
}

function migrateLegacyChat() {
  if (localStorage.getItem(SESSIONS_KEY)) return [];
  try {
    const legacy = JSON.parse(localStorage.getItem(LEGACY_CHAT_KEY) || "null");
    if (legacy && typeof legacy === "object" && (legacy.history?.length || legacy.conversationId)) {
      const migrated = [
        {
          id: crypto.randomUUID(),
          title: "历史对话",
          conversationId: typeof legacy.conversationId === "string" ? legacy.conversationId : "",
          history: sanitizeHistory(legacy.history),
          updatedAt: Date.now(),
        },
      ];
      localStorage.setItem(
        SESSIONS_KEY,
        JSON.stringify({ sessions: migrated, activeId: migrated[0].id }),
      );
      return migrated;
    }
  } catch {
    // Corrupted legacy data is simply dropped.
  }
  return [];
}

function loadSessions() {
  const migrated = migrateLegacyChat();
  if (migrated.length) return { sessions: migrated, activeId: migrated[0].id };
  try {
    const saved = JSON.parse(localStorage.getItem(SESSIONS_KEY) || "null");
    if (!saved || !Array.isArray(saved.sessions)) return { sessions: [], activeId: "" };
    const sessions = saved.sessions
      .filter((session) => session && typeof session.id === "string")
      .map((session) => ({
        id: session.id,
        title: typeof session.title === "string" ? session.title.slice(0, 40) : "",
        conversationId:
          typeof session.conversationId === "string" ? session.conversationId : "",
        history: sanitizeHistory(session.history),
        updatedAt: typeof session.updatedAt === "number" ? session.updatedAt : 0,
      }))
      .sort((a, b) => b.updatedAt - a.updatedAt)
      .slice(0, MAX_SAVED_SESSIONS);
    const activeId = sessions.some((session) => session.id === saved.activeId)
      ? saved.activeId
      : (sessions[0]?.id || "");
    return { sessions, activeId };
  } catch {
    localStorage.removeItem(SESSIONS_KEY);
    return { sessions: [], activeId: "" };
  }
}

function persistSessions() {
  try {
    localStorage.setItem(SESSIONS_KEY, JSON.stringify(sessionStore));
  } catch {
    // Storage can be unavailable or full in private/in-app browsers.
  }
}

const sessionStore = loadSessions();

function currentSession() {
  let session = sessionStore.sessions.find((item) => item.id === sessionStore.activeId);
  if (!session) {
    session = {
      id: crypto.randomUUID(),
      title: "",
      conversationId: "",
      history: [],
      updatedAt: Date.now(),
    };
    sessionStore.sessions.unshift(session);
    sessionStore.activeId = session.id;
    persistSessions();
  }
  return session;
}

const state = {
  position: null,
  user: localStorage.getItem("nearbygo-user") || crypto.randomUUID(),
  radius: localStorage.getItem(RADIUS_KEY) || "",
  busy: false,
};
localStorage.setItem("nearbygo-user", state.user);

const messages = document.querySelector("#messages");
const composer = document.querySelector("#composer");
const input = document.querySelector("#queryInput");
const sendButton = document.querySelector("#sendButton");
const clearChatButton = document.querySelector("#clearChatButton");
const locationButton = document.querySelector("#locationButton");
const locationLabel = document.querySelector("#locationLabel");
const welcomeMessage = messages.firstElementChild.cloneNode(true);
const historyButton = document.querySelector("#historyButton");
const historyPanel = document.querySelector("#historyPanel");
const historyList = document.querySelector("#historyList");
const closeHistoryButton = document.querySelector("#closeHistoryPanel");
const newChatButton = document.querySelector("#newChatButton");
const locationPanel = document.querySelector("#locationPanel");
const closeLocationButton = document.querySelector("#closeLocationPanel");
const placeSearchInput = document.querySelector("#placeSearchInput");
const placeSearchResults = document.querySelector("#placeSearchResults");
const useBrowserLocationButton = document.querySelector("#useBrowserLocation");
const settingsButton = document.querySelector("#settingsButton");
const settingsPanel = document.querySelector("#settingsPanel");
const closeSettingsButton = document.querySelector("#closeSettingsPanel");
const prefInput = document.querySelector("#prefInput");
const restrictInput = document.querySelector("#restrictInput");
const transportSelect = document.querySelector("#transportSelect");
const mobilitySelect = document.querySelector("#mobilitySelect");
const vehicleSelect = document.querySelector("#vehicleSelect");
const notesInput = document.querySelector("#notesInput");

const { escapeHtml, renderMarkdown } = window.NearbyGoMarkdown;

function formatAnswer(value) {
  return renderMarkdown(value);
}

const DATA_COMMENT_PATTERN = /<!--NEARBYGO-DATA:([\s\S]*?)-->/g;

function extractAnswerData(text) {
  const source = String(text || "");
  const match = source.match(/<!--NEARBYGO-DATA:([\s\S]*?)-->/);
  let data = null;
  if (match) {
    try {
      const parsed = JSON.parse(match[1]);
      if (parsed && typeof parsed === "object") data = parsed;
    } catch {
      data = null;
    }
  }
  return { data, text: source.replace(DATA_COMMENT_PATTERN, "") };
}

function stripStreamingData(text) {
  let result = String(text || "").replace(DATA_COMMENT_PATTERN, "");
  const open = result.indexOf("<!--NEARBYGO-DATA:");
  if (open >= 0) result = result.slice(0, open);
  return result;
}

function renderAnswerBubble(bubble, text) {
  const { data, text: clean } = extractAnswerData(text);
  bubble.innerHTML = formatAnswer(clean);
  try {
    enhancePlaceCards(bubble, data);
  } catch {
    // 增强失败时保留原始 markdown 渲染结果，不阻塞回答展示
  }
  try {
    decorateThumbs(bubble);
  } catch {
    // 同上，缩略图徽标失败不阻塞
  }
}

function placeChips(place) {
  if (!place || typeof place !== "object") return "";
  const parts = [];
  if (typeof place.rating === "number") parts.push(`★ ${place.rating.toFixed(1)}`);
  if (typeof place.cost_per_person === "number") parts.push(`人均 ¥${Math.round(place.cost_per_person)}`);
  if (typeof place.straight_distance_meters === "number") {
    parts.push(`${Math.round(place.straight_distance_meters)}m`);
  }
  return parts.join(" · ");
}

function travelLine(place, transport) {
  if (!place || typeof place.straight_distance_meters !== "number") return null;
  const meters = Math.round(place.straight_distance_meters);
  const driving = transport === "driving";
  const speed = driving ? 500 : 80;
  const minutes = Math.max(1, Math.round(meters / speed));
  let suggestion;
  if (driving) suggestion = `驾车约 ${minutes} 分钟`;
  else if (meters <= 1000) suggestion = `步行约 ${minutes} 分钟即可`;
  else if (meters <= 3000) suggestion = `步行约 ${minutes} 分钟，偏远可骑行或驾车`;
  else suggestion = `步行较远（约 ${minutes} 分钟），建议骑行、驾车或公交`;
  const line = document.createElement("p");
  line.className = "travel-hint";
  const label = document.createElement("b");
  label.textContent = "到达方式：";
  line.append(label, document.createTextNode(`直线约 ${meters} 米，${suggestion}。`));
  if (place.navigation_url && String(place.navigation_url).startsWith("https://")) {
    const link = document.createElement("a");
    link.href = place.navigation_url;
    link.target = "_blank";
    link.rel = "noopener";
    link.className = "travel-nav-link";
    link.textContent = "高德规划路线";
    line.append(document.createTextNode(" "), link);
  }
  return line;
}

function foldSection(heading, summaryLabel) {
  const details = document.createElement("details");
  details.className = "section-fold";
  const summary = document.createElement("summary");
  summary.textContent = summaryLabel || heading.textContent.trim();
  const body = document.createElement("div");
  body.className = "section-fold-body";
  details.append(summary, body);
  return details;
}

function estimateTravelText(meters, transport) {
  const driving = transport === "driving";
  const minutes = Math.max(1, Math.round(meters / (driving ? 500 : 80)));
  return `${meters}m · ${driving ? "驾车" : "步行"}约${minutes}分钟`;
}

function buildComparisonTable(places, itinerary, transport) {
  const wrap = document.createElement("div");
  wrap.className = "table-wrap";
  const table = document.createElement("table");
  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  ["排名", "推荐", "评分", "人均", "距离·时间"].forEach((text) => {
    const th = document.createElement("th");
    th.textContent = text;
    headRow.append(th);
  });
  thead.append(headRow);
  const tbody = document.createElement("tbody");
  places.forEach((place, index) => {
    if (!place || typeof place !== "object") return;
    const tr = document.createElement("tr");
    const rank = document.createElement("td");
    rank.textContent = String(place.index || index + 1);
    const name = document.createElement("td");
    if (place.navigation_url && String(place.navigation_url).startsWith("https://")) {
      const link = document.createElement("a");
      link.href = place.navigation_url;
      link.target = "_blank";
      link.rel = "noopener";
      link.className = "amap-navigation";
      link.textContent = place.name || "附近地点";
      name.append(link);
    } else {
      name.textContent = place.name || "附近地点";
    }
    const rating = document.createElement("td");
    rating.textContent = typeof place.rating === "number" ? place.rating.toFixed(1) : "—";
    const cost = document.createElement("td");
    cost.textContent =
      typeof place.cost_per_person === "number" ? `¥${Math.round(place.cost_per_person)}` : "—";
    const distance = document.createElement("td");
    const segment = Array.isArray(itinerary) ? itinerary[index] : null;
    if (segment && typeof segment.route_distance_meters === "number") {
      distance.textContent =
        typeof segment.route_duration_minutes === "number"
          ? `${Math.round(segment.route_distance_meters)}m · ${segment.route_duration_minutes}分钟`
          : `${Math.round(segment.route_distance_meters)}m`;
    } else if (typeof place.straight_distance_meters === "number") {
      distance.textContent = estimateTravelText(
        Math.round(place.straight_distance_meters),
        transport,
      );
    } else {
      distance.textContent = "—";
    }
    tr.append(rank, name, rating, cost, distance);
    tbody.append(tr);
  });
  table.append(thead, tbody);
  wrap.append(table);
  return wrap;
}

function enhancePlaceCards(bubble, data) {
  const places = data && Array.isArray(data.places) ? data.places : [];
  const transport = data && typeof data.transport === "string" ? data.transport : "walking";
  const isCardHead = (el) =>
    el.tagName === "H3" &&
    /^(?:推荐)?\s*[①②③④⑤0-9]{1,2}\s*[·.、:：]?\s*\S/.test(el.textContent.trim());
  const isFoldHead = (el) =>
    el.tagName === "H3" && /^(对比一览|其他候选)/.test(el.textContent.trim());

  const children = [...bubble.children];
  const firstIndex = children.findIndex((el) => isCardHead(el) || isFoldHead(el));
  if (firstIndex < 0) return;

  const groups = [];
  for (const el of children.slice(firstIndex)) {
    if (isCardHead(el) || isFoldHead(el)) {
      groups.push({ head: el, body: [] });
    } else if (["H2", "HR"].includes(el.tagName)) {
      // H2/HR 是区块分隔（如"高德位置概览"在决策卡之后）：保留原位，不终止后续折叠
      continue;
    } else if (groups.length) {
      groups[groups.length - 1].body.push(el);
    }
  }

  groups.forEach((group, groupIndex) => {
    const number = parseInt(group.head.textContent.trim(), 10);
    const place = Number.isInteger(number) ? places[number - 1] : null;

    let details;
    if (isCardHead(group.head)) {
      details = document.createElement("details");
      details.className = "place-card";
      if (groupIndex === 0) details.open = true;
      const summary = document.createElement("summary");
      const title = document.createElement("span");
      title.className = "place-card-title";
      title.textContent = group.head.textContent.trim();
      const chips = document.createElement("span");
      chips.className = "place-card-chips";
      chips.textContent = placeChips(place);
      summary.append(title, chips);
      const body = document.createElement("div");
      body.className = "place-card-body";
      const travel = travelLine(place, transport);
      if (travel) body.append(travel);
      group.body.forEach((el) => body.append(el));
      details.append(summary, body);
    } else {
      details = foldSection(group.head);
      const foldBody = details.querySelector(".section-fold-body");
      group.body.forEach((el) => foldBody.append(el));
      if (/^对比一览\s*$/.test(group.head.textContent.trim()) && places.length) {
        foldBody.querySelectorAll(".table-wrap").forEach((el) => el.remove());
        foldBody.prepend(
          buildComparisonTable(places, (data && data.itinerary) || [], transport),
        );
      }
    }
    group.head.replaceWith(details);
  });
}

function decorateThumbs(bubble) {
  bubble.querySelectorAll("img.answer-visual-thumb").forEach((img) => {
    const match = String(img.alt || "").match(/^(\d+)\s*[·.、]\s*/);
    if (!match || !img.parentElement || img.parentElement.classList.contains("thumb-wrap")) return;
    const wrap = document.createElement("span");
    wrap.className = "thumb-wrap";
    const badge = document.createElement("b");
    badge.className = "thumb-badge";
    badge.textContent = match[1];
    img.replaceWith(wrap);
    wrap.append(badge, img);
  });
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
  if (role === "assistant") {
    renderAnswerBubble(bubble, text);
  } else {
    bubble.innerHTML = `<p>${escapeHtml(text)}</p>`;
  }
  article.append(bubble);
  messages.append(article);
  messages.scrollTop = messages.scrollHeight;
  return bubble;
}

function rememberTurn(query, answer) {
  const session = currentSession();
  session.history.push(
    { role: "user", text: query.slice(0, MAX_SAVED_MESSAGE_LENGTH) },
    { role: "assistant", text: answer.slice(0, MAX_SAVED_MESSAGE_LENGTH) },
  );
  session.history = session.history.slice(-MAX_SAVED_MESSAGES);
  if (!session.title) session.title = query.replace(/\s+/g, " ").trim().slice(0, 24) || "新对话";
  session.updatedAt = Date.now();
  sessionStore.sessions.sort((a, b) => b.updatedAt - a.updatedAt);
  persistSessions();
}

function restoreChat() {
  const session = currentSession();
  if (!session.history.length) return;
  messages.replaceChildren();
  session.history.forEach(({ role, text }) => addMessage(role, text));
  normalizeVisualImages();
}

function normalizeVisualImages() {
  document.querySelectorAll("img.answer-visual").forEach((img) => {
    if (img.classList.contains("loaded") || img.classList.contains("error")) return;
    if (img.complete) {
      img.classList.add(img.naturalWidth ? "loaded" : "error");
    } else {
      img.classList.add("loading");
    }
  });
}

function handleVisualImageEvent(event) {
  const img = event.target;
  if (!(img instanceof HTMLImageElement) || !img.classList.contains("answer-visual")) return;
  if (event.type === "error") {
    img.classList.remove("loading", "loaded");
    img.classList.add("error");
  } else {
    img.classList.remove("loading", "error");
    img.classList.add("loaded");
  }
}

function startNewSession() {
  sessionStore.activeId = "";
  persistSessions();
  currentSession();
  messages.replaceChildren(welcomeMessage.cloneNode(true));
}

function deleteSession(id) {
  const index = sessionStore.sessions.findIndex((session) => session.id === id);
  if (index < 0) return;
  sessionStore.sessions.splice(index, 1);
  if (sessionStore.activeId === id) {
    sessionStore.activeId = sessionStore.sessions[0]?.id || "";
    persistSessions();
    messages.replaceChildren(welcomeMessage.cloneNode(true));
    restoreChat();
  } else {
    persistSessions();
  }
  renderHistoryList();
}

function switchSession(id) {
  if (sessionStore.activeId === id) {
    closePanels();
    return;
  }
  sessionStore.activeId = id;
  persistSessions();
  messages.replaceChildren(welcomeMessage.cloneNode(true));
  restoreChat();
  renderHistoryList();
  closePanels();
}

function formatSessionTime(timestamp) {
  if (!timestamp) return "";
  const date = new Date(timestamp);
  const now = new Date();
  const sameDay = date.toDateString() === now.toDateString();
  const time = `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
  return sameDay ? time : `${date.getMonth() + 1}/${date.getDate()} ${time}`;
}

function renderHistoryList() {
  historyList.replaceChildren();
  if (!sessionStore.sessions.length) {
    const empty = document.createElement("li");
    empty.className = "history-empty";
    empty.textContent = "还没有历史对话";
    historyList.append(empty);
    return;
  }
  [...sessionStore.sessions]
    .sort((a, b) => b.updatedAt - a.updatedAt)
    .forEach((session) => {
      const item = document.createElement("li");
      if (session.id === sessionStore.activeId) item.classList.add("active");
      const main = document.createElement("button");
      main.type = "button";
      main.className = "history-item-main";
      const title = document.createElement("span");
      title.className = "history-item-title";
      title.textContent = session.title || "未命名对话";
      const time = document.createElement("span");
      time.className = "history-item-time";
      time.textContent = formatSessionTime(session.updatedAt);
      main.append(title, time);
      main.addEventListener("click", () => switchSession(session.id));
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "history-item-delete";
      remove.textContent = "删除";
      remove.setAttribute("aria-label", `删除对话：${session.title || "未命名对话"}`);
      remove.addEventListener("click", (event) => {
        event.stopPropagation();
        deleteSession(session.id);
      });
      item.append(main, remove);
      historyList.append(item);
    });
}

function loadSettings() {
  try {
    const parsed = JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}");
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function readSettingsForm() {
  const splitList = (value) =>
    String(value || "")
      .split(/[,，、;；]/)
      .map((item) => item.trim())
      .filter(Boolean)
      .slice(0, 8);
  return {
    preferences: splitList(prefInput.value),
    restrictions: splitList(restrictInput.value),
    transport: transportSelect.value,
    mobility: mobilitySelect.value,
    vehicle: vehicleSelect.value,
    notes: notesInput.value.trim().slice(0, 120),
  };
}

function saveSettings() {
  const settings = readSettingsForm();
  localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
  return settings;
}

function buildProfilePayload() {
  const settings = loadSettings();
  const profile = {};
  if (settings.preferences?.length) profile.preferences = settings.preferences;
  if (settings.restrictions?.length) profile.restrictions = settings.restrictions;
  if (settings.transport) profile.transport = settings.transport;
  if (settings.mobility && settings.mobility !== "normal") profile.mobility = settings.mobility;
  if (settings.vehicle) profile.vehicle = settings.vehicle;
  if (settings.notes) profile.notes = settings.notes;
  return Object.keys(profile).length ? profile : null;
}

function closePanels() {
  historyPanel.classList.add("hidden");
  locationPanel.classList.add("hidden");
  settingsPanel.classList.add("hidden");
}

function togglePanel(panel) {
  const willOpen = panel.classList.contains("hidden");
  closePanels();
  if (willOpen) {
    panel.classList.remove("hidden");
    if (panel === historyPanel) renderHistoryList();
    if (panel === locationPanel) {
      placeSearchInput.focus();
      if (!placeSearchResults.childElementCount) renderPlaceSuggestions([]);
    }
    if (panel === settingsPanel) prefInput.focus();
  }
}

function fillSettingsForm() {
  const settings = loadSettings();
  prefInput.value = (settings.preferences || []).join("、");
  restrictInput.value = (settings.restrictions || []).join("、");
  transportSelect.value = settings.transport || "";
  mobilitySelect.value = settings.mobility || "";
  vehicleSelect.value = settings.vehicle || "";
  notesInput.value = settings.notes || "";
}

restoreChat();

function applyManualPlace(place) {
  state.position = {
    longitude: place.longitude,
    latitude: place.latitude,
    coordinate_system: "autonavi",
    name: place.name,
  };
  locationButton.className = "location-button ready";
  locationLabel.textContent = place.name.length > 10 ? `${place.name.slice(0, 10)}…` : place.name;
  closePanels();
}

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
        coordinate_system: "gps",
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
  if (event.conversation_id) currentSession().conversationId = event.conversation_id;
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
        ...(state.position
          ? {
              longitude: state.position.longitude,
              latitude: state.position.latitude,
              ...(state.position.accuracy ? { accuracy: state.position.accuracy } : {}),
              coordinate_system: state.position.coordinate_system || "gps",
              ...(state.position.name ? { position_name: state.position.name } : {}),
            }
          : {}),
        ...(state.radius ? { radius_meters: Number(state.radius) } : {}),
        ...(buildProfilePayload() ? { profile: buildProfilePayload() } : {}),
        conversation_id: currentSession().conversationId,
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
        answer = stripStreamingData(stripReasoning(rawAnswer));
        if (answer) {
          answerBubble.classList.remove("typing");
          answerBubble.innerHTML = formatAnswer(answer);
        }
        messages.scrollTop = messages.scrollHeight;
      }
      if (done) break;
    }
    const finalAnswer = stripReasoning(rawAnswer).trim();
    if (!finalAnswer) {
      answerBubble.classList.remove("typing");
      answerBubble.innerHTML = "<p>暂时没有取得推荐，请稍后重试。</p>";
    } else {
      answerBubble.classList.remove("typing");
      renderAnswerBubble(answerBubble, finalAnswer);
      rememberTurn(query, finalAnswer);
      messages.scrollTop = messages.scrollHeight;
    }
  } catch (error) {
    answerBubble.classList.remove("typing");
    answerBubble.innerHTML = `<p>请求失败：${escapeHtml(error.message)}</p>`;
  } finally {
    state.busy = false;
    sendButton.disabled = false;
    clearChatButton.disabled = false;
    persistSessions();
    input.focus();
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

const radiusButtons = [...document.querySelectorAll(".radius-picker [data-radius]")];

function refreshRadiusButtons() {
  radiusButtons.forEach((button) => {
    button.classList.toggle("active", button.dataset.radius === state.radius);
  });
}

radiusButtons.forEach((button) => {
  button.addEventListener("click", () => {
    state.radius = button.dataset.radius;
    localStorage.setItem(RADIUS_KEY, state.radius);
    refreshRadiusButtons();
  });
});
refreshRadiusButtons();
function renderPlaceSuggestions(tips, message = "") {
  placeSearchResults.replaceChildren();
  if (message) {
    const empty = document.createElement("li");
    empty.className = "place-empty";
    empty.textContent = message;
    placeSearchResults.append(empty);
    return;
  }
  if (!tips.length) {
    const empty = document.createElement("li");
    empty.className = "place-empty";
    empty.textContent = "输入地点名称开始搜索";
    placeSearchResults.append(empty);
    return;
  }
  tips.forEach((tip) => {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "place-item";
    const name = document.createElement("span");
    name.className = "place-name";
    name.textContent = tip.name;
    const address = document.createElement("span");
    address.className = "place-address";
    address.textContent = tip.address || tip.district || "";
    button.append(name, address);
    button.addEventListener("click", () => applyManualPlace(tip));
    item.append(button);
    placeSearchResults.append(item);
  });
}

let placeSearchTimer = null;
let placeSearchSeq = 0;

async function searchPlaces(keyword) {
  const trimmed = keyword.trim();
  if (!trimmed) {
    renderPlaceSuggestions([]);
    return;
  }
  const seq = ++placeSearchSeq;
  try {
    const response = await fetch(`/api/place-search?query=${encodeURIComponent(trimmed.slice(0, 50))}`, {
      headers: { "X-NearbyGo-User": state.user },
    });
    const payload = await response.json().catch(() => ({}));
    if (seq !== placeSearchSeq) return;
    if (!response.ok) {
      renderPlaceSuggestions([], payload.detail || `搜索失败（${response.status}）`);
      return;
    }
    renderPlaceSuggestions(Array.isArray(payload.tips) ? payload.tips : []);
  } catch {
    if (seq === placeSearchSeq) renderPlaceSuggestions([], "搜索失败，请检查网络");
  }
}

placeSearchInput.addEventListener("input", () => {
  window.clearTimeout(placeSearchTimer);
  placeSearchTimer = window.setTimeout(() => searchPlaces(placeSearchInput.value), 350);
});

placeSearchInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    window.clearTimeout(placeSearchTimer);
    searchPlaces(placeSearchInput.value);
  }
});

document.addEventListener("click", (event) => {
  if (
    event.target.closest(
      "#locationPanel, #locationButton, #historyPanel, #historyButton, #settingsPanel, #settingsButton",
    )
  ) {
    return;
  }
  closePanels();
});

messages.addEventListener("click", (event) => {
  const link = event.target.closest("a[data-amap-navigation]");
  if (!link || !/MicroMessenger/i.test(navigator.userAgent)) return;
  window.alert("微信内可能无法直接唤起高德 App；若停留在当前页，请使用右上角菜单选择“在浏览器打开”。");
});
historyButton.addEventListener("click", () => togglePanel(historyPanel));
closeHistoryButton.addEventListener("click", closePanels);
newChatButton.addEventListener("click", () => {
  startNewSession();
  renderHistoryList();
});
locationButton.addEventListener("click", () => togglePanel(locationPanel));
closeLocationButton.addEventListener("click", closePanels);
settingsButton.addEventListener("click", () => {
  fillSettingsForm();
  togglePanel(settingsPanel);
});
closeSettingsButton.addEventListener("click", () => closePanels());
[prefInput, restrictInput, transportSelect, mobilitySelect, vehicleSelect, notesInput].forEach(
  (element) => {
    element.addEventListener("change", saveSettings);
    element.addEventListener("blur", saveSettings);
  },
);
useBrowserLocationButton.addEventListener("click", () => {
  closePanels();
  locate();
});
clearChatButton.addEventListener("click", startNewSession);
messages.addEventListener("load", handleVisualImageEvent, true);
messages.addEventListener("error", handleVisualImageEvent, true);
locate();
