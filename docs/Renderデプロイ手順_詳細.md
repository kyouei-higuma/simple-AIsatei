# Render へのデプロイ手順（詳細）

Render の無料枠で査定アプリをホスティングすると、「Created by」「Hosted with Streamlit」が表示されなくなります。

---

## 事前準備

1. **GitHub アカウント** があること
2. **プロジェクトを GitHub に push 済み** であること
   - まだの場合は、GitHub でリポジトリを作成し `git push` でコードをアップロードしてください

---

## ステップ1: Render に登録

1. ブラウザで **https://render.com** を開く
2. 右上の **「Get Started」** をクリック
3. **「Sign up with GitHub」** を選択
4. GitHub の認証画面で **「Authorize Render」** をクリックして許可

---

## ステップ2: 新しい Web Service を作成

1. Render のダッシュボード（https://dashboard.render.com）にログイン
2. 右上の **「New +」** ボタンをクリック
3. 一覧から **「Web Service」** を選択

---

## ステップ3: GitHub リポジトリを接続

1. **「Connect a repository」** の欄に、GitHub のリポジトリ一覧が表示されます
2. デプロイしたい **査定アプリのリポジトリ** をクリック
3. 右側に **「Configure」** ボタンが出たらクリック

※リポジトリが表示されない場合：
   - **「Configure account」** から GitHub 連携を確認
   - 「All repositories」または該当リポジトリへのアクセスを許可

---

## ステップ4: サービス設定を入力

以下の項目を設定します。

| 項目 | 入力値 |
|------|--------|
| **Name** | 任意（例：`simple-aisatei`）。URL の一部になります |
| **Region** | `Singapore` または `Frankfurt`（日本からは Singapore が近い） |
| **Branch** | `main`（通常はこのままでOK） |
| **Root Directory** | 空欄のまま（プロジェクトがルートにある場合） |
| **Runtime** | `Python 3` |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `streamlit run app_simple.py --server.port=$PORT --server.address=0.0.0.0` |

### Start Command の説明

```
streamlit run app_simple.py --server.port=$PORT --server.address=0.0.0.0
```

- `app_simple.py` … 起動するアプリファイル（簡易版）
- `--server.port=$PORT` … Render が割り当てるポート番号を使用
- `--server.address=0.0.0.0` … 外部からのアクセスを受け付けるため

---

## ステップ5: 環境変数（任意）

Webhook や API キーを使う場合は、**Environment** で追加します。

1. **「Advanced」** をクリック
2. **「Add Environment Variable」** をクリック
3. 例：
   - `WEBHOOK_URL` = Google Chat の Webhook URL
   - `REINFOLIB_API_KEY` = 不動産情報ライブラリの API キー

使わない場合は省略して問題ありません。

---

## ステップ6: 無料プランの確認

1. 画面下部の **「Create Web Service」** をクリック
2. **Free** プランを選択（表示されていれば）
3. デプロイが自動で開始されます

---

## ステップ7: デプロイ完了を待つ

1. 画面上部に **ログ** が表示されます
2. **Build**（ビルド）が終わると **Deploy**（デプロイ）が始まります
3. ログに **「Your service is live at ○○○」** と表示されたら完了です

初回は 3〜5 分ほどかかることがあります。

---

## アプリの URL を確認する

1. 画面上部に表示される **URL**（例：`https://simple-aisatei-xxxx.onrender.com`）をクリック
2. ブラウザでアプリが開けば成功です  
   （「Created by」「Hosted with Streamlit」は表示されません）

---

## よくあるトラブル

### デプロイが失敗する

- **Build 失敗**  
  `requirements.txt` に問題がないか確認してください。

- **Listen エラー**  
  Start Command が正しいか確認してください。  
  `--server.address=0.0.0.0` を忘れずに追加してください。

### 15分使わないとスリープする（無料枠）

- 無料プランでは、約 15 分間アクセスがないとサービスがスリープします
- 次にアクセスしたとき、起動まで約 30 秒〜1 分かかります
- 常時稼働させたい場合は有料プラン（$7/月〜）が必要です

### CSV データが反映されない

- `data/reins_data_3years.csv` は GitHub に push されている必要があります
- `.gitignore` で除外していないか確認してください

---

## 設定変更や再デプロイ

1. Render ダッシュボードで対象の **Web Service** をクリック
2. 左メニューで **「Settings」** を開く
3. 設定を変更して **「Save Changes」** をクリックすると、自動で再デプロイされます

GitHub に push すると、自動で再デプロイされる場合もあります（**Auto-Deploy** が有効なとき）。
