# comfyui-instance-colab-cli

[English](README.md)

Google Colab の GPU で [ComfyUI](https://github.com/comfyanonymous/ComfyUI) を動かし、Windows のブラウザや
[Comfy Desktop](https://github.com/Comfy-Org/desktop) の Remote Connection から使うためのスクリプトです。
WSL 上の [Colab CLI](https://github.com/googlecolab/google-colab-cli) と SSH のポート転送を使うので、ComfyUI をインターネットに公開しません。

## 必要なもの

- Windows 10/11 と WSL2 の Ubuntu
- GPU を使える Colab のプラン。動かしている間はコンピューティングユニットを消費します

## 準備

WSL でこのフォルダに移動して実行します。初回の `colab usage` で Google アカウントの認証を行います。

```bash
bash ./setup-wsl.sh
colab usage
```

GPU やポートなどは `config.env` で変更できます。自分だけの設定は `config.local.env` に書くと、`config.env` を書き換えずに上書きできます。

## 使い方

| 操作 | Windows | WSL |
|---|---|---|
| ランタイムを作って ComfyUI を開く | `start.bat` | `./up.sh` |
| ランタイムを削除する | `stop.bat` | `./down.sh` |
| ComfyUI を更新する | `update.bat` | `./update.sh` |
| 状態を見る | `status.bat` | `./status.sh` |
| 閉じたトンネルをつなぎ直す | `start.bat` | `./tunnel.sh` |
| 出力を手元にコピーする | | `./pull-outputs.sh` |
| ログを見る | | `./logs.sh` |
| VM に入る | | `./ssh.sh` |

`start.bat` をダブルクリックすると、ランタイムの作成、ComfyUI の取得と起動、ポート転送までを行い、ブラウザで `http://localhost:8188` を開きます。

### Comfy Desktop から使う

`start.bat` の準備ができたら、Comfy Desktop で新しい **Remote Connection** を作り、URL に `http://localhost:8188` を入力します。
接続の設定で **Auto-download outputs** をオンにすると、生成した画像や動画が PC に保存されます。

### ComfyUI の画面から終わらせる

画面右上の「停止」ボタンから、次のどちらかを選べます。

- **接続を解除してランタイムを削除**: 出力を手元の `outputs` フォルダーにコピーしてから、ランタイムを削除します。課金が止まります。
- **接続だけを解除**: トンネルだけを閉じます。ランタイムと ComfyUI は動き続け、課金も続きます。`start.bat` でつなぎ直せます。

ボタンの操作は `start.bat` のウィンドウが実行するので、使っている間はそのウィンドウを開いたままにしてください。

同じダイアログの **出力を ZIP でダウンロード** を押すと、Colab 上の出力をまとめて 1 つの ZIP にし、ブラウザのダウンロードとして受け取れます。
出力が多いと、受け取るまでに時間がかかります。

### ComfyUI の更新

起動のたびに、ComfyUI の新しいリリースがあるかを確かめ、あれば `start.bat` のウィンドウに表示します。

- `update.bat` を実行すると、動いているランタイムの ComfyUI を最新のリリースに更新して、起動し直します。終わったら画面を再読み込みしてください。
  WSL で `./update.sh v0.3.60` のように実行すると、版を選べます。
- `config.env` の `AUTO_UPDATE=1` にすると、新しいリリースがあれば、更新してから起動します。既定は 0 で、表示するだけです。
- カスタムノード、設定、ワークフローはそのまま残ります。更新できたら、環境を Google ドライブに保存します。
- 更新できなかったときは元の版に戻し、Google ドライブには保存しません。
- ComfyUI-Manager の「Update ComfyUI」は使わないでください。更新の途中で定期保存が走ると、食い違った環境が保存されることがあります。

### SageAttention と起動オプション

`config.env` の `USE_SAGE_ATTENTION=1` が既定で、起動のときに SageAttention を入れて、その GPU で試します。
使えれば `--use-sage-attention` を付けて起動し、使えなければ理由を表示して標準の attention で起動します。

ComfyUI の起動オプションは、`config.env` の `COMFY_EXTRA_ARGS` に空白で区切って書きます。例: `COMFY_EXTRA_ARGS='--fast fp16_accumulation'`。
新しく ComfyUI を起動するときに反映されます。

### Google ドライブのモデル

`USE_DRIVE=1` のとき、起動時に Google ドライブをマウントし、`マイドライブ/ComfyUI-Colab/models` のモデルを ComfyUI に追加します。
初回は `start.bat` のウィンドウに認可の URL が表示されるので、ブラウザで開いて許可し、ウィンドウで Enter を押します。

```text
マイドライブ/
└── ComfyUI-Colab/
    └── models/
        ├── checkpoints/
        ├── diffusion_models/
        ├── text_encoders/
        ├── vae/
        ├── loras/
        └── ...
```

ComfyUI-Manager などがダウンロードしたモデルも、このフォルダーに保存されるので、次のランタイムでも使えます。

### 環境の保存と復元

ComfyUI 本体、カスタムノード、設定、ワークフロー、インストールしたパッケージの一覧を、`マイドライブ/ComfyUI-Colab/environment` に保存します。
起動中は `SAVE_INTERVAL_SEC` ごとに変わった部分だけを保存し、ランタイムを削除する前にも保存します。
新しいランタイムでは、この保存から環境を復元し、同じ版のパッケージを入れ直します。

[ComfyUI instance on Colab](https://github.com/NeviumX/ComfyUI-instance-on-colab) のノートブックと、モデルと環境を共有できます。
`DRIVE_FOLDER` を同じ名前にしておけば、どちらで保存した環境も、もう一方で復元できます。

- 環境を Google ドライブに保存できなかったときは、ランタイムを削除しません。`stop.bat` では、保存せずに削除するかを尋ねます。
- 保存した環境と VM の Python の版が違うときは、起動を中止します。新しい環境で始めるときは `RESTORE_ENVIRONMENT=0` にします。
- 保存しないものもあります。モデルは models フォルダーに、出力は手元の `outputs` に置かれます。input と temp のフォルダーは保存しません。
- 保存した環境に悪意のあるカスタムノードが入ると、次のランタイムでも実行されます。不審なときは `RESTORE_ENVIRONMENT=0` で始めてください。

## 注意

- `start.bat` のウィンドウを閉じてもランタイムは動き続け、課金されます。使い終わったら、画面の「停止」か `stop.bat` で削除してください。
- ランタイムを削除すると、Colab 上のファイルはすべて消えます。残るのは、Google ドライブのモデルと保存した環境、手元にコピーした出力です。
- 同じ `DRIVE_FOLDER` に、2 つのランタイムから同時に保存しないでください。上のノートブックと同じフォルダーを使う場合も、同時には動かさないでください。
- ComfyUI は Colab VM 上で root として動き、マウントした Google ドライブ全体を読み書きできます。信頼できるカスタムノードだけを入れ、個人のデータを置いていない Google アカウントを使うことをおすすめします。
- Colab のランタイムには同時に 1 本しか SSH をつなげません。VM に手で入るときは `colab ssh` ではなく `./ssh.sh` を使ってください。
- 表示は、日本語の環境なら日本語、それ以外なら英語になります。`config.env` の `COMFY_COLAB_LANG` を `ja` か `en` にすると固定できます。
- 既定以外の WSL のディストリビューションを使うときは、環境変数 `COMFY_COLAB_WSL_DISTRO` に名前を設定してから `.bat` を実行します。
