// Minimal Markdown → DOM for model output: paragraphs, bullet/numbered lists, headings, **bold**,
// *italic*. Everything becomes text nodes; model output is never inserted as HTML.

import { h } from "./dom.js";

function inline(text) {
  const out = [];
  const re = /(\*\*[^*]+\*\*|\*[^*\s][^*]*\*|`[^`]+`)/g;
  let last = 0;
  for (const m of text.matchAll(re)) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith("**")) out.push(h("strong", {}, tok.slice(2, -2)));
    else if (tok.startsWith("`")) out.push(h("code", {}, tok.slice(1, -1)));
    else out.push(h("em", {}, tok.slice(1, -1)));
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export function renderMarkdown(src) {
  const root = h("div", { class: "md" });
  let list = null;
  let para = [];
  const flushPara = () => {
    if (para.length) root.append(h("p", {}, inline(para.join(" "))));
    para = [];
  };
  for (const raw of src.replace(/\r/g, "").split("\n")) {
    const line = raw.trim();
    const bullet = line.match(/^[-*•]\s+(.*)$/);
    const numbered = line.match(/^\d+[.)]\s+(.*)$/);
    const heading = line.match(/^#{1,6}\s+(.*)$/);
    if (bullet || numbered) {
      flushPara();
      const tag = bullet ? "ul" : "ol";
      if (!list || list.tagName.toLowerCase() !== tag) { list = h(tag); root.append(list); }
      list.append(h("li", {}, inline((bullet || numbered)[1])));
      continue;
    }
    list = null;
    if (heading) { flushPara(); root.append(h("h3", {}, inline(heading[1]))); continue; }
    if (!line) { flushPara(); continue; }
    para.push(line);
  }
  flushPara();
  return root;
}
