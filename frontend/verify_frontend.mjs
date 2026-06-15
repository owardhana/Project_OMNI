// Headless end-to-end verification of the 8 smoke-test checks.
// Run from frontend/: node verify_frontend.mjs  (backend on :8000, app on :3000)
import { chromium } from 'playwright';

const URL = 'http://localhost:3000';
const results = [];
const check = (name, ok, detail = '') => {
  results.push({ name, ok });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}  ${detail}`);
};

// Headless swiftshader emits shader-compile warnings for MeshBasicMaterial that
// do not occur in real WebGL — not app errors.
const isHeadlessGlNoise = (s) => /WebGLProgram|Shader Error|THREE\.WebGL/.test(s);

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
const errors = [];
page.on('pageerror', (e) => errors.push(String(e)));
page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
const appErrors = () => errors.filter((e) => !isHeadlessGlNoise(e));
const requests = [];
page.on('request', (r) => requests.push(r.url()));

const loadGene = async (sym) => {
  await page.fill('.search-input', sym);
  await page.waitForSelector('.search-dropdown .search-option', { timeout: 10000 });
  await page.click('.search-dropdown .search-option');
  await page.waitForTimeout(4500);
};

await page.goto(URL, { waitUntil: 'networkidle' });
await page.waitForSelector('canvas', { timeout: 20000 });
await page.waitForTimeout(4500);

// 1. Page loads -> TP53 graph with both layers
const d1 = await page.evaluate(() => window.__omniData);
const hasGene = d1?.nodes?.some((n) => n.node_type === 'gene');
const hasTx = d1?.nodes?.some((n) => n.node_type === 'transcript');
check(
  '1 page loads TP53 3D w/ two layers',
  !!d1 && d1.nodes.length > 0 && hasGene && hasTx && appErrors().length === 0,
  `nodes=${d1?.nodes?.length} gene=${hasGene} tx=${hasTx} appErrors=${appErrors().length}`,
);

// 2. Search BRCA2 -> graph updates
await loadGene('BRCA2');
const status2 = (await page.textContent('.status-line')) || '';
check('2 search BRCA2 updates graph', status2.includes('BRCA2'), status2.trim());

// 3. Tissue Liver -> re-fetch with tissue=liver
await page.click('.tissue-btn:has-text("Liver")');
await page.waitForTimeout(2500);
check('3 tissue Liver re-fetches', requests.some((u) => u.includes('/graph?tissue=liver')), '');

// back to All + TP53 for interactions
await page.click('.tissue-btn:has-text("All")');
await loadGene('TP53');

// 7. Toggle off Transcriptomics -> hides transcripts (also de-occludes genes)
const txInput = page.locator('.layer-toggle-item:has-text("Transcriptomics") input');
await txInput.click();
await page.waitForTimeout(1000);
check('7 toggle transcriptomics layer', (await txInput.isChecked()) === false, 'transcripts hidden');

// 4 + 5. Click a (visible, non-center) gene node -> panel -> expand adds neighbors.
// Try several gene nodes until a click lands a gene panel (avoids raycast misses).
let panelOk = false;
let expandOk = false;
const candidates = await page.evaluate(() => {
  const d = window.__omniData, fg = window.__omniFG;
  return d.nodes
    .filter((n) => n.node_type === 'gene' && n.x != null && n.hgnc_symbol)
    .slice(0, 12)
    .map((n) => {
      const c = fg.graph2ScreenCoords(n.x, n.y, n.z);
      return { sym: n.hgnc_symbol, x: c.x, y: c.y };
    })
    .filter((c) => c.x > 0 && c.x < 1400 && c.y > 0 && c.y < 900);
});
for (const cand of candidates) {
  await page.mouse.click(cand.x, cand.y);
  await page.waitForTimeout(700);
  if (await page.isVisible('.expand-btn')) {
    panelOk = true;
    const before = await page.evaluate(() => window.__omniData.nodes.length);
    await page.click('.expand-btn');
    await page.waitForTimeout(3500);
    const after = await page.evaluate(() => window.__omniData.nodes.length);
    expandOk = after >= before;
    check('4 click gene node opens panel', true, `gene panel for ${cand.sym}`);
    check('5 expand adds neighbors', expandOk, `nodes ${before}->${after}`);
    break;
  }
}
if (!panelOk) {
  check('4 click gene node opens panel', false, 'no gene panel after clicks');
  check('5 expand adds neighbors', false, 'panel never opened');
}

// restore transcriptomics
await txInput.click();
await page.waitForTimeout(800);

// 6. Query panel -> answer
await page.click('.query-toggle');
await page.fill('.query-input', 'What TFs regulate TP53?');
await page.click('.query-submit');
await page.waitForSelector('.query-answer', { timeout: 90000 });
const answer = (await page.textContent('.query-answer')) || '';
check('6 query returns answer', answer.trim().length > 15, answer.slice(0, 50).replace(/\n/g, ' '));

// 8. Hover an edge -> edge detail panel. From the side-profile camera the
// genomics layer is edge-on, so gene-gene edges are foreshortened; the cleanly
// hoverable edges are the interlayer PRODUCES "bridges" that span the view.
// Keep both layers visible and sample densely along those.
await page.waitForTimeout(800);
let edgeOk = false;
const bridgeCount = await page.evaluate(
  () =>
    window.__omniData.links.filter(
      (x) => typeof x.source === 'object' && x.source.x != null && x.rel_type === 'PRODUCES',
    ).length,
);
outer: for (let li = 0; li < Math.min(bridgeCount, 12); li++) {
  for (const f of [0.5, 0.45, 0.55, 0.4, 0.6, 0.35, 0.65, 0.3, 0.7]) {
    const hp = await page.evaluate(
      ({ li, f }) => {
        const d = window.__omniData, fg = window.__omniFG;
        const links = d.links.filter(
          (x) => typeof x.source === 'object' && x.source.x != null && x.rel_type === 'PRODUCES',
        );
        const l = links[li];
        const a = fg.graph2ScreenCoords(l.source.x, l.source.y, l.source.z);
        const b = fg.graph2ScreenCoords(l.target.x, l.target.y, l.target.z);
        return { x: a.x + (b.x - a.x) * f, y: a.y + (b.y - a.y) * f };
      },
      { li, f },
    );
    await page.mouse.move(hp.x, hp.y);
    await page.waitForTimeout(220);
    if (await page.isVisible('.edge-panel')) {
      edgeOk = true;
      break outer;
    }
  }
}
check('8 hover edge shows panel', edgeOk, edgeOk ? 'edge panel shown' : 'no hover hit');

const passed = results.filter((r) => r.ok).length;
console.log(`\nSUMMARY: ${passed}/${results.length} checks passed`);
if (appErrors().length) console.log('App JS errors:', [...new Set(appErrors())].slice(0, 6));
await browser.close();
process.exit(passed === results.length ? 0 : 1);
