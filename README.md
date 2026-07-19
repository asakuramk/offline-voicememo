# Offline Voice Memo Processor

macOS メニューバー常駐の音声メモアプリ。ホットキー（既定: Option キー）で録音 →
ローカル（faster-whisper）で文字起こし → LLM で整形 → カーソル位置へ貼り付けます。
既定はすべてローカル完結で、患者情報などの要配慮情報を端末外へ出さない運用を前提にしています。

## セットアップ

```bash
./setup.sh          # venv 作成・依存インストール・lock生成・脆弱性スキャン
./run.sh            # 起動（または source venv/bin/activate && python main.py）
```

初回起動前に **アクセシビリティ権限**（Option キー検知）と **マイク権限** を付与してください
（システム設定 > プライバシーとセキュリティ）。ローカル整形には LM Studio を起動し、
Local Server（既定 `http://localhost:1234`）を開始しておきます。

---

## セキュリティ・医療利用の運用ルール（必読）

本アプリで患者情報など要配慮個人情報を扱う場合は、以下を必ず守ってください。
関連ガイドライン: 厚労省「医療情報システムの安全管理に関するガイドライン」、
3省2ガイドライン、個人情報保護法（要配慮個人情報）。

### 1. 端末の暗号化（必須）
録音・設定・辞書はローカル保存されます。紛失・盗難に備え、
**FileVault（ディスク暗号化）を必ず有効化**してください
（システム設定 > プライバシーとセキュリティ > FileVault）。

### 2. オフライン処理を原則とする
- 医療テンプレート（問診 / SOAP / 医療サマリー）使用中は、オンラインモードでも
  外部APIへ送信されず、自動的にローカル処理へフォールバックします
  （設定 `medical_templates_offline_only`、既定 `true`）。
- オンラインモードは外部API（OpenAI 等）へ本文を送信します。
  **患者情報を含む音声には使用しないでください。**
  外部事業者の利用には別途、契約・安全管理措置の確認が必要です。

### 3. AI出力の最終確認は人が行う（医師の責任）
- 生成AIは幻覚（原文にない情報の創作）や誤転写を起こします。
- 医療テンプレート使用時は挿入前に確認ダイアログが表示されます
  （設定 `medical_confirm_before_insert`、既定 `true`）。
  **数値・薬剤名・用量・単位は必ず原文と照合**し、
  原文にない情報が追加されていないか確認してから記録してください。
- 診療録への記載内容は、確認・修正した医師の責任で確定してください。

### 4. 録音時の説明と同意
患者の音声を録音する場合は、事前に説明し同意を得てください。
録音開始/停止時にはシステムサウンドが鳴ります（設定 `record_sounds`）。

### 5. 保存データの最小化
- 既定では録音音声・セッション記録を端末に残しません
  （`save_audio` / `save_sessions` はいずれも既定 `false`）。
- 保存を有効にした場合も、保持日数（`retention_days`、既定7日）を過ぎたデータは
  起動時に自動削除されます。メニュー「保存データを全削除」で即時削除も可能です。
- 保存ファイルはオーナー限定（0600/0700）で作成されます。

### 6. 認証情報の管理
- オンラインAPIキーは `settings.json` に平文保存されず、**macOS Keychain** に保存されます。
- オンラインAPIのURLは `https://` のみ許可されます（`http://` は localhost のみ）。

### 7. 依存パッケージの監査
`setup.sh` は `requirements.lock.txt` を生成し `pip-audit` を実行します。
定期的に `pip-audit` を実行し、脆弱性のある依存を更新してください。

---

## 主な設定（config/settings.json）

`settings.json` と `dictionary.json` は git 管理外（`.gitignore`）です。
初期辞書は `dictionary.sample.json` を参照してください。

| キー | 既定 | 説明 |
|------|------|------|
| `whisper_model` | `small` | 文字起こしモデル（tiny/small/medium/large） |
| `llm_mode` | `offline` | `offline`(LM Studio) / `online`(外部API) |
| `medical_templates_offline_only` | `true` | 医療テンプレ時は外部送信を禁止しローカル処理 |
| `medical_confirm_before_insert` | `true` | 医療テンプレ時、挿入前に確認ダイアログ |
| `notify_content_preview` | `false` | 通知に本文プレビューを含めるか |
| `save_audio` / `save_sessions` | `false` | 録音・セッションを端末に保存するか |
| `retention_days` | `7` | 保存データの保持日数（起動時に自動削除） |
| `record_sounds` | `true` | 録音開始/停止時のサウンド |
| `show_raw_text` | `false` | 出力に文字起こし原文を併記 |

---

## 開発メモ

パイプライン単体テスト（UIなし）:

```bash
python test_pipeline.py --template soap
```
