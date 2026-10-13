#!/usr/bin/env node
/* Logic-only tests for the published inline controls; not browser/visual QA. */
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
let tests = 0;
function ok(condition, message) { assert.ok(condition, message); tests++; }
function read(name) { return fs.readFileSync(path.join(root, name), 'utf8'); }
function script(html) {
  const found = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)];
  assert.equal(found.length, 1, 'one local inline behavior script');
  return found[0][1];
}
class Element {
  constructor() { this.value = ''; this.children = []; this.events = {}; this.hidden = false; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener(name, fn) { this.events[name] = fn; }
  fire(name) { assert.equal(typeof this.events[name], 'function'); this.events[name](); }
}
const searchHtml = read('search.html');
const data = JSON.parse(read('search-index.json'));
const searchNodes = Object.fromEntries(['#q', '#kind', '#results', '#count'].map(k => [k, new Element()]));
searchNodes['#kind'].value = 'current';
const document = { querySelector: k => { assert.ok(searchNodes[k], k); return searchNodes[k]; }, createElement: () => new Element() };
vm.runInNewContext(script(searchHtml), {document}, {timeout: 2000});
const historic = p => ['historical_record', 'retained_public_review'].includes(p.kind);
const norm = s => s.normalize('NFKC').toLowerCase();
function checkSearch(query, mode) {
  searchNodes['#q'].value = query; searchNodes['#kind'].value = mode;
  searchNodes['#q'].fire('input');
  const words = norm(query).trim().split(/\s+/).filter(Boolean);
  const expected = data.filter(p => (mode === 'all' || (mode === 'history' ? historic(p) : !historic(p))) && words.every(w => norm([p.title, p.desc, p.keywords, p.url].join(' ')).includes(w)));
  assert.deepEqual(searchNodes['#results'].children.map(li => li.children[0].href), expected.map(p => p.url));
  tests++;
  ok(searchNodes['#count'].textContent === expected.length + '件 / ' + data.length + '件', 'accurate search count');
  for (const li of searchNodes['#results'].children) ok(typeof li.children[0].textContent === 'string' && typeof li.children[1].textContent === 'string', 'labels use textContent');
}
for (const [q, mode] of [['', 'current'], ['', 'history'], ['', 'all'], ['Ｃｌａｕｄｅ', 'current'], ['ontology', 'current'], ['Gemini API', 'current'], ['no-such-knowledge-unique-zz', 'all']]) checkSearch(q, mode);
searchNodes['#kind'].fire('change');
ok(!/https?:\/\/[^"']+\.js/.test(searchHtml) && !/\bfetch\(/.test(script(searchHtml)), 'search has no remote JS or fetch dependency');
const ledger = JSON.parse(read('research/refresh-2026-10-13/model-ledger.json'));
const trackerHtml = read('research/llm-tracker.html');
const rows = [...trackerHtml.matchAll(/<tr id="([^"]+)" data-china="([^"]+)" data-stage="([^"]+)">([\s\S]*?)<\/tr>/g)].map(m => ({id: m[1], dataset: {china: m[2], stage: m[3]}, textContent: m[4].replace(/<[^>]*>/g, ' '), hidden: false}));
ok(rows.length === ledger.rows.length && rows.length === 22, 'tracker row count matches ledger');
for (const row of ledger.rows) {
  const actual = rows.find(r => r.id === row.key);
  ok(actual && actual.dataset.china === String(row.china) && actual.dataset.stage === row.stage, 'ledger identity/surface stage row: ' + row.key);
}
const trackerNodes = Object.fromEntries(['#query', '#scope', '#count'].map(k => [k, new Element()]));
trackerNodes['#scope'].value = 'all';
vm.runInNewContext(script(trackerHtml), {document: {querySelector: k => trackerNodes[k], querySelectorAll: () => rows}}, {timeout: 2000});
function checkTracker(scope, q, count) {
  trackerNodes['#scope'].value = scope; trackerNodes['#query'].value = q;
  trackerNodes['#scope'].fire('change');
  ok(rows.filter(r => !r.hidden).length === count, 'tracker filter: ' + scope + '/' + q);
  ok(trackerNodes['#count'].textContent === count + '行 / 22行', 'accurate tracker count');
}
checkTracker('all', '', 22); checkTracker('china', '', 13); checkTracker('gap', '', 4); checkTracker('partner', '', 1); checkTracker('all', 'grok', 1); checkTracker('all', 'no-such-model-zz', 0);
trackerNodes['#query'].fire('input');
console.log(JSON.stringify({passed_assertions: tests, indexed_pages: data.length, model_rows: rows.length, network_or_browser: false, visual_rendering: false}));
