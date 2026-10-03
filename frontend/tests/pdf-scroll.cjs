const { chromium } = require(process.argv[2] || 'playwright');
const fs = require('fs');
(async () => {
const browser = await chromium.launch({executablePath:process.argv[3],headless:true,args:['--no-sandbox'],ignoreDefaultArgs:['--hide-scrollbars']});
const page = await browser.newPage();
for (const width of [1440,800,390]) {
await page.setViewportSize({width,height:800});
await page.setContent(`<style>${fs.readFileSync(require('path').join(__dirname, '../src/styles.css'),'utf8')}</style><div class="app-shell app-shell--reading"><header class="site-header">Reader</header><div class="session-bar"><div class="session-heading">Sessão</div><div class="session-actions">Abrir PDF</div><div class="document-tabs">Arquivo.pdf</div></div><div class="reader-layout reader-layout--focused"><section class="reader-column"><div class="reader-toolbar">Página e zoom</div><div class="document-stage" tabindex="0"><div class="react-pdf__Document"><div class="page-shell"><div style="width:650px;height:1500px">Página longa<div id="end" style="position:absolute;bottom:0">Fim da página</div></div></div></div></div></section></div></div>`);
const result=await page.locator('.document-stage').evaluate(el=>{el.scrollTop=el.scrollHeight;const end=document.getElementById('end').getBoundingClientRect();const bounds=el.getBoundingClientRect();return {height:el.clientHeight,content:el.scrollHeight,scrollTop:el.scrollTop,overflow:getComputedStyle(el).overflowY,endVisible:end.bottom<=bounds.bottom,insideViewport:bounds.bottom<=innerHeight};});
console.log(width,result);
if (!result.scrollTop || !result.endVisible || !result.insideViewport || result.overflow!=='scroll') throw Error('PDF scrolling failed');
}
await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
