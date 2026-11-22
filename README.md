# comfyui-instance-colab-cli

[日本語](README_JP.md)

Scripts for running [ComfyUI](https://github.com/comfyanonymous/ComfyUI) on a Google Colab GPU and using it from a browser on Windows
or from the Remote Connection of [Comfy Desktop](https://github.com/Comfy-Org/desktop).
They use the [Colab CLI](https://github.com/googlecolab/google-colab-cli) on WSL and SSH port forwarding, so ComfyUI is never exposed to the internet.

## Requirements

- Windows 10/11 with Ubuntu on WSL2
- A Colab plan that gives access to GPUs. A running runtime consumes compute units

## Setup

In WSL, move to this folder and run the following. The first `colab usage` signs you in to your Google account.

```bash
bash ./setup-wsl.sh
colab usage
```

Change the GPU, ports and other settings in `config.env`. Settings of your own can go in `config.local.env`, which overrides `config.env` without editing it.

## Usage

| Action | Windows | WSL |
|---|---|---|
| Create a runtime and open ComfyUI | `start.bat` | `./up.sh` |
| Delete the runtime | `stop.bat` | `./down.sh` |
| Update ComfyUI | `update.bat` | `./update.sh` |
| Download a model | `model-download.bat` | `./model-download.sh` |
| Authorize the Drive API again, or revoke it | `drive-auth.bat` | `./drive-auth.sh` / `./drive-auth.sh revoke` |
| Show status | `status.bat` | `./status.sh` |
| Reconnect a closed tunnel | `start.bat` | `./tunnel.sh` |
| Copy the outputs to this PC | | `./pull-outputs.sh` |
| Show logs | | `./logs.sh` |
| Open a shell on the VM | | `./ssh.sh` |

Double-clicking `start.bat` creates the runtime, downloads and starts ComfyUI, forwards the port and opens `http://localhost:8188` in your browser.

### Using Comfy Desktop

When `start.bat` says it is ready, create a new **Remote Connection** in Comfy Desktop and enter `http://localhost:8188` as the URL.
Turn on **Auto-download outputs** in the connection settings to save generated images and videos on your PC.

### Finishing from ComfyUI

The Stop button at the top right of ComfyUI offers two choices.

- **Disconnect and delete the runtime**: copies the outputs to the local `outputs` folder, then deletes the runtime. Billing stops.
- **Disconnect only**: closes only the tunnel. The runtime and ComfyUI keep running and keep being billed. Reconnect with `start.bat`.

The `start.bat` window carries out the choice, so keep it open while you use ComfyUI.

**Download the outputs as a ZIP** in the same dialog packs the outputs on Colab into one ZIP file and hands it to your browser as a download.
With many outputs, it takes a while to arrive.

### Downloading models

While the runtime runs, you can download models from Hugging Face and Civitai to Colab in two ways.

- **In ComfyUI:** press Models at the top right, pick a folder, paste the URL and press Download.
  A progress bar shows how far it got. The download runs on Colab, so closing the dialog does not stop it.
- **model-download.bat:** pick a folder by number, paste the URL and choose where to save. A progress bar is shown and the window closes when it is done.
  If the download fails, the window stays open so you can read why.

For Hugging Face, copy the URL of the file page from your browser. For Civitai, copy the URL of the model page with a version selected.

In ComfyUI, choose one of three places to save:

- **The runtime only:** only downloaded to the runtime. It is not recorded in the ledger, so it leaves the lists when the runtime is deleted.
- **The runtime only, recorded in the ledger:** gone when the runtime is deleted, but it is recorded in the ledger and stays in the model lists on the next runtime.
  When a node uses it, it is downloaded again from the recorded source.
- **The runtime and Google Drive:** downloaded to the runtime and recorded in the ledger, then copied to Drive in the background.
  ComfyUI can use it as soon as it is on the runtime. The bottom right of the page shows the copy and when it is done.

`model-download.bat` offers two choices that are both recorded in the ledger: the runtime only, or Google Drive too. The copy to Drive finishes before the window closes.

When the download is done, press R in ComfyUI to see the model in the lists. A file with the same name is never overwritten.

For models that need authentication, put your tokens in `config.local.env`, then start with `start.bat`.

```bash
HF_TOKEN=hf_...
CIVITAI_TOKEN=...
```

Each token is sent only to its own site.

### The model ledger

Models downloaded with this tool are recorded in a ledger with where they are saved and where they came from, unless "The runtime only" is chosen.
Hugging Face models are recorded with the commit they were downloaded from, so the same content can be downloaded again later.

- With Google Drive, the ledger is `My Drive/ComfyUI-Colab/model-sources.json`. Without it, the ledger is `model-sources.json` in this folder.
  The local ledger is sent to the runtime at every start and new records come back from the runtime, so the records carry over either way.
- Models in the ledger show in the ComfyUI model lists even when they are on neither the runtime nor Drive.
  Remove one under "Models in the ledger" in the download dialog to hide it from the lists. No file is deleted.
- Models downloaded by ComfyUI-Manager and similar tools are not in the ledger, because their source is unknown.

### Updating ComfyUI

Every start checks whether a newer release of ComfyUI exists and, if so, says so in the `start.bat` window.

- `update.bat` updates ComfyUI on the running runtime to the latest release and starts it again. Reload ComfyUI when it is done.
  Run `./update.sh v0.3.60` in WSL to choose a version.
- With `AUTO_UPDATE=1` in `config.env`, a newer release is installed before ComfyUI starts. The default is 0, which only tells you.
- Custom nodes, settings and workflows are kept. After a successful update, the environment is saved to Google Drive.
- If the update fails, ComfyUI goes back to the earlier version and nothing is saved to Google Drive.
- Do not use "Update ComfyUI" in ComfyUI-Manager. A periodic save during that update can store an inconsistent environment.

### SageAttention and launch options

With `USE_SAGE_ATTENTION=1` in `config.env`, the default, SageAttention is installed at startup and tried on the GPU.
If it works, ComfyUI starts with `--use-sage-attention`. If not, the reason is shown and ComfyUI starts with the standard attention.

Put other ComfyUI launch options in `COMFY_EXTRA_ARGS` in `config.env`, separated by spaces. Example: `COMFY_EXTRA_ARGS='--fast fp16_accumulation'`.
They take effect the next time ComfyUI starts.

### Models on Google Drive

With `USE_DRIVE=1`, Google Drive is mounted at startup and the models in `My Drive/ComfyUI-Colab/models` are added to ComfyUI.
The first time, the `start.bat` window shows an authorization URL. Open it in a browser, allow access, then press Enter in the window.

```text
My Drive/
└── ComfyUI-Colab/
    └── models/
        ├── checkpoints/
        ├── diffusion_models/
        ├── text_encoders/
        ├── vae/
        ├── loras/
        └── ...
```

ComfyUI reads the copy on the runtime first when there is one. When a node uses a model with no copy on the runtime, the model is first brought to the runtime and then loaded.
For large models this is often faster than reading from the mounted Drive directly.
Choose the order with `MODEL_SOURCE` in `config.env`.

- **`MODEL_SOURCE=drive`:** the default. Tries a parallel download with the Drive API, then a sequential copy from Drive, then the source in the ledger.
- **`MODEL_SOURCE=url`:** tries the source in the ledger first, then Drive.

While a model is being brought over, the progress is shown at the bottom right of ComfyUI. If nothing works, or the runtime disk is too full, the model is read from Drive directly as before.
If the ledger has a model with the same name but a different size from the one on Drive, the one on Drive is used.

Models downloaded by ComfyUI-Manager and similar tools are saved on the runtime first and copied to Drive automatically once they are complete, so the next runtime can use them.

### Setting up the Drive API (Optional)

With the Drive API, models on Drive are downloaded in parallel. Make an OAuth client in your own Google Cloud project and authorize it once.
Everything works without it too. Models on Drive are then copied one part after another.

1. Create a project in the [Google Cloud console](https://console.cloud.google.com/). No billing account is needed.
2. Under APIs & Services, Library, enable the **Google Drive API**.
3. Set up the OAuth consent screen. Choose External as the audience and add your own Google account as a test user.
4. Under Credentials, create an OAuth client ID. The application type is **Desktop app**.
5. Download the client's JSON and save it in this folder as `drive-oauth-client.json`.

The next `start.bat` shows an authorization URL after Drive is mounted and opens the browser. Allow access with your Google account, and the browser says it is authorized.

- Only reading Drive is allowed.
- The long-lived refresh token is kept only in `.state/drive-token.json` in this folder. The Colab VM gets only a token that expires in about an hour, and the `start.bat` window sends a new one every 40 minutes.
- While the OAuth consent screen is in Testing, the authorization expires every 7 days. When it does, the next start shows the authorization URL again.
  In Production it does not expire, but allowing access shows an "unverified app" warning. For an app you made yourself, you can go on.
- To stop using it, run `drive-auth.bat revoke`. It asks Google to revoke the token and deletes it from this PC.

### Saving and restoring the environment

ComfyUI itself, custom nodes, settings, workflows and the list of installed packages are saved to `My Drive/ComfyUI-Colab/environment`.
While ComfyUI runs, the parts that changed are saved every `SAVE_INTERVAL_SEC` seconds, and everything is saved again before the runtime is deleted.
A new runtime restores the environment from this save and installs the same package versions again.

Models and environments can be shared with the [ComfyUI instance on Colab](https://github.com/NeviumX/ComfyUI-instance-on-colab) notebook.
With the same `DRIVE_FOLDER`, an environment saved by either one can be restored by the other.

- If the environment cannot be saved to Google Drive, the runtime is not deleted. `stop.bat` asks whether to delete it without saving.
- If the saved environment was made with another Python version than the VM has, startup is aborted. Set `RESTORE_ENVIRONMENT=0` to start with a new environment.
- Some things are not part of the environment. Models stay in the models folder, outputs go to the local `outputs` folder, and the input and temp folders are not saved.
- A malicious custom node in the saved environment runs again on the next runtime. If you suspect one, start with `RESTORE_ENVIRONMENT=0`.

## Notes

- Closing the `start.bat` window does not stop the runtime, and it keeps being billed. When you are done, delete it with Stop in ComfyUI or with `stop.bat`.
- Deleting the runtime erases every file on Colab. What remains is the models and the saved environment on Google Drive, and the outputs copied to this PC.
- Do not save to the same `DRIVE_FOLDER` from two runtimes at once. If the notebook above uses the same folder, do not run both at the same time.
- ComfyUI runs as root on the Colab VM and can read and write the whole mounted Google Drive. Install only custom nodes you trust, and consider a Google account that holds no personal data.
- A Colab runtime accepts only one SSH connection at a time. To open a shell on the VM, use `./ssh.sh` instead of `colab ssh`.
- Messages are shown in Japanese on a Japanese system and in English otherwise. Set `COMFY_COLAB_LANG` to `ja` or `en` in `config.env` to choose one.
- To use a WSL distribution other than the default, set the environment variable `COMFY_COLAB_WSL_DISTRO` to its name before running the `.bat` files.
