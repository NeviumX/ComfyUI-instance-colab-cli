// comfyui-instance-colab-cli: ランタイムに写しが無いモデルを取ってくる間、画面の右下に進み具合を出す。
// 取ってくるのは Colab の VM 上のノードで、この画面は届いた知らせを表示するだけ。
import { api } from "../../scripts/api.js";
import { app } from "../../scripts/app.js";

const TEXTS = {
  ja: {
    url: "元の URL からダウンロード中",
    drive_api: "Google ドライブから並列ダウンロード中",
    drive_copy: "Google ドライブからコピー中",
    done: "ランタイムに用意しました",
    fallback: "Google ドライブから直接読み込みます",
    failed: "モデルを取得できませんでした",
    cancelled: "取得を中止しました",
    backed_up: "Google ドライブにコピーしました",
  },
  en: {
    url: "Downloading from the original URL",
    drive_api: "Downloading from Google Drive in parallel",
    drive_copy: "Copying from Google Drive",
    done: "Ready on the runtime",
    fallback: "Reading it from Google Drive directly",
    failed: "Could not get the model",
    cancelled: "Stopped getting the model",
    backed_up: "Copied to Google Drive",
  },
};

const sizeText = (bytes) => {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes || 0, unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit++; }
  return (unit === 0 ? value : value.toFixed(1)) + " " + units[unit];
};

async function language() {
  try {
    const answer = await api.fetchApi("/colab_cli/config", { cache: "no-store" });
    if (answer.ok) {
      const settings = await answer.json();
      if (settings.lang) return settings.lang;
    }
  } catch (error) {
    // 取れなければブラウザーの言語にする
  }
  return (navigator.language || "").slice(0, 2);
}

function mount(lang) {
  if (window.top !== window || document.getElementById("colab-fetch-stack")) return;
  const text = TEXTS[lang === "ja" ? "ja" : "en"];
  const style = document.createElement("style");
  style.textContent = `
    #colab-fetch-stack { position: fixed; right: 16px; bottom: 16px; z-index: 1100; display: flex; flex-direction: column;
      gap: 8px; width: min(24rem, calc(100vw - 32px)); font: 13px/1.5 system-ui, sans-serif; pointer-events: none; }
    #colab-fetch-stack .colab-fetch { pointer-events: auto; padding: .65rem .8rem; border-radius: 8px; border: 1px solid #555;
      background: rgba(32, 32, 32, .96); color: #eee; box-shadow: 0 6px 24px rgba(0, 0, 0, .5); }
    #colab-fetch-stack .colab-fetch-title { font-weight: 600; }
    #colab-fetch-stack .colab-fetch-name { color: #bbb; word-break: break-all; }
    #colab-fetch-stack .colab-fetch-detail { color: #bbb; white-space: pre-wrap; word-break: break-word; }
    #colab-fetch-stack .colab-fetch-detail:empty { display: none; }
    #colab-fetch-stack progress { width: 100%; height: .8rem; margin-top: .3rem; }
    #colab-fetch-stack progress[hidden] { display: none; }
    #colab-fetch-stack .colab-fetch.failed { border-color: #ef4444; }
    #colab-fetch-stack .colab-fetch.fallback { border-color: #f59e0b; }
    #colab-fetch-stack .colab-fetch.done, #colab-fetch-stack .colab-fetch.backed_up { border-color: #22c55e; }
  `;
  const stack = document.createElement("div");
  stack.id = "colab-fetch-stack";
  stack.setAttribute("role", "status");
  stack.setAttribute("aria-live", "polite");
  document.head.appendChild(style);
  document.body.appendChild(stack);

  const cards = new Map();
  const card = (name) => {
    let entry = cards.get(name);
    if (!entry) {
      const element = document.createElement("div");
      element.className = "colab-fetch";
      const title = element.appendChild(document.createElement("div"));
      title.className = "colab-fetch-title";
      const label = element.appendChild(document.createElement("div"));
      label.className = "colab-fetch-name";
      label.textContent = name;
      const bar = element.appendChild(document.createElement("progress"));
      const detail = element.appendChild(document.createElement("div"));
      detail.className = "colab-fetch-detail";
      stack.appendChild(element);
      entry = { element, title, bar, detail, timer: null };
      cards.set(name, entry);
    }
    clearTimeout(entry.timer);
    return entry;
  };
  const closeLater = (name, entry, seconds) => {
    entry.timer = setTimeout(() => { entry.element.remove(); cards.delete(name); }, seconds * 1000);
  };

  api.addEventListener("colab_cli.model_fetch", (event) => {
    const data = event.detail || {};
    if (!data.name) return;
    const entry = card(data.name);
    entry.element.className = "colab-fetch " + (data.state || "");
    if (data.state === "running") {
      entry.title.textContent = text[data.source] || data.message || "";
      entry.bar.hidden = false;
      if (data.total) {
        entry.bar.max = data.total;
        entry.bar.value = data.received;
        entry.detail.textContent = `${(data.received * 100 / data.total).toFixed(1)}%  ` +
          `${sizeText(data.received)} / ${sizeText(data.total)}  ${sizeText(data.speed)}/s`;
      } else {
        entry.bar.removeAttribute("value");
        entry.detail.textContent = `${sizeText(data.received)}  ${sizeText(data.speed)}/s`;
      }
      return;
    }
    entry.title.textContent = text[data.state] || data.state || "";
    entry.bar.hidden = data.state !== "done";
    if (data.state === "done") { entry.bar.max = 1; entry.bar.value = 1; }
    entry.detail.textContent = data.state === "done" || data.state === "backed_up" ? "" : (data.message || "");
    closeLater(data.name, entry, data.state === "failed" || data.state === "fallback" ? 15 : 5);
  });
}

app.registerExtension({
  name: "comfyui-colab-cli.model-fetch",
  async setup() {
    mount(await language());
  },
});
