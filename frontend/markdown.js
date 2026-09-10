(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.NearbyGoMarkdown = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function safeHttpsUrl(value) {
    try {
      const url = new URL(value);
      return url.protocol === "https:" ? url.href : "";
    } catch {
      return "";
    }
  }

  function isAmapNavigation(url) {
    try {
      const parsed = new URL(url);
      return parsed.hostname === "uri.amap.com" && parsed.pathname === "/navigation";
    } catch {
      return false;
    }
  }

  function renderInline(value) {
    const source = String(value);
    let output = "";
    let cursor = 0;

    while (cursor < source.length) {
      if (source.startsWith("![", cursor)) {
        const labelEnd = source.indexOf("](", cursor + 2);
        const urlEnd = labelEnd >= 0 ? source.indexOf(")", labelEnd + 2) : -1;
        if (labelEnd > cursor + 2 && urlEnd > labelEnd + 2) {
          const url = safeHttpsUrl(source.slice(labelEnd + 2, urlEnd));
          if (url) {
            let alt = source.slice(cursor + 2, labelEnd);
            const isMap = alt.startsWith("map:");
            if (isMap) alt = alt.slice(4);
            const visualClass = isMap ? "answer-visual answer-visual-map" : "answer-visual answer-visual-thumb";
            output += `<img class="${visualClass}" src="${escapeHtml(url)}" alt="${escapeHtml(alt)}" loading="lazy">`;
            cursor = urlEnd + 1;
            continue;
          }
        }
      }

      if (source.startsWith("**", cursor)) {
        const end = source.indexOf("**", cursor + 2);
        if (end > cursor + 2) {
          output += `<strong>${renderInline(source.slice(cursor + 2, end))}</strong>`;
          cursor = end + 2;
          continue;
        }
      }

      if (source[cursor] === "[") {
        const labelEnd = source.indexOf("](", cursor + 1);
        const urlEnd = labelEnd >= 0 ? source.indexOf(")", labelEnd + 2) : -1;
        if (labelEnd > cursor + 1 && urlEnd > labelEnd + 2) {
          const url = safeHttpsUrl(source.slice(labelEnd + 2, urlEnd));
          if (url) {
            const amap = isAmapNavigation(url);
            const className = amap ? ' class="amap-navigation" data-amap-navigation="true"' : "";
            output += `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer"${className}>${renderInline(source.slice(cursor + 1, labelEnd))}</a>`;
            cursor = urlEnd + 1;
            continue;
          }
        }
      }

      output += escapeHtml(source[cursor]);
      cursor += 1;
    }
    return output;
  }

  function blockType(line) {
    if (/^#{1,6}[ \t]+/.test(line)) return "heading";
    if (/^[ \t]*(?:-{3,}|\*{3,}|_{3,})[ \t]*$/.test(line)) return "horizontal_rule";
    if (/^>[ \t]?/.test(line)) return "quote";
    if (/^[ \t]*[-+*][ \t]+/.test(line)) return "unordered";
    if (/^[ \t]*\d+\.[ \t]+/.test(line)) return "ordered";
    return "paragraph";
  }

  function renderMarkdown(value) {
    const lines = String(value || "").replaceAll("\r\n", "\n").replaceAll("\r", "\n").split("\n");
    const blocks = [];
    let index = 0;

    while (index < lines.length) {
      const line = lines[index];
      if (!line.trim()) {
        index += 1;
        continue;
      }

      const kind = blockType(line);
      if (kind === "heading") {
        const match = line.match(/^(#{1,6})[ \t]+(.+?)\s*$/);
        if (!match) {
          index += 1;
          continue;
        }
        const level = Math.min(match[1].length, 3);
        blocks.push(`<h${level}>${renderInline(match[2])}</h${level}>`);
        index += 1;
        continue;
      }

      if (kind === "horizontal_rule") {
        blocks.push("<hr>");
        index += 1;
        continue;
      }

      if (kind === "quote") {
        const quoteLines = [];
        while (index < lines.length && /^>[ \t]?/.test(lines[index])) {
          quoteLines.push(lines[index].replace(/^>[ \t]?/, ""));
          index += 1;
        }
        blocks.push(`<blockquote>${renderMarkdown(quoteLines.join("\n"))}</blockquote>`);
        continue;
      }

      if (kind === "unordered" || kind === "ordered") {
        const tag = kind === "ordered" ? "ol" : "ul";
        const pattern = kind === "ordered" ? /^[ \t]*\d+\.[ \t]+(.*)$/ : /^[ \t]*[-+*][ \t]+(.*)$/;
        const items = [];
        while (index < lines.length) {
          const match = lines[index].match(pattern);
          if (!match) break;
          items.push(`<li>${renderInline(match[1])}</li>`);
          index += 1;
        }
        blocks.push(`<${tag}>${items.join("")}</${tag}>`);
        continue;
      }

      const paragraph = [];
      while (index < lines.length && lines[index].trim() && blockType(lines[index]) === "paragraph") {
        paragraph.push(renderInline(lines[index]));
        index += 1;
      }
      blocks.push(`<p>${paragraph.join("<br>")}</p>`);
    }

    return blocks.join("");
  }

  return { escapeHtml, isAmapNavigation, renderInline, renderMarkdown, safeHttpsUrl };
});
