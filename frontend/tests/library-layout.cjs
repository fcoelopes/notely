const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const { chromium } = require(process.argv[2] || 'playwright');
const css = fs.readFileSync(path.join(__dirname, '../src/styles.css'), 'utf8');
const fixture = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'notely-layout-')), 'index.html');
const sessionCard = `<li><button type="button"><span class="collection-icon">▤</span><span class="collection-card-body"><span class="collection-card-top"><strong>Sem tema definido</strong><small>4 de out. de 2026</small></span><small class="collection-origin">defina o tema quando quiser</small><small class="collection-description">4 PDFs · ABNT ISO TS 55010 - 2021 · ABNT NBR ISO 55000 - 2024</small><small class="reading-progress">3% · 7/207 páginas visualizadas</small><span class="library-progress-track"><span style="width:3%"></span></span></span><span class="collection-arrow">↗</span></button></li>`;
const documentCard = `<li><span class="collection-icon document-icon">PDF</span><span class="collection-card-body"><span class="collection-card-top"><strong>ABNT NBR ISO 55002 - 2020</strong><small>4 de out. de 2026</small></span><small class="collection-description">99 páginas · ABNT NBR ISO 55002 - 2020.pdf</small><small class="reading-progress">0% · 0/99 páginas visualizadas</small><span class="library-progress-track"><span></span></span></span></li>`;
fs.writeFileSync(fixture, `<!doctype html><meta charset="utf-8"><style>${css}</style><div class="app-shell"><main class="session-library"><aside class="library-nav">Biblioteca</aside><div class="library-content"><section class="library-columns"><div class="library-block"><div class="collection-title"><h3>Sessões abertas</h3><span>7</span><button class="collection-view-all">Ver todas as sessões →</button></div><ul class="session-list">${sessionCard.repeat(3)}</ul></div><div class="library-block"><div class="collection-title"><h3>Documentos já ingeridos</h3><span>7</span><button class="collection-view-all">Ver todos os documentos →</button></div><ul class="document-list">${documentCard.repeat(3)}</ul></div></section></div></main></div><pre id="report"></pre><script>
const blocks = [...document.querySelectorAll('.library-block')];
const cards = [...document.querySelectorAll('.session-list li, .document-list li')];
const rect = (element) => { const r = element.getBoundingClientRect(); return { left: r.left, right: r.right, top: r.top, bottom: r.bottom }; };
document.getElementById('report').textContent = JSON.stringify({ width: innerWidth, scrollWidth: document.documentElement.scrollWidth, blocks: blocks.map(rect), cards: cards.map(rect) });
</script>`);
(async () => {
  const browser = await chromium.launch({ executablePath: process.argv[3], headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage();
    for (const width of [1440, 972, 390]) {
      await page.setViewportSize({ width, height: 800 });
      await page.goto(`file://${fixture}`);
      const report = JSON.parse(await page.locator('#report').textContent());
      assert.ok(report.scrollWidth <= report.width, `Rolagem horizontal em ${width}px: ${JSON.stringify(report)}`);
      for (let index = 0; index < report.cards.length; index++) {
        const block = report.blocks[index < 3 ? 0 : 1];
        const card = report.cards[index];
        assert.ok(card.left >= block.left - 1 && card.right <= block.right + 1, `Cartão fora da coluna em ${width}px: ${JSON.stringify(report)}`);
      }
      const [sessions, documents] = report.blocks;
      assert.ok(sessions.right <= documents.left + 1 || sessions.bottom <= documents.top + 1, `Colunas sobrepostas em ${width}px: ${JSON.stringify(report)}`);
      console.log(`${width}px: cartões dentro das colunas, sem sobreposição`);
    }
  } finally {
    await browser.close();
    fs.rmSync(path.dirname(fixture), { recursive: true, force: true });
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
