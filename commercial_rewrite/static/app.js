const $ = (sel) => document.querySelector(sel);

const form = $("#form");
const promptInput = $("#prompt");
const statusEl = $("#status");
const codeEl = $("#code");
const iframe = $("#player");
const btnGenerate = $("#btn-generate");
const btnOpen = $("#btn-open");
const btnSave = $("#btn-save");
const btnFrames = $("#btn-frames");
const fpsInput = $("#fps");
const maxFramesInput = $("#maxFrames");
const exportTip = $("#exportTip");
const btnLogout = $("#btn-logout");

function setStatus(text, kind = "") {
  statusEl.textContent = text || "";
  statusEl.classList.remove("error", "ok");
  if (kind) statusEl.classList.add(kind);
}

function extractHtmlFromMarkdown(text) {
  const first = text.indexOf("```");
  if (first === -1) return null;
  const langEnd = text.indexOf("\n", first + 3);
  if (langEnd === -1) return null;
  const fenceEnd = text.indexOf("```", langEnd + 1);
  if (fenceEnd === -1) return null;
  const inside = text.slice(langEnd + 1, fenceEnd);
  return inside.trim();
}

function setPreview(html) {
  iframe.srcdoc = patchAnimationHtml(html);
  btnOpen.disabled = !html;
  btnSave.disabled = !html;
  btnFrames.disabled = !html;
}

function patchAnimationHtml(html) {
  // 为了解决某些模型生成的动画在 renderFrame(t) 之间“未清空画布”导致的重影/双画面问题：
  // 通过 monkey patch，在页面加载后包装 window.renderFrame(t)，每次调用前先 clear canvas。
  const injection = `
<script>
(function(){
  function clearCanvas(){
    try{
      const c = document.querySelector('canvas');
      if(!c) return;
      const ctx = c.getContext('2d');
      if(!ctx) return;
      ctx.setTransform(1,0,0,1,0,0);
      ctx.clearRect(0,0,c.width,c.height);
    }catch(e){}
  }
  window.addEventListener('load', function(){
    if(typeof window.renderFrame !== 'function') return;
    const orig = window.renderFrame;
    window.renderFrame = function(t){
      clearCanvas();
      return orig(t);
    };
  });
})();
</script>`;

  if (html.includes('</body>')) return html.replace('</body>', `${injection}</body>`);
  if (html.includes('</html>')) return html.replace('</html>', `${injection}</html>`);
  return html + injection;
}

function clampInt(v, min, max, fallback) {
  const n = Number.parseInt(String(v), 10);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(min, Math.min(max, n));
}

function updateExportTip() {
  const fps = clampInt(fpsInput?.value, 1, 60, 12);
  const maxFrames = clampInt(maxFramesInput?.value, 60, 6000, 1500);
  exportTip.textContent = `提示：动画越长导出越慢、ZIP 越大。当前设置：${fps}fps，最多 ${maxFrames} 帧。`;
}

async function generate(prompt) {
  setStatus("生成中：正在从模型流式接收结果…");
  btnGenerate.disabled = true;
  btnOpen.disabled = true;
  btnSave.disabled = true;
  btnFrames.disabled = true;
  codeEl.value = "";
  setPreview("");

  const res = await fetch("/api/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({ prompt }),
  });
  if (res.status === 401) {
    window.location.href = "/login";
    throw new Error("未登录或登录已过期");
  }
  if (!res.ok) throw new Error(`HTTP ${res.status}`);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let full = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop();
    for (const part of parts) {
      if (!part.startsWith("data: ")) continue;
      const jsonStr = part.slice(6);
      if (jsonStr.includes("[DONE]")) {
        const html = extractHtmlFromMarkdown(full) || full.trim();
        codeEl.value = html;
        setPreview(html);
        setStatus("完成：可预览、保存或导出序列帧。", "ok");
        btnGenerate.disabled = false;
        return;
      }
      const data = JSON.parse(jsonStr);
      if (data.error) throw new Error(data.error);
      if (data.token) full += data.token;
    }
  }
}

function downloadText(filename, content) {
  const blob = new Blob([content], { type: "text/plain;charset=utf-8" });
  saveAs(blob, filename);
}

async function waitForIframeReady() {
  const maxWaitMs = 3000;
  const step = 50;
  let waited = 0;
  while (waited < maxWaitMs) {
    const w = iframe.contentWindow;
    if (w && w.document && w.document.readyState === "complete") return;
    await new Promise((r) => setTimeout(r, step));
    waited += step;
  }
}

function safeGetIframeExports() {
  const w = iframe.contentWindow;
  if (!w) throw new Error("iframe 不可用");
  const renderFrame = w.renderFrame;
  const duration = w.DURATION;
  const width = w.WIDTH;
  const height = w.HEIGHT;
  const canvas = w.document.querySelector("canvas");
  if (typeof renderFrame !== "function") throw new Error("动画页面缺少 renderFrame(t)");
  if (typeof duration !== "number" || !isFinite(duration) || duration <= 0)
    throw new Error("动画页面缺少合法的 DURATION");
  if (!canvas) throw new Error("动画页面未找到 canvas（导出帧需要 canvas）");
  return { w, renderFrame, duration, width, height, canvas };
}

async function exportFramesZip() {
  setStatus("导出中：准备抓帧…");
  btnFrames.disabled = true;

  const html = codeEl.value.trim();
  if (!html) throw new Error("没有可导出的 HTML");
  setPreview(html);
  await waitForIframeReady();

  const { renderFrame, duration, canvas } = safeGetIframeExports();
  const fps = clampInt(fpsInput?.value, 1, 60, 12);
  const maxFrames = clampInt(maxFramesInput?.value, 60, 6000, 1500);
  const totalFramesRaw = Math.max(1, Math.floor(duration * fps));
  const totalFrames = Math.min(totalFramesRaw, maxFrames);
  if (totalFramesRaw > maxFrames) {
    setStatus(`导出中：动画约 ${totalFramesRaw} 帧，已按上限截断为 ${maxFrames} 帧…`);
  }

  const zip = new JSZip();
  const folder = zip.folder("frames");
  if (!folder) throw new Error("无法创建 zip 目录");

  for (let i = 0; i < totalFrames; i++) {
    const t = i / fps;
    // 额外保险：在导出帧时清空一次，避免画面残留导致的重影。
    try {
      const ctx = canvas.getContext('2d');
      if (ctx) {
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.clearRect(0, 0, canvas.width, canvas.height);
      }
    } catch (e) {}
    renderFrame(t);
    await new Promise((r) => requestAnimationFrame(r));
    const dataUrl = canvas.toDataURL("image/png");
    const base64 = dataUrl.split(",")[1];
    const name = String(i).padStart(5, "0") + ".png";
    folder.file(name, base64, { base64: true });
    if (i % fps === 0) setStatus(`导出中：${i}/${totalFrames} 帧…`);
  }

  setStatus("导出中：压缩 ZIP…");
  const blob = await zip.generateAsync({ type: "blob" });
  saveAs(blob, "frames.zip");
  setStatus("完成：已下载 frames.zip", "ok");
  btnFrames.disabled = false;
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const prompt = promptInput.value.trim();
  if (!prompt) return;
  try {
    await generate(prompt);
  } catch (err) {
    console.error(err);
    setStatus(`生成失败：${err.message || String(err)}`, "error");
    btnGenerate.disabled = false;
  }
});

fpsInput?.addEventListener("change", updateExportTip);
maxFramesInput?.addEventListener("change", updateExportTip);
updateExportTip();

btnOpen.addEventListener("click", () => {
  const html = codeEl.value.trim();
  if (!html) return;
  const blob = new Blob([html], { type: "text/html;charset=utf-8" });
  window.open(URL.createObjectURL(blob), "_blank");
});

btnSave.addEventListener("click", () => {
  const html = codeEl.value.trim();
  if (!html) return;
  downloadText("animation.html", html);
});

btnFrames.addEventListener("click", async () => {
  try {
    await exportFramesZip();
  } catch (err) {
    console.error(err);
    setStatus(`导出失败：${err.message || String(err)}`, "error");
    btnFrames.disabled = false;
  }
});

btnLogout?.addEventListener("click", async () => {
  try {
    await fetch("/api/logout", { method: "POST", credentials: "same-origin" });
  } finally {
    window.location.href = "/login";
  }
});

