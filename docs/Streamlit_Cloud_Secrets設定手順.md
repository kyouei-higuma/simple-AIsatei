# Streamlit Cloud で Google Chat Webhook を設定する手順

デプロイ済みアプリ（例: https://appsimplepy-xxx.streamlit.app）に、査定結果を Google Chat に送る設定を行う手順です。

---

## 前提：Google Chat の Webhook URL を取得済みであること

まだ取得していない場合は、先に [Google_Chat_設定手順.md](Google_Chat_設定手順.md) の「ステップ1」で Webhook を作成し、URL をコピーしてください。

---

## ステップ1: Streamlit Cloud のダッシュボードを開く

1. ブラウザで [https://share.streamlit.io/](https://share.streamlit.io/) を開く
2. GitHub アカウントでログイン（未ログインの場合）
3. デプロイ済みの**アプリ一覧**が表示されます

---

## ステップ2: 対象アプリを開く

1. 一覧から **app_simple.py** を指定してデプロイしたアプリをクリック
2. アプリの画面（査定フォームが表示されているページ）が開きます

---

## ステップ3: Settings を開く

1. 画面**右上**を確認
2. **「⋮」**（縦3点のメニュー）をクリック
3. 表示されたメニューから **「Settings」** をクリック

> 注意: アプリの**中**（査定フォームの上）ではなく、Streamlit Cloud の**外枠・ヘッダー部分**のメニューです。

---

## ステップ4: Secrets タブを選択

1. Settings 画面が開いたら、左側のメニューを確認
2. **「Secrets」** をクリック
3. テキストエリア（入力欄）が表示されます

---

## ステップ5: Webhook URL を入力

テキストエリアに、次の形式で **1行** 入力します。

```
WEBHOOK_URL = "ここにGoogle_ChatのWebhook_URLを貼り付け"
```

### 入力例

```
WEBHOOK_URL = "https://chat.googleapis.com/v1/spaces/AAQA6hF1DTs/messages?key=AIzaSyDdI0hCZtE6vySjMm-WEfRq3CPzqKqqsHI&token=Tuq89tDGa1b-2OqvCCemgVTeS1IrVvyljs2ll5i8Y9I"
```

### 注意点

| 項目 | 説明 |
|------|------|
| **引用符** | 必ず**半角**の `"` を使用。Word などからコピーすると `"`（スマートクォート）になることがあるので注意 |
| **形式** | `WEBHOOK_URL = "URL"` の形。`=` の前後にスペースを入れてもOK |
| **改行** | 1行で入力。余分な改行やスペースを入れない |
| **URL** | Google Chat で取得した Webhook URL をそのまま貼り付け |

---

## ステップ6: 保存する

1. 画面下部の **「Save」** ボタンをクリック
2. 保存が完了すると、Streamlit Cloud が自動でアプリを再起動します

---

## ステップ7: 再起動を待つ

- 通常 **30秒〜1分** かかります
- 画面上で「再デプロイ中」などの表示が出ることがあります
- 再起動が完了するまで、古い設定のまま動作している場合があります

---

## ステップ8: 動作確認

1. アプリの URL を**新しいタブ**で開き直す  
   例: `https://appsimplepy-aq5imo7g2rgzjoadzekymy.streamlit.app`
2. 住所・面積などを入力して **「査定を実行」** をクリック
3. 査定が完了すると、Google Chat の Webhook を設定したスペースにメッセージが届くか確認

### 届いた場合

- 「お客様情報を送信しました。」と表示されれば成功です

### 届かない場合

- アプリ上に「送信に失敗しました」と表示されていないか確認
- Secrets の入力（引用符・スペル・URL）が正しいか再確認
- 保存後、1〜2分待ってから再度査定を実行

---

## トラブルシューティング

### 「Settings」が見つからない

- アプリの**中**（査定フォームの上）ではなく、Streamlit Cloud の**外枠**（ブラウザのタブやウィンドウの上部）を確認してください
- ログインしていない場合は、先に [share.streamlit.io](https://share.streamlit.io/) でログインしてください

### メッセージが届かない

- 引用符が半角 `"` であることを確認
- Webhook を追加した Google Chat のスペースに、あなたが参加しているか確認
- ブラウザの開発者ツールでネットワークエラーが出ていないか確認

### 設定を変更したい場合

- 再度 Settings → Secrets を開き、内容を編集して Save をクリック
- 保存後、1分ほど待ってから動作確認してください

---

以上で設定は完了です。
