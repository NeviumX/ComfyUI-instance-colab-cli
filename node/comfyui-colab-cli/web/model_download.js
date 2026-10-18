// comfyui-instance-colab-cli: ComfyUI の画面に「モデル」ボタンを足し、Hugging Face と Civitai のモデルを Colab にダウンロードする。
// ダウンロードは Colab の VM 上で進み、この画面は進み具合を 1 秒ごとに尋ねて表示するだけなので、閉じても止まらない。
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

// ダイアログの右上に置く GitHub のマーク
const REPOSITORY_URL = "https://github.com/NeviumX/ComfyUI-instance-colab-cli";

const TEXTS = {
  ja: {
    button: "モデル",
    title: "モデルをダウンロード",
    repository: "GitHub のリポジトリ",
    explain: "Hugging Face のファイルのページかCivitai のモデルのページの URL を貼り付けてください。",
    folder: "保存先のフォルダー",
    url: "URL",
    filename: "ファイル名 空のままなら URL やサイトの名前を使います",
    where: "保存する場所",
    runtime: "ランタイムだけ ランタイムを削除すると消えます",
    drive: "Googleドライブにも残す ランタイムに落としてからドライブにコピーします",
    noDrive: "Googleドライブがマウントされていないので、ランタイムにだけ保存できます。",
    start: "ダウンロード",
    cancelDownload: "中止",
    close: "閉じる",
    loading: "フォルダーの一覧を読み込んでいます…",
    loadFailed: "フォルダーの一覧を読み込めませんでした。",
    needUrl: "URL を入力してください。",
    busy: "ほかのダウンロードが進んでいます。",
    failedToStart: "ダウンロードを始められませんでした。",
    downloading: "ダウンロード中",
    copying: "Google ドライブへコピー中",
    failed: "失敗しました",
    cancelled: "中止しました。",
  },
  en: {
    button: "Models",
    title: "Download a model",
    repository: "GitHub repository",
    explain: "Paste the URL of a Hugging Face file page or a Civitai model page.",
    folder: "Folder",
    url: "URL",
    filename: "File name. Leave empty to use the name from the URL or the site",
    where: "Save to",
    runtime: "The runtime only. Fast, and gone when the runtime is deleted",
    drive: "Google Drive too. Downloaded to the runtime first, then copied to Drive",
    noDrive: "Google Drive is not mounted, so the model can be saved on the runtime only.",
    start: "Download",
    cancelDownload: "Cancel",
    close: "Close",
    loading: "Loading the folders…",
    loadFailed: "Could not load the folders.",
    needUrl: "Enter a URL.",
    busy: "Another download is in progress.",
    failedToStart: "Could not start the download.",
    downloading: "Downloading",
    copying: "Copying to Google Drive",
    failed: "Failed",
    cancelled: "Cancelled.",
  },
};

const sizeText = (bytes) => {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes || 0, unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit++; }
  return (unit === 0 ? value : value.toFixed(1)) + " " + units[unit];
};

async function loadSettings() {
  try {
    const answer = await api.fetchApi("/colab_cli/config", { cache: "no-store" });
    if (answer.ok) return await answer.json();
  } catch (error) {
    // 取れなくても、ブラウザーの言語で表示する
  }
  return {};
}

function mount(settings) {
  if (window.top !== window || document.getElementById("colab-model-button")) return;
  const lang = settings.lang || (navigator.language || "").slice(0, 2);
  const text = TEXTS[lang === "ja" ? "ja" : "en"];

  const style = document.createElement("style");
  style.textContent = `
    #colab-model-button { position: fixed; top: 7px; right: 112px; z-index: 1000; font: 12px/1 system-ui, sans-serif;
      padding: 5px 10px; border-radius: 6px; border: 1px solid rgba(128, 128, 128, .5); background: rgba(30, 30, 30, .7);
      color: #ddd; cursor: pointer; opacity: .6; }
    #colab-model-button:hover, #colab-model-button:focus-visible { opacity: 1; color: #fff; border-color: #60a5fa; }
    #colab-model-dialog { font: 14px/1.6 system-ui, sans-serif; width: min(34rem, calc(100vw - 2rem)); padding: 1.25rem;
      border: 1px solid #555; border-radius: 10px; background: #202020; color: #eee; box-shadow: 0 12px 40px rgba(0, 0, 0, .6); }
    #colab-model-dialog::backdrop { background: rgba(0, 0, 0, .6); }
    #colab-model-dialog h2 { margin: 0 2.25rem .5rem 0; font-size: 1.1rem; }
    #colab-model-dialog .colab-model-repository { position: absolute; top: 1.1rem; right: 1.1rem; display: flex; color: #aaa; }
    #colab-model-dialog .colab-model-repository:hover, #colab-model-dialog .colab-model-repository:focus-visible { color: #fff; }
    #colab-model-dialog p { margin: 0 0 .75rem; }
    #colab-model-dialog label { display: block; margin: 0 0 .6rem; font-size: 12.5px; color: #bbb; }
    #colab-model-dialog input[type=text], #colab-model-dialog select { display: block; width: 100%; box-sizing: border-box;
      margin-top: .25rem; padding: .45rem .55rem; border-radius: 6px; border: 1px solid #666; background: #2b2b2b; color: #eee; font: 13px system-ui, sans-serif; }
    #colab-model-dialog fieldset { border: 1px solid #444; border-radius: 6px; margin: 0 0 .75rem; padding: .4rem .7rem; }
    #colab-model-dialog legend { font-size: 12.5px; color: #bbb; padding: 0 .3rem; }
    #colab-model-dialog fieldset label { display: flex; gap: .5rem; align-items: flex-start; margin: .3rem 0; color: #ddd; }
    #colab-model-dialog progress { width: 100%; height: 1rem; margin: .25rem 0; }
    #colab-model-dialog progress[hidden] { display: none; }
    #colab-model-dialog .colab-model-status { color: #fcd34d; white-space: pre-wrap; word-break: break-all; }
    #colab-model-dialog .colab-model-status:empty { display: none; }
    #colab-model-dialog .colab-model-actions { display: flex; gap: .5rem; justify-content: flex-end; }
    #colab-model-dialog button { font: inherit; padding: .5rem .9rem; border-radius: 6px; border: 1px solid #666;
      background: #333; color: #eee; cursor: pointer; }
    #colab-model-dialog button:hover:enabled { background: #444; }
    #colab-model-dialog button:disabled { opacity: .45; cursor: default; }
    #colab-model-dialog button.colab-model-primary { background: #2563eb; border-color: #2563eb; color: #fff; }
    #colab-model-dialog button.colab-model-primary:hover:enabled { background: #3b82f6; }
  `;
  const add = (parent, tag, content, name) => {
    const element = document.createElement(tag);
    if (content) element.textContent = content;
    if (name) element.className = name;
    if (tag === "button") element.type = "button";
    return parent.appendChild(element);
  };
  const field = (parent, label, input) => {
    const wrapper = add(parent, "label", label);
    wrapper.appendChild(input);
    return input;
  };

  const dialog = document.createElement("dialog");
  dialog.id = "colab-model-dialog";
  add(dialog, "h2", text.title);
  add(dialog, "p", text.explain);
  const folder = field(dialog, text.folder, document.createElement("select"));
  const url = field(dialog, text.url, Object.assign(document.createElement("input"), { type: "text", spellcheck: false,
    placeholder: "https://huggingface.co/…/blob/main/…  /  https://civitai.com/models/…?modelVersionId=…" }));
  const filename = field(dialog, text.filename, Object.assign(document.createElement("input"), { type: "text", spellcheck: false }));
  const where = add(dialog, "fieldset");
  add(where, "legend", text.where);
  const choice = (value, label) => {
    const wrapper = add(where, "label");
    const radio = Object.assign(document.createElement("input"), { type: "radio", name: "colab-model-where", value });
    wrapper.append(radio, document.createTextNode(label));
    return radio;
  };
  const toRuntime = choice("runtime", text.runtime);
  const toDrive = choice("drive", text.drive);
  const bar = add(dialog, "progress");
  bar.hidden = true;
  const status = add(dialog, "p", "", "colab-model-status");
  const actions = add(dialog, "div", "", "colab-model-actions");
  const cancel = add(actions, "button", text.cancelDownload);
  const close = add(actions, "button", text.close);
  const start = add(actions, "button", text.start, "colab-model-primary");
  cancel.hidden = true;
  // キーボードでは入力欄に先に届くよう、ダイアログの最後に置いて右上に表示する
  const link = add(dialog, "a", "", "colab-model-repository");
  link.href = REPOSITORY_URL;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.title = text.repository;
  link.setAttribute("aria-label", text.repository);
  const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  for (const [name, value] of [["viewBox", "0 0 16 16"], ["width", "20"], ["height", "20"], ["fill", "currentColor"], ["aria-hidden", "true"]]) {
    icon.setAttribute(name, value);
  }
  const mark = document.createElementNS("http://www.w3.org/2000/svg", "path");
  mark.setAttribute("d", "M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z");
  icon.appendChild(mark);
  link.appendChild(icon);

  const button = document.createElement("button");
  button.id = "colab-model-button";
  button.type = "button";
  button.textContent = text.button;
  button.title = text.title;

  let timer = null;
  let downloading = false;  // 進んでいる途中なら、閉じても結果を残して続きを見られるようにする
  const setRunning = (running) => {
    for (const input of [folder, url, filename, toRuntime, toDrive, start]) input.disabled = running;
    if (!running && toDrive.dataset.unavailable) toDrive.disabled = true;
    cancel.hidden = !running;
  };
  const show = (progress) => {
    const running = progress.state === "running";
    downloading = running;
    setRunning(running);
    if (running) {
      const label = progress.phase === "copy" ? text.copying : text.downloading;
      bar.hidden = false;
      if (progress.total) {
        bar.max = progress.total;
        bar.value = progress.received;
        status.textContent = `${label}: ${progress.name || ""}\n${(progress.received * 100 / progress.total).toFixed(1)}%  ` +
          `${sizeText(progress.received)} / ${sizeText(progress.total)}  ${sizeText(progress.speed)}/s`;
      } else {
        bar.removeAttribute("value");
        status.textContent = `${label}: ${progress.name || ""}\n${sizeText(progress.received)}  ${sizeText(progress.speed)}/s`;
      }
      return true;
    }
    bar.hidden = progress.state !== "done";
    if (progress.state === "done") { bar.max = 1; bar.value = 1; }
    if (progress.state === "failed") status.textContent = `${text.failed}: ${progress.message || ""}`;
    else if (progress.state === "cancelled") status.textContent = text.cancelled;
    else if (progress.state === "done") status.textContent = progress.message || "";
    return false;
  };
  const poll = async () => {
    clearTimeout(timer);
    try {
      const answer = await api.fetchApi("/colab_cli/models/download", { cache: "no-store" });
      if (answer.ok && show(await answer.json())) timer = setTimeout(poll, 1000);
    } catch (error) {
      timer = setTimeout(poll, 3000);
    }
  };

  const loadFolders = async () => {
    status.textContent = text.loading;
    try {
      const answer = await api.fetchApi("/colab_cli/models/folders", { cache: "no-store" });
      if (!answer.ok) throw new Error(answer.status);
      const listing = await answer.json();
      const current = folder.value;
      folder.replaceChildren(...listing.folders.map((name) => Object.assign(document.createElement("option"), { value: name, textContent: name })));
      folder.value = listing.folders.includes(current) ? current : (listing.folders.includes("checkpoints") ? "checkpoints" : listing.folders[0] || "");
      if (listing.drive) {
        delete toDrive.dataset.unavailable;
        toDrive.disabled = false;
        if (!toRuntime.checked && !toDrive.checked) toDrive.checked = true;
      } else {
        toDrive.dataset.unavailable = "1";
        toDrive.disabled = true;
        toRuntime.checked = true;
      }
      status.textContent = listing.drive ? "" : text.noDrive;
    } catch (error) {
      status.textContent = text.loadFailed;
    }
  };

  start.addEventListener("click", async () => {
    if (!url.value.trim()) {
      status.textContent = text.needUrl;
      return;
    }
    setRunning(true);
    try {
      const answer = await api.fetchApi("/colab_cli/models/download", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: url.value.trim(), folder: folder.value, filename: filename.value.trim(),
                               to_drive: toDrive.checked }),
      });
      if (answer.status === 409) throw new Error(text.busy);
      if (!answer.ok) throw new Error(text.failedToStart);
    } catch (error) {
      status.textContent = error.message || text.failedToStart;
      setRunning(false);
      return;
    }
    status.textContent = "";
    poll();
  });
  cancel.addEventListener("click", async () => {
    try {
      await api.fetchApi("/colab_cli/models/cancel", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    } catch (error) {
      // 次の問い合わせで結果が分かる
    }
  });
  close.addEventListener("click", () => dialog.close());
  dialog.addEventListener("close", () => {
    clearTimeout(timer);
    if (downloading) return;
    // 終わった結果は、閉じたら画面からも Colab 側の記録からも消す。再読み込みしても出さない
    status.textContent = "";
    bar.hidden = true;
    api.fetchApi("/colab_cli/models/dismiss", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" })
      .catch(() => {});
  });
  // ダイアログの中で押したキーを ComfyUI のショートカットとして扱わせない
  dialog.addEventListener("keydown", (event) => event.stopPropagation());
  button.addEventListener("click", async () => {
    dialog.showModal();
    await loadFolders();
    poll();  // 閉じている間に進んでいたダウンロードも表示する
  });

  document.head.appendChild(style);
  document.body.append(button, dialog);
  // 「停止」ボタンの左に並べる
  const place = () => {
    const stop = document.getElementById("colab-stop-button");
    if (stop) button.style.right = `${56 + stop.offsetWidth + 6}px`;
  };
  place();
  setTimeout(place, 1000);
}

app.registerExtension({
  name: "comfyui-colab-cli.model-download",
  async setup() {
    mount(await loadSettings());
  },
});
