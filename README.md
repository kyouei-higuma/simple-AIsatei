# 不動産仮査定アプリ

Streamlitで作成した不動産仮査定アプリです。国土交通省の「不動産情報ライブラリAPI」を使用して、入力された住所周辺の中古マンション取引データをもとに仮査定を算出します。

## 必要な環境

- Python 3.8以上

## インストール方法

```bash
# プロジェクトフォルダに移動
cd 不動産仮査定Project

# 仮想環境の作成（推奨）
python -m venv venv

# 仮想環境の有効化
# Windows:
venv\Scripts\activate
# Mac/Linux:
# source venv/bin/activate

# 必要なライブラリのインストール
pip install -r requirements.txt
```

## APIキーの取得

本アプリは国土交通省の「不動産情報ライブラリAPI」を使用します。**2024年4月よりAPI利用には申請が必要**です。

1. [不動産情報ライブラリ](https://www.reinfolib.mlit.go.jp/) にアクセス
2. 利用申請フォームから申請
3. 審査後にAPIキーがメールで送付されます

### 環境変数の設定

取得したAPIキーを環境変数に設定してください：

```bash
# Windows (PowerShell)
$env:REINFOLIB_API_KEY = "あなたのAPIキー"

# Windows (コマンドプロンプト)
set REINFOLIB_API_KEY=あなたのAPIキー

# Mac/Linux
export REINFOLIB_API_KEY="あなたのAPIキー"
```

## アプリの起動

```bash
streamlit run main.py
```

ブラウザが自動で開き、アプリが表示されます。

## 機能

- 住所入力フォームによる検索
- 住所周辺（半径5km以内）の**過去10年間**の取引データ取得
- ㎡単価の平均値算出と仮査定金額の計算
- 古いデータの場合：**AI補正アドバイス**（OpenAI API使用時）またはルールベースの補正アドバイス
- 公示地価・基準地価の表示、地図表示

### AI補正アドバイス（任意）

環境変数 `OPENAI_API_KEY` を設定すると、古い取引データに対する**AIによる補正アドバイス**が表示されます。未設定の場合はルールベースのアドバイスが表示されます。

## データの出典

本アプリで使用するデータは「国土交通省　不動産情報ライブラリ」より取得しています。参考情報としての利用を想定しており、重要事項説明等には保証されません。
