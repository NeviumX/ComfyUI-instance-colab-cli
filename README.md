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

Models downloaded by ComfyUI-Manager and similar tools are saved to this folder as well, so the next runtime can use them.

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
