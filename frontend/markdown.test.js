const test = require("node:test");
const assert = require("node:assert/strict");

const { renderMarkdown } = require("./markdown.js");

test("renders the supported Markdown block and inline subset", () => {
  const html = renderMarkdown([
    "# 一级",
    "## 二级",
    "### **三级**",
    "#### 详细步骤",
    "",
    "普通段落  ",
    "换行",
    "",
    "- 无序一",
    "- **无序二**",
    "",
    "1. 有序一",
    "2. 有序二",
    "",
    "> 引用内容",
    "",
    "---",
  ].join("\n"));

  assert.match(html, /<h1>一级<\/h1>/);
  assert.match(html, /<h2>二级<\/h2>/);
  assert.match(html, /<h3><strong>三级<\/strong><\/h3>/);
  assert.match(html, /<h3>详细步骤<\/h3>/);
  assert.match(html, /<p>普通段落  <br>换行<\/p>/);
  assert.match(html, /<ul><li>无序一<\/li><li><strong>无序二<\/strong><\/li><\/ul>/);
  assert.match(html, /<ol><li>有序一<\/li><li>有序二<\/li><\/ol>/);
  assert.match(html, /<blockquote><p>引用内容<\/p><\/blockquote>/);
  assert.match(html, /<hr>/);
});

test("tolerates an incomplete heading while streaming", () => {
  assert.equal(renderMarkdown("### "), "");
  assert.equal(renderMarkdown("### 完整标题"), "<h3>完整标题</h3>");
});

test("escapes untrusted HTML and rejects non-HTTPS links", () => {
  const html = renderMarkdown('<img src=x onerror=alert(1)> **安全** [坏链接](javascript:alert(1))');

  assert.ok(!html.includes("<img"));
  assert.ok(!html.includes('href="javascript:'));
  assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/);
  assert.match(html, /<strong>安全<\/strong>/);
});

test("marks an encoded AMap HTTPS navigation URL as a prominent navigation link", () => {
  const url = "https://uri.amap.com/navigation?to=116.3%2C40.0%2C%E6%B5%8B%E8%AF%95&mode=walk&coordinate=gaode&callnative=1";
  const html = renderMarkdown(`[打开高德导航](${url})`);

  assert.match(html, /class="amap-navigation"/);
  assert.match(html, /data-amap-navigation="true"/);
  assert.match(html, /target="_blank" rel="noopener noreferrer"/);
  assert.match(html, /coordinate=gaode/);
  assert.match(html, /callnative=1/);
});

test("renders only HTTPS Markdown images as lazy visual cards", () => {
  const html = renderMarkdown("![附近地图](https://guide.example.com/api/route-map?sig=safe)\n![坏图](javascript:alert(1))");

  assert.match(html, /class="answer-visual answer-visual-thumb"/);
  assert.match(html, /loading="lazy"/);
  assert.match(html, /https:\/\/guide\.example\.com\/api\/route-map/);
  assert.ok(!html.includes('src="javascript:'));
});

test("marks map: prefixed image alt text as a large map visual", () => {
  const html = renderMarkdown("![map:附近候选与实际路线示意](https://guide.example.com/api/route-map?sig=safe)");

  assert.match(html, /class="answer-visual answer-visual-map"/);
  assert.match(html, /alt="附近候选与实际路线示意"/);
  assert.ok(!html.includes("map:"));
});
