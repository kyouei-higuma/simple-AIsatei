# Google Chat への査定結果送信 設定手順

AI査定アプリでお客様が査定を完了すると、お客様情報と査定結果が自動でGoogle Chatに送信されます。

---

## ステップ1: Google Chat で Webhook を作成する

### 1-1. チャットスペースを開く

1. [Google Chat](https://chat.google.com/) を開く
2. 査定結果を受け取りたい**スペース**（チャットルーム）を開く
   - 既存のスペースを使うか、新規作成してください

### 1-2. Webhook を追加する

1. スペース上部の**スペース名**をクリック
2. **「アプリとインテグレーションを管理」** をクリック
3. **「Webhookを管理」** をクリック
4. **「Webhookを追加」** をクリック

### 1-3. Webhook の設定

1. **名前**: 例）`AI査定通知` など分かりやすい名前を入力
2. **アバターURL**（任意）: アイコン画像のURLがあれば入力
3. **「保存」** をクリック

### 1-4. Webhook URL をコピーする

1. 作成後に表示される **Webhook URL** をコピー
2. URL の形式は次のようなものです：
   ```
   https://chat.googleapis.com/v1/spaces/XXXXX/messages?key=XXXXX&token=XXXXX
   ```
3. ⚠️ **重要**: このURLは認証情報を含むため、他人に共有しないでください

---

## ステップ2: AI査定アプリに Webhook URL を設定する

### 方法A: Streamlit Cloud でデプロイしている場合（重要）

**ローカルの `secrets.toml` は Streamlit Cloud では使われません。**

Streamlit Cloud 上で動くアプリは、あなたのPCではなくクラウド側で実行されます。そのため、PC内の `.streamlit/secrets.toml` は参照されず、**Streamlit Cloud のダッシュボードで設定した Secrets だけ**が使われます。

#### 設定手順（詳細）

1. **Streamlit Cloud にログイン**
   - ブラウザで [https://share.streamlit.io/](https://share.streamlit.io/) を開く
   - GitHub アカウントでログイン

2. **アプリ一覧から対象アプリを選択**
   - デプロイ済みのアプリ一覧が表示されます
   - `app_simple.py` を指定してデプロイしているアプリをクリック

3. **Settings を開く**
   - アプリ画面の右上にある **「⋮」（三点メニュー）** をクリック
   - メニューから **「Settings」** を選択

4. **Secrets タブを開く**
   - 左側のメニューまたはタブから **「Secrets」** をクリック
   - 「Secrets」というテキストエリアが表示されます

5. **Secrets を入力**
   - テキストエリアに、次の形式で **1行** 入力します（ローカルの `secrets.toml` と同じ形式）

   ```
   WEBHOOK_URL = "https://chat.googleapis.com/v1/spaces/XXXXX/messages?key=XXXXX&token=XXXXX"
   ```

   - `"..."` の中に、Google Chat で取得した Webhook URL を**そのまま**貼り付け
   - 例: `WEBHOOK_URL = "https://chat.googleapis.com/v1/spaces/AAQA6hF1DTs/messages?key=AIzaSy...&token=Tuq89tDGa1b-2OqvCCemgVTeS1IrVvyljs2ll5i8Y9I"
   - 注意: 前後に余分なスペースや改行を入れない

6. **Save をクリック**
   - 画面下部の **「Save」** ボタンをクリック

7. **アプリの再起動を待つ**
   - 保存後、Streamlit Cloud が自動でアプリを再起動します
   - 通常 **30秒〜1分程度** かかります
   - 再起動が完了するまで、古い設定のまま動作している可能性があります

8. **動作確認**
   - アプリのURL（例: `https://xxx.streamlit.app`）を**新しいタブ**で開き直す
   - 査定を実行して、Google Chat にメッセージが届くか確認

#### ローカルと Streamlit Cloud の違い

| 実行場所 | 設定ファイル | 設定の反映 |
|----------|---------------|----------------|
| **ローカル**（PCで `streamlit run`） | `.streamlit/secrets.toml` | このファイルが使われる |
| **Streamlit Cloud**（share.streamlit.io） | ダッシュボードの Secrets | ローカルの secrets.toml は**無視**される |

### 方法B: ローカルで実行している場合

1. プロジェクトフォルダ内に `.streamlit` フォルダがあることを確認
2. `.streamlit/secrets.toml` ファイルを作成（なければ新規作成）
3. 次の内容を記述して保存：

   ```toml
   WEBHOOK_URL = "https://chat.googleapis.com/v1/spaces/XXXXX/messages?key=XXXXX&token=XXXXX"
   ```

4. アプリを再起動

---

## ステップ3: 動作確認

1. AI査定アプリを開く
2. お名前・電話番号・メールアドレス・住所などを入力
3. **「査定を実行」** をクリック
4. 査定が完了すると、Google Chat のスペースに次のようなメッセージが届きます：

   ```
   【AI査定】新規お問い合わせ

   ■ お客様情報
   お名前: 山田 太郎
   電話番号: 090-1234-5678
   メール: example@email.com

   ■ 物件情報
   住所: 北海道旭川市神居一条18丁目
   種別: 中古住宅（戸建て）
   土地: 100.0㎡ / 建物: 80.0㎡ / 専有: -㎡
   築年数: 15年 / 角地: なし

   ■ 査定結果
   仮査定金額: 2,500万円
   ㎡単価: 12.5万円/㎡ / 坪単価: 41.3万円/坪 / 参照事例: 25件

   送信日時: 2025-02-24 15:30:00
   ```

5. 画面上に「お客様情報を送信しました。」と表示されれば成功です

---

## トラブルシューティング

### メッセージが届かない場合

1. **「送信に失敗しました」と表示される場合**
   - 査定結果画面の **「🔧 トラブルシューティング」** を開き、表示されるエラー内容を確認してください
   - HTTP 403/404: Webhook URL が無効または期限切れの可能性があります
   - 接続エラー: ネットワークまたは URL の形式を確認してください

2. **何も表示されない場合（送信成功/失敗のメッセージが出ない）**
   - **WEBHOOK_URL が未設定** の可能性があります
   - Streamlit Cloud: Settings → Secrets に `WEBHOOK_URL = "https://..."` が正しく記載されているか確認
   - ローカル: `.streamlit/secrets.toml` が存在し、正しい形式か確認
   - 設定後は **アプリの再起動** が必要です（Streamlit Cloud は自動で数十秒かかります）

3. **Webhook URL の形式**
   - 必ず `"` で囲む: `WEBHOOK_URL = "https://chat.googleapis.com/..."`
   - コピー時に前後のスペースや改行が入っていないか確認
   - Google Chat の URL は `https://chat.googleapis.com/v1/spaces/` で始まります

4. **スペースの権限**  
  Webhook を追加したスペースに、あなたが参加しているか確認してください

### Webhook を無効にしたい場合

1. Google Chat のスペース → 「アプリとインテグレーションを管理」
2. 「Webhookを管理」→ 該当 Webhook の「削除」または「無効化」

### 設定を削除したい場合

- **Streamlit Cloud**: Settings → Secrets から `WEBHOOK_URL` の行を削除
- **ローカル**: `.streamlit/secrets.toml` から `WEBHOOK_URL` の行を削除

---

## 補足

- 送信は**査定が正常に完了したときのみ**行われます
- 住所未入力などで査定が実行されなかった場合は送信されません
- Webhook URL を設定していない場合、送信処理はスキップされ、査定結果のみ表示されます
