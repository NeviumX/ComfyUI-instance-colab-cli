// comfyui-instance-colab-cli: ComfyUI の画面に「停止」ボタンを足す。
// 押されたら POST /colab_cli/stop で要求を記録し、手元の start.bat のウィンドウが実行するのを待つ。
// ダイアログからは、出力をまとめて ZIP でダウンロードすることもできる。
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const STOP_PATH = "/colab_cli/stop";
// 停止ダイアログの右上に置く GitHub のマーク。空ならマークを出さない
const REPOSITORY_URL = "";

const TEXTS = {
  ja: {
    stop: "停止",
    title: "ComfyUIを停止",
    explain: "ランタイムを削除すると、コンピューティングユニットの消費が止まり、Colab 上のファイルはすべて消えます。Google ドライブのモデルは残ります。",
    saveNote: "削除の前に、カスタムノードや設定などの環境を Google ドライブに保存します。",
    pull: "生成したファイルは、削除の前に手元の outputs フォルダーへコピーされます。",
    noPull: "生成したファイルは手元にコピーされません。必要なものは先に保存してください。",
    disconnectNote: "接続だけを解除した場合、ランタイムは動き続けて課金も続きます。start.bat で再接続できます。",
    delete: "接続を解除してランタイムを削除",
    disconnect: "接続だけを解除",
    cancel: "キャンセル",
    zip: "出力を ZIP でダウンロード",
    zipChecking: "出力を確かめています…",
    zipStarted: "ZIP のダウンロードを始めました。{files} 個のファイル、{size} です。大きいときは時間がかかります。",
    zipEmpty: "ダウンロードできる出力はまだありません。",
    zipFailed: "出力を確かめられませんでした。",
    sending: "start.bat のウィンドウに伝えています…",
    accepted: "処理しています。進み具合は start.bat のウィンドウで確認できます。",
    deleted: "接続が切れました。ランタイムの削除の結果は start.bat のウィンドウで確認できます。",
    disconnected: "接続を解除しました。ランタイムは動き続けています。使い終わったら stop.bat で削除してください。",
    failed: "要求を送れませんでした。start.bat のウィンドウか stop.bat で操作してください。",
    saveFailed: "環境を Google ドライブに保存できなかったので、ランタイムは削除していません。理由は start.bat のウィンドウに表示されています。",
    slow: "start.bat のウィンドウから応答がありません。ウィンドウが開いているか確認するか、stop.bat で削除してください。",
    repository: "GitHub のリポジトリ",
  },
  en: {
    stop: "Stop",
    title: "Stop ComfyUI",
    explain: "Deleting the runtime stops compute-unit usage and erases every file on Colab. Models on Google Drive are kept.",
    saveNote: "Before that, the environment with custom nodes and settings is saved to Google Drive.",
    pull: "Generated files are copied to the local outputs folder before the runtime is deleted.",
    noPull: "Generated files are not copied to this PC. Save what you need first.",
    disconnectNote: "If you only disconnect, the runtime keeps running and keeps being billed. Reconnect with start.bat.",
    delete: "Disconnect and delete the runtime",
    disconnect: "Disconnect only",
    cancel: "Cancel",
    zip: "Download the outputs as a ZIP",
    zipChecking: "Checking the outputs…",
    zipStarted: "The ZIP download has started: {files} files, {size}. Large outputs take a while.",
    zipEmpty: "There are no outputs to download yet.",
    zipFailed: "Could not check the outputs.",
    sending: "Telling the start.bat window…",
    accepted: "Working on it. Follow the progress in the start.bat window.",
    deleted: "Disconnected. Check the start.bat window for the result of deleting the runtime.",
    disconnected: "Disconnected. The runtime is still running. Delete it with stop.bat when you are done.",
    failed: "Could not send the request. Use the start.bat window or stop.bat instead.",
    saveFailed: "The environment could not be saved to Google Drive, so the runtime was not deleted. The start.bat window shows why.",
    slow: "The start.bat window does not answer. Check that it is still open, or delete the runtime with stop.bat.",
    repository: "GitHub repository",
  },
};

async function loadSettings() {
  try {
    const answer = await api.fetchApi("/colab_cli/config", { cache: "no-store" });
    if (answer.ok) return await answer.json();
  } catch (error) {
    // 古い ComfyUI などで取れなくても、ブラウザーの言語で表示する
  }
  return {};
}

function mount(settings) {
  if (window.top !== window || document.getElementById("colab-stop-button")) return;
  const lang = settings.lang || (navigator.language || "").slice(0, 2);
  const text = TEXTS[lang === "ja" ? "ja" : "en"];

  const style = document.createElement("style");
  style.textContent = `
    #colab-stop-button { position: fixed; top: 7px; right: 56px; z-index: 1000; font: 12px/1 system-ui, sans-serif;
      padding: 5px 10px; border-radius: 6px; border: 1px solid rgba(128, 128, 128, .5); background: rgba(30, 30, 30, .7);
      color: #ddd; cursor: pointer; opacity: .6; }
    #colab-stop-button:hover, #colab-stop-button:focus-visible { opacity: 1; color: #fff; border-color: #f87171; }
    #colab-stop-dialog { font: 14px/1.6 system-ui, sans-serif; width: min(28rem, calc(100vw - 2rem)); padding: 1.25rem;
      border: 1px solid #555; border-radius: 10px; background: #202020; color: #eee;
      box-shadow: 0 12px 40px rgba(0, 0, 0, .6); }
    #colab-stop-dialog::backdrop { background: rgba(0, 0, 0, .6); }
    #colab-stop-dialog h2 { margin: 0 2.25rem .5rem 0; font-size: 1.1rem; }
    #colab-stop-dialog .colab-stop-repository { position: absolute; top: 1.1rem; right: 1.1rem; display: flex; color: #aaa; }
    #colab-stop-dialog .colab-stop-repository:hover, #colab-stop-dialog .colab-stop-repository:focus-visible { color: #fff; }
    #colab-stop-dialog p { margin: 0 0 .75rem; }
    #colab-stop-dialog .colab-stop-note { color: #aaa; font-size: 12.5px; }
    #colab-stop-dialog .colab-stop-status { color: #fcd34d; }
    #colab-stop-dialog .colab-stop-status:empty { display: none; }
    #colab-stop-dialog .colab-stop-actions { display: flex; flex-direction: column; gap: .5rem; }
    #colab-stop-dialog button { font: inherit; padding: .55rem .75rem; border-radius: 6px; border: 1px solid #666;
      background: #333; color: #eee; cursor: pointer; }
    #colab-stop-dialog button:hover:enabled { background: #444; }
    #colab-stop-dialog button:disabled { opacity: .45; cursor: default; }
    #colab-stop-dialog button.colab-stop-danger { background: #b91c1c; border-color: #b91c1c; color: #fff; }
    #colab-stop-dialog button.colab-stop-danger:hover:enabled { background: #dc2626; }
    #colab-stop-dialog button.colab-stop-quiet { background: transparent; }
  `;
  const add = (parent, tag, content, name) => {
    const element = document.createElement(tag);
    if (content) element.textContent = content;
    if (name) element.className = name;
    if (tag === "button") element.type = "button";
    return parent.appendChild(element);
  };

  const dialog = document.createElement("dialog");
  dialog.id = "colab-stop-dialog";
  add(dialog, "h2", text.title);
  add(dialog, "p", [text.explain, settings.save_environment ? text.saveNote : "",
                    settings.pull_outputs ? text.pull : text.noPull].filter(Boolean).join(" "));
  add(dialog, "p", text.disconnectNote, "colab-stop-note");
  const status = add(dialog, "p", "", "colab-stop-status");
  const actions = add(dialog, "div", "", "colab-stop-actions");
  const download = add(actions, "button", text.zip);
  const remove = add(actions, "button", text.delete, "colab-stop-danger");
  const disconnect = add(actions, "button", text.disconnect);
  const cancel = add(actions, "button", text.cancel, "colab-stop-quiet");
  cancel.autofocus = true;  // 開いた直後の Enter で停止が選ばれないようにする
  if (REPOSITORY_URL.startsWith("https://")) {
    // キーボードでは選択肢に先に届くよう、ダイアログの最後に置いて右上に表示する
    const link = add(dialog, "a", "", "colab-stop-repository");
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
  }

  const button = document.createElement("button");
  button.id = "colab-stop-button";
  button.type = "button";
  button.textContent = text.stop;
  button.title = text.title;

  let busy = false;
  const lock = (locked) => {
    busy = locked;
    for (const choice of [download, remove, disconnect, cancel]) choice.disabled = locked;
  };
  const ask = async (deleteRuntime) => {
    lock(true);
    status.textContent = text.sending;
    try {
      const answer = await api.fetchApi(STOP_PATH, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ delete_runtime: deleteRuntime }),
      });
      if (!answer.ok) throw new Error(answer.status);
    } catch (error) {
      status.textContent = text.failed;
      lock(false);
      return;
    }
    const started = Date.now();
    const timer = setInterval(async () => {
      let state;
      try {
        const answer = await api.fetchApi(STOP_PATH, { cache: "no-store" });
        if (!answer.ok) throw new Error(answer.status);
        state = (await answer.json()).state;
      } catch (error) {
        // 何も応答しなくなった: トンネルが閉じられた
        clearInterval(timer);
        status.textContent = deleteRuntime ? text.deleted : text.disconnected;
        button.remove();
        return;
      }
      if (state === "failed") {
        clearInterval(timer);
        status.textContent = text.saveFailed;
        lock(false);
      } else if (state === "accepted") {
        status.textContent = text.accepted;
      } else if (state === "pending" && Date.now() - started > 20000) {
        status.textContent = text.slow;
      }
    }, 2000);
  };
  const sizeText = (bytes) => {
    const units = ["B", "KB", "MB", "GB", "TB"];
    let value = bytes, unit = 0;
    while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit++; }
    return (unit === 0 ? value : value.toFixed(1)) + " " + units[unit];
  };
  // 件数と大きさを確かめてから、ブラウザーのダウンロードとして ZIP を受け取る
  const downloadOutputs = async () => {
    status.textContent = text.zipChecking;
    let summary;
    try {
      const answer = await api.fetchApi("/colab_cli/outputs", { cache: "no-store" });
      if (!answer.ok) throw new Error(answer.status);
      summary = await answer.json();
    } catch (error) {
      status.textContent = text.zipFailed;
      return;
    }
    if (!summary.files) {
      status.textContent = text.zipEmpty;
      return;
    }
    const link = document.createElement("a");
    link.href = api.apiURL("/colab_cli/outputs.zip");
    link.download = "";
    document.body.appendChild(link);
    link.click();
    link.remove();
    status.textContent = text.zipStarted.replace("{files}", summary.files).replace("{size}", sizeText(summary.bytes));
  };
  download.addEventListener("click", downloadOutputs);
  remove.addEventListener("click", () => ask(true));
  disconnect.addEventListener("click", () => ask(false));
  cancel.addEventListener("click", () => dialog.close());
  dialog.addEventListener("cancel", (event) => { if (busy) event.preventDefault(); });
  // ダイアログの中で押したキーを ComfyUI のショートカットとして扱わせない
  dialog.addEventListener("keydown", (event) => event.stopPropagation());
  button.addEventListener("click", () => {
    if (!busy) status.textContent = "";
    dialog.showModal();
  });

  document.head.appendChild(style);
  document.body.append(button, dialog);
}

app.registerExtension({
  name: "comfyui-colab-cli.stop-button",
  async setup() {
    mount(await loadSettings());
  },
});
