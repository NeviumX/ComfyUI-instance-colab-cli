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
| モデルをダウンロードする | `model-download.bat` | `./model-download.sh` |
| Drive API を認可し直す・取り消す | `drive-auth.bat` | `./drive-auth.sh` / `./drive-auth.sh revoke` |
| 状態を見る | `status.bat` | `./status.sh` |
| 閉じたトンネルをつなぎ直す | `start.bat` | `./tunnel.sh` |
| 出力を手元にコピーする | | `./pull-outputs.sh` |
| ログを見る | | `./logs.sh` |
| VM に入る | | `./ssh.sh` |
| ComfyUI の利用者として VM に入る | | `./ssh.sh app` |

`start.bat` をダブルクリックすると、ランタイムの作成、ComfyUI の取得と起動、ポート転送までを行い、ブラウザで `http://localhost:8188` を開きます。

### Comfy Desktop から使う

`start.bat` の準備ができたら、Comfy Desktop で新しい **Remote Connection** を作り、URL に `http://localhost:8188` を入力します。
接続の設定で **Auto-download outputs** をオンにすると、生成した画像や動画が PC に保存されます。

### ComfyUI の画面から終わらせる

画面右上の「停止」ボタンから、次のどちらかを選べます。

- **接続を解除してランタイムを削除**: 出力を手元の `outputs` フォルダーにコピーしてからランタイムを削除します。課金が止まります。
- **接続だけを解除**: トンネルだけを閉じます。ランタイムと ComfyUI は動き続け、課金も続きます。`start.bat` でつなぎ直せます。

ボタンの操作は `start.bat` のウィンドウが実行するので、使っている間はそのウィンドウを開いたままにしてください。

同じダイアログの **出力を ZIP でダウンロード** を押すと Colab 上の出力をまとめて 1 つの ZIP にし、ブラウザのダウンロードとして受け取れます。
出力が多いと、受け取るまでに時間がかかります。

### モデルのダウンロード

ランタイムが動いている間に、Hugging Face と Civitai のモデルを Colab にダウンロードできます。方法は 2 つあります。

- **ComfyUI の画面:** 右上の「モデル」ボタンを押し、保存先のフォルダーを選んで URL を貼り付け、「ダウンロード」を押します。
  進み具合はプログレスバーに表示されます。ダウンロードは Colab で進むので、画面を閉じても止まりません。
- **model-download.bat:** フォルダーの番号を選び、URL を貼り付け、保存する場所を選びます。プログレスバーが表示され、終わるとウィンドウが閉じます。
  失敗したときだけ、理由を読めるようにウィンドウが残ります。

URL は、Hugging Face ならファイルのページ、Civitai ならバージョンを選んだモデルのページを、ブラウザからコピーして貼り付けます。

保存する場所は次の 3 つから選べます。

- **ランタイムのみ:** ランタイムにダウンロードするだけです。台帳には記録しないのでランタイムを削除すると一覧からも消えます。
- **ランタイムと台帳に記録:** ランタイムを削除すると消えますが、台帳に記録されるので次のランタイムでもモデルの一覧に出ます。
  ノードで使われたときに、記録したダウンロード元からランタイムへダウンロードし直します。
- **ランタイムとGoogleドライブ:** ランタイムにダウンロードして台帳に記録し、その後バックグラウンドでドライブにコピーします。
  ランタイムに落ちた時点で ComfyUI ですぐ使えます。コピーの進み具合は画面の右下に表示されます。
  `model-download.bat` では、ダウンロードに続けてウィンドウの中でコピーし、終わってからウィンドウが閉じます。

ダウンロードが終わったら、ComfyUI の画面で R キーを押すとモデルの一覧に出ます。同じ名前のファイルがあるときは上書きせずに断ります。

認証が必要なモデルを落とすときは、`config.local.env` にトークンを書いてから `start.bat` で起動します。

```bash
HF_TOKEN=hf_...
CIVITAI_TOKEN=...
```

トークンは、そのモデルのサイトにだけ送ります。

### モデルの台帳

このツールでダウンロードしたモデルは「ランタイムのみ」を選んだときを除き、保存先とダウンロード元を台帳に記録します。
Hugging Face のモデルは、ダウンロードしたときのコミットに固定して記録するので、後で同じ中身を取り直せます。

- 台帳は、Google ドライブを使うときは `マイドライブ/ComfyUI-Colab/model-sources.json`、使わないときはこのフォルダーの `model-sources.json` に置きます。
  手元の台帳は起動のたびにランタイムへ送り、ランタイムで増えた記録は手元へ戻すので、どちらでもモデルの記録が引き継がれます。
- 台帳にあるモデルは、ランタイムにもドライブにも無くても、ComfyUI のモデルの一覧に出ます。
  モデルのダウンロードのダイアログの「台帳のモデル」から台帳を外すと、一覧に出なくなります。ファイルは消しません。
- ComfyUI-Manager などがダウンロードしたモデルは、ダウンロード元が分からないので台帳には入りません。

### ComfyUI の更新

起動のたびに ComfyUI の新しいリリースがあるかを確かめ、あれば `start.bat` のウィンドウに表示します。

- `update.bat` を実行すると、動いているランタイムの ComfyUI を最新のリリースに更新して、起動し直します。終わったら画面を再読み込みしてください。
  WSL で `./update.sh v0.3.60` のように実行すると、版を選べます。
- `config.env` の `AUTO_UPDATE=1` にすると、新しいリリースがあれば、更新してから起動します。既定は 0 で、表示するだけです。
- カスタムノード、設定、ワークフローはそのまま残ります。更新できたら、環境を Google ドライブに保存します。
- 更新できなかったときは元の版に戻し、Google ドライブには保存しません。
- ComfyUI-Manager の「Update ComfyUI」は使わないでください。更新の途中で定期保存が走ると、食い違った環境が保存されることがあります。

### SageAttention と起動オプション

`config.env` の `USE_SAGE_ATTENTION=1` が既定で、起動のときに SageAttention を入れてその GPU で試します。
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

ComfyUI は、ランタイムに写しがあればそちらを先に読みます。写しが無いモデルがノードで使われると、ランタイムへ取ってきてから読み込みます。
大きなモデルでもマウントしたドライブから直接読むより速く読み込めることが多いです。
取ってくる順は `config.env` の `MODEL_SOURCE` で選べます。

- **`MODEL_SOURCE=drive`:** 既定です。Drive API で並列にダウンロード、ドライブから順番にコピー、台帳のダウンロード元の順に試します。
- **`MODEL_SOURCE=url`:** 台帳のダウンロード元を先に試し、取得できなければドライブから取ります。

取得中はComfyUI の画面の右下に進み具合を表示します。どの方法でも取得できなかったときやランタイムのディスクの空きが足りないときはドライブから直接読みます。
台帳に同じ名前のモデルがあってもドライブのモデルとファイルサイズが違う場合はドライブのほうを使用します。

ComfyUI-Manager などがダウンロードしたモデルはまずランタイムに保存され、書き終わってから自動でドライブにもコピーされるので、次のランタイムでも使えます。

### Drive API の準備（任意）

Drive API を使うとドライブのモデルを並列にダウンロードできます。自分の Google Cloud のプロジェクトに OAuth クライアントを作り、一度だけ認可します。
準備しなくても動きますが、そのときはドライブから順番にコピーします。

1. [Google Cloud コンソール](https://console.cloud.google.com/) でプロジェクトを作ります。請求先アカウントをつなぐ必要はありません。
2. 「API とサービス」の「ライブラリ」で **Google Drive API** を有効にします。
3. 「OAuth 同意画面」を作ります。対象は「外部」にし、自分の Google アカウントをテストユーザーに加えます。
4. 「認証情報」で「OAuth クライアント ID」を作ります。アプリケーションの種類は **デスクトップ アプリ** です。
5. 作ったクライアントの JSON をダウンロードし、このフォルダーに `drive-oauth-client.json` という名前で置きます。

次に `start.bat` で起動すると、ドライブのマウントの後に認可の URL が表示され、ブラウザが開きます。Google アカウントで許可すると、ブラウザに「認可しました」と表示されます。

- APIのドライブへの許可はリードオンリーです。
- 長く使える更新用のトークンはこのフォルダーの `.state/drive-token.json` にだけ保存します。Colab の VM には 1 時間ほどで切れるトークンだけを送り、`start.bat` のウィンドウが 40 分ごとに送り直します。
- OAuth 同意画面を「テスト」のままにすると、7 日ごとに認可し直しになります。無効になると、次の起動のときに認可の URL がもう一度表示されます。
  「本番環境」にすると切れなくなりますが、許可するときに「確認されていないアプリ」の警告が出ます。自分で作ったアプリならそのまま進んでかまいません。
- 認可をやめるときは `drive-auth.bat revoke` を実行します。Google に取り消しを頼み、手元のトークンを消します。

### 環境の保存と復元

ComfyUI 本体、カスタムノード、設定、ワークフロー、インストールしたパッケージの一覧を、`マイドライブ/ComfyUI-Colab/environment` に保存します。
起動中は `SAVE_INTERVAL_SEC` ごとに変わった部分だけを保存し、ランタイムを削除する前にも保存します。
新しいランタイムでは、この保存から環境を復元し、同じ版のパッケージを入れ直します。

[ComfyUI instance on Colab](https://github.com/NeviumX/ComfyUI-instance-on-colab) のノートブックと、モデルと環境を共有できます。
`DRIVE_FOLDER` を同じ名前にしておけば、どちらで保存した環境ももう一方で復元できます。

- 環境を Google ドライブに保存できなかったときはランタイムを削除しません。`stop.bat` では保存せずに削除するかを尋ねます。
- 保存した環境と VM の Python の版が違うときは起動を中止します。新しい環境で始めるときは `RESTORE_ENVIRONMENT=0` にします。
- 保存しないものもあります。モデルは models フォルダーに、出力は手元の `outputs` に置かれます。input と temp のフォルダーは保存しません。
- 保存した環境に悪意のあるカスタムノードが入ると次のランタイムでも実行されます。不審なときは `RESTORE_ENVIRONMENT=0` で始めてください。

### ComfyUI を動かす利用者

ComfyUI とカスタムノードは、Colab VM 上で root ではない利用者 `comfy` として動きます。
カスタムノードからは、Google ドライブの `DRIVE_FOLDER/models` 以外のファイルと、`HF_TOKEN` などのトークンには触れません。`DRIVE_FOLDER/models` も読み取り専用です。

root の権限が必要なカスタムノードは動きません。VM で `pip` などを使うときは、`./ssh.sh app` で ComfyUI と同じ利用者として入ります。

カスタムノードが動かないなどの問題が起きたときは、次の手順で ComfyUI を root で動かせます。

1. `config.local.env` に `ISOLATE_COMFY=0` と書きます。
2. `stop.bat` でランタイムを削除します。
3. `start.bat` で起動します。

## 注意

- `start.bat` のウィンドウを閉じてもランタイムは動き続け、課金されます。使い終わったら画面の「停止」か `stop.bat` で削除してください。
- ランタイムを削除すると Colab 上のファイルはすべて消えます。残るのは Google ドライブのモデルと保存した環境と手元にコピーした出力です。
- 同じ `DRIVE_FOLDER` に、2 つのランタイムから同時に保存しないでください。
- カスタムノードはComfyUI のフォルダーの中のファイルを読み書きでき、外部通信もできます。信頼できるものだけを入れてください。
- Colab のランタイムには同時に 1 本しか SSH をつなげません。VM に手で入るときは `colab ssh` ではなく `./ssh.sh` を使ってください。
- 表示言語は日本語の環境なら日本語、それ以外なら英語になります。`config.env` の `COMFY_COLAB_LANG` を `ja` か `en` にすると固定できます。
- 既定以外の WSL のディストリビューションを使うときは環境変数 `COMFY_COLAB_WSL_DISTRO` に名前を設定してから `.bat` を実行します。
