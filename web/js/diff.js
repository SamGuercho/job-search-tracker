// Word-level diff of a proposed wording against the original, and the draft card every AI wording uses
// (variants, What · How · Result drafts, placements of a job's words, the chat's approval cards).
//   added words: green underline · removed: red strikethrough · replaced: old struck, new in yellow
//   the job's keyword: bold purple mark · swappable words: dotted underline, tap to choose another
import { h, mount, modal } from "./ui.js";

const TOKEN = /[\p{L}\p{N}+#&%~]+(?:['’./-][\p{L}\p{N}+#&%~]+)*|[^\s\p{L}\p{N}]/gu;

/** Tokens with their position and the spaces that follow them. */
function tokenize(s) {
  const out = [];
  for (const m of s.matchAll(TOKEN)) out.push({ text: m[0], start: m.index, end: m.index + m[0].length });
  out.forEach((t, i) => { t.trail = s.slice(t.end, i + 1 < out.length ? out[i + 1].start : s.length); });
  return out;
}

/** Longest-common-subsequence diff of two token lists: [{type: "eq"|"del"|"ins", a?, b?}] in order. */
function lcs(a, b) {
  const n = a.length, m = b.length;
  const key = (t) => t.text.toLowerCase();
  const dp = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = key(a[i]) === key(b[j]) ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const ops = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (key(a[i]) === key(b[j])) ops.push({ type: "eq", a: a[i++], b: b[j++] });
    else if (dp[i + 1][j] >= dp[i][j + 1]) ops.push({ type: "del", a: a[i++] });
    else ops.push({ type: "ins", b: b[j++] });
  }
  while (i < n) ops.push({ type: "del", a: a[i++] });
  while (j < m) ops.push({ type: "ins", b: b[j++] });
  return ops;
}

/** Character ranges of `needle` in `hay` (all of them, or the first only). */
function ranges(hay, needle, all = true) {
  const out = [];
  if (!needle) return out;
  const low = hay.toLowerCase(), n = needle.toLowerCase();
  for (let i = low.indexOf(n); i >= 0; i = all ? low.indexOf(n, i + n.length) : -1) out.push([i, i + n.length]);
  return out;
}

/** How many words changed, for the card's badge. */
export function changeCount(original, draft) {
  return lcs(tokenize(original), tokenize(draft)).filter((o) => o.type === "ins" && /[\p{L}\p{N}]/u.test(o.b.text)).length;
}

/**
 * The draft as DOM nodes. With `original`, changes are marked; `spans` are the keyword pieces to mark;
 * `swaps` [{phrase, options}] become tappable (onSwap(swap) is called on tap).
 */
export function renderDiff(original, draft, { spans = [], swaps = [], onSwap } = {}) {
  const b = tokenize(draft);
  const kw = spans.flatMap((s) => ranges(draft, s));
  const sw = swaps.map((s) => ({ swap: s, r: ranges(draft, s.phrase, false)[0] })).filter((x) => x.r);
  const inRange = (t, rs) => rs.some(([s, e]) => t.start < e && t.end > s);  // "LLM" marks the token "LLM-based"
  const swapOf = (t) => sw.find((x) => t.start >= x.r[0] && t.end <= x.r[1]);

  // Ops in draft order; replaced = removed words directly followed by added ones
  const ops = original != null ? lcs(tokenize(original), b) : b.map((t) => ({ type: "eq", b: t }));
  const items = [];
  for (let i = 0; i < ops.length; i++) {
    const o = ops[i];
    if (o.type === "del") {
      const dels = [o];
      while (ops[i + 1]?.type === "del") dels.push(ops[++i]);
      const replaced = ops[i + 1]?.type === "ins";
      items.push({ del: dels.map((d) => d.a.text + d.a.trail).join("").trim(), replaced });
      continue;
    }
    const prevDel = items.at(-1)?.del !== undefined && items.at(-1).replaced;
    const kind = o.type === "eq" ? "eq" : (prevDel || items.at(-1)?.kind === "rep" ? "rep" : "add");
    const s = swapOf(o.b);
    const isKw = inRange(o.b, kw);
    // the job's word is one mark, whether it was there already or partly added
    items.push({ kind: isKw ? "eq" : kind, token: o.b, kw: isKw, swap: s?.swap });
  }

  // Group neighbouring tokens that look the same into one element
  const nodes = [];
  let group = null;
  const flush = () => {
    if (!group) return;
    const cls = [group.kind !== "eq" && `d-${group.kind}`, group.kw && "kw", group.swap && "d-swap"].filter(Boolean).join(" ");
    const text = group.text;
    if (group.swap) {
      const swap = group.swap;
      nodes.push(h("button", { type: "button", class: cls, title: "Tap for other words", onclick: () => onSwap?.(swap) }, text));
    } else nodes.push(cls ? h(group.kw ? "mark" : "span", { class: cls }, text) : text);
    if (group.trail) nodes.push(group.trail);
    group = null;
  };
  for (const it of items) {
    if (it.del !== undefined) {
      flush();
      if (it.del) nodes.push(h("del", { class: it.replaced ? "d-old" : "d-del" }, it.del), it.replaced ? "" : " ");
      continue;
    }
    const same = group && group.kind === it.kind && group.kw === it.kw && group.swap === it.swap;
    if (same) { group.text += group.trail + it.token.text; group.trail = it.token.trail; } else {
      flush();
      group = { kind: it.kind, kw: it.kw, swap: it.swap, text: it.token.text, trail: it.token.trail };
    }
  }
  flush();
  return nodes;
}

/** Pick another word for a swappable phrase. Resolves the chosen text, or null. */
function chooseSwap(swap) {
  return new Promise((resolve) => {
    let done = false;
    const pick = (v) => { if (!done) { done = true; close(); resolve(v); } };
    const body = h("div", { class: "swap-sheet" },
      h("p", { class: "small muted" }, "Same meaning, same sentence: pick the word that sounds like you."),
      h("div", { class: "swap-options" },
        swap.options.map((o) => h("button", { type: "button", onclick: () => pick(o) }, o)),
        h("button", { type: "button", class: "ghost", onclick: () => pick(null) }, `Keep “${swap.phrase}”`)));
    const close = modal(`Instead of “${swap.phrase}”`, body, { onClose: () => pick(null) });
  });
}

/**
 * One AI draft with its changes highlighted.
 *   original: the text it rewrites ("" or null for a brand-new achievement)
 *   d: {lead, text, covers, spans, swaps, edit|order}; keywords: the target keywords
 *   actions: [{label, primary?, onclick(draft) -> truthy when done}]
 * Swapping a word changes d.text in place, so the actions save what the user sees.
 */
export function draftCard(original, d, { keywords = [], actions = [], note = "" } = {}) {
  const card = h("div", { class: "draft draft-card" });
  let showChanges = !!original;
  const draw = () => {
    const full = `${d.lead ? `${d.lead} ` : ""}${d.text}`;
    const onSwap = async (swap) => {
      const chosen = await chooseSwap(swap);
      if (!chosen) return;
      d.text = d.text.replace(swap.phrase, chosen);
      swap.options = [swap.phrase, ...swap.options.filter((o) => o !== chosen)];
      swap.phrase = chosen;
      draw();
    };
    const changed = original ? changeCount(original, full) : 0;
    const badge = d.order ? { "how-led": "What · How · Result", "result-first": "Result first", compact: "Compact" }[d.order]
      : d.edit === "swap" ? (changed <= 1 ? "1 word changed" : `${changed} words changed`)
        : d.edit === "restructure" ? "Reworded" : "";
    mount(card,
      h("div", { class: "draft-text", dir: "auto" },
        renderDiff(showChanges ? original : null, full, { spans: d.spans || [], swaps: d.swaps || [], onSwap })),
      h("div", { class: "row draft-meta" },
        badge && h("span", { class: "pill small" }, badge),
        h("span", { class: "chips small" },
          keywords.map((k) => (d.covers || []).includes(k)
            ? h("span", { class: "chip ok" }, `✓ ${k}`)
            : h("span", { class: "chip", title: "Not in this draft" }, k))),
        (d.swaps || []).length > 0 && h("span", { class: "small muted" }, "Tap a dotted word to swap it"),
        note && h("span", { class: "small muted" }, note),
        h("span", { class: "spacer" }),
        original && h("button", { class: "ghost small", onclick: () => { showChanges = !showChanges; draw(); } },
          showChanges ? "Plain" : "Show changes"),
        actions.map((a) => h("button", {
          class: `small${a.primary ? " primary" : ""}`, onclick: async (e) => {
            e.target.disabled = true;
            if (!(await a.onclick(d))) e.target.disabled = false;
          },
        }, a.label))));
  };
  draw();
  return card;
}

/** Inline legend for the colours (shown once above a list of drafts). */
export const diffLegend = () => h("div", { class: "diff-legend small muted" },
  h("mark", { class: "kw" }, "job's word"), h("span", { class: "d-add" }, "added"),
  h("span", { class: "d-rep" }, "replaced"), h("del", { class: "d-del" }, "removed"),
  h("span", { class: "d-swap-legend" }, "tap to swap"));
