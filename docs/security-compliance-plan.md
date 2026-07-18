# offline-voicememo セキュリティ・医療AIガイドライン準拠 修正計画

作成日: 2026-07-19 / 対象: 全コードベース精査（コード変更なし、計画のみ）

想定準拠先:
- 厚労省「医療情報システムの安全管理に関するガイドライン 第6.0版」
- 経産省・総務省「医療情報を取り扱う情報処理事業者における安全管理ガイドライン」（3省2ガイドライン）
- 個人情報保護法（要配慮個人情報＝病歴等）
- 厚労省 生成AI利用の留意事項（診療録はAI出力を医師が確認・責任を持つ）

---

## 問題点一覧

### 【P0 — Critical: 患者情報の外部流出リスク】

**C-1. オンラインモードで患者情報が無警告で外部APIへ送信される**
- 場所: `core/llm_client.py` `process()` / `main.py` `toggle_llm_mode()`
- 医療テンプレート（shosin / soap / medical_summary）選択中でも、`llm_mode=online` なら文字起こし全文（患者氏名・病歴を含みうる）が OpenAI 等の外部APIへそのまま送信される。同意確認・警告・ブロックが一切ない。3省2ガイドライン上、契約・安全管理措置のない外部事業者への要配慮個人情報の提供は不可。
- 修正方針:
  1. 医療テンプレート選択中は online モードをデフォルトでブロックする設定 `medical_templates_offline_only: true` を新設（デフォルト true）。
  2. ブロック時は rumps.alert で理由を表示し、オフライン処理へフォールバック。
  3. 設定でブロックを解除する場合も、送信直前に毎回確認ダイアログ（送信先URL・モデル名を明示）を出す。
  4. online 切替時（`toggle_llm_mode`）に「外部サーバーへ送信されます。患者情報を含む音声には使用しないでください」の警告を追加。

**C-2. macOS通知に文字起こし内容（患者情報になりうる）が表示される**
- 場所: `main.py:192` `notify("AI解析中...", raw_text[:80])`、`main.py:217` `notify("完了", processed[:100])`、`main.py:235` コピー時
- 通知センターに履歴が残り、ロック画面・画面共有中にも表示される。
- 修正方針: 通知本文から転写テキストを除去し、「文字数」「テンプレート名」等のメタ情報のみ表示。設定 `notify_content_preview: false`（デフォルト false）で明示的に有効化した場合のみプレビュー表示。

**C-3. クリップボード経由ペーストが Universal Clipboard / クリップボード履歴アプリに漏れる**
- 場所: `core/inserter.py`
- pyperclip（NSPasteboard 汎用書き込み）のため、(a) iCloud ユニバーサルクリップボードで他デバイスへ自動同期＝事実上の外部送信、(b) クリップボード履歴アプリに患者情報が永続保存される。
- 修正方針: pyobjc で NSPasteboard を直接操作し `org.nspasteboard.ConcealedType` を付与（履歴アプリ除外・Universal Clipboard 抑制）。pyperclip 依存を inserter から除去。復元失敗時に患者情報がクリップボードに残留しないよう、restore 失敗時はクリップボードをクリアする。

### 【P1 — High: 保存データ・認証情報の保護】

**H-1. 録音WAV・セッションJSON（原文＋整形結果）が無期限・平文で全件保存され、削除手段がない**
- 場所: `core/recorder.py` `stop()`、`main.py` `_save_session()`
- data/audio・data/sessions に全録音・全テキストが蓄積。保持期間・削除機能・暗号化なし。個人情報保護の最小化原則に反し、端末紛失時のリスクが大きい。
- 修正方針:
  1. 設定 `save_sessions: false` / `save_audio: false` を新設（デフォルト false＝保存しない。WAVは転写後即削除）。
  2. 保存を有効化した場合は保持日数 `retention_days`（デフォルト 7）を設け、起動時に期限切れファイルを自動削除。
  3. メニューに「保存データを今すぐ全削除」を追加。
  4. README/setup.sh に FileVault 有効化を必須事項として明記。

**H-2. 保存ファイルのパーミッションが緩い**
- 場所: `recorder.py`（wavfile.write）、`main.py` `_save_session()`、`config_manager.py` `save()`
- umask 依存（通常 644）で他ローカルユーザーから読める。
- 修正方針: data/ 配下と config/settings.json の作成時に `os.chmod(path, 0o600)`（ディレクトリは 0o700）を徹底。共通ヘルパー関数化。

**H-3. APIキーが settings.json に平文保存・ダイアログに平文表示**
- 場所: `main.py` `_run_online_config_dialog()`（default_text で既存キーを再表示）、`config/config_manager.py`
- 修正方針: `keyring` ライブラリで macOS Keychain に保存し、settings.json から `online_api_key` を廃止（読み込み時に旧キーがあれば Keychain へ移行して JSON から削除）。ダイアログの default_text には既存キーを出さず「設定済み（変更する場合のみ入力）」表示にする。

**H-4. config/dictionary.json・templates/*.txt が git 追跡対象**
- 場所: `.gitignore`
- 変換辞書に患者名・施設名を登録した場合、またカスタムプロンプトに機微情報を書いた場合、コミットに含まれる。
- 修正方針: `.gitignore` に `config/dictionary.json` と `templates/custom.txt` を追加。既に追跡済みなら `git rm --cached` を計画に含める。ユーザー編集テンプレートは `templates/user/` に分離して丸ごと ignore する構成も検討。

### 【P2 — Medium: 医療AI利用ガイドライン（出力品質・責任）】

**M-1. AI生成テキストが確認なしで診療録等へ直接ペーストされる**
- 場所: `main.py` `_process_audio()` → `inserter.insert()`
- LLMの幻覚（原文にない診断・数値の捏造）がそのまま電子カルテに入るリスク。診療録は医師の確認・責任下で記載する必要がある。
- 修正方針:
  1. 医療テンプレート使用時は挿入前に確認ウィンドウ（生成結果を表示し「挿入/コピーのみ/破棄」を選択）を出す設定 `medical_confirm_before_insert: true`（デフォルト true）。
  2. 医療テンプレート使用時は `show_raw_text` を実質デフォルトONにし、原文との対照確認を可能にする。

**M-2. 医療テンプレートに幻覚抑制指示がない**
- 場所: `templates/shosin.txt` `soap.txt` `medical_summary.txt`
- 「（記載なし）」指示はあるが、「文字起こしに含まれない情報を推測・補完しない」「診断名・薬剤名・用量は原文の表現を保持する」という明示指示がない。特に SOAP の A（評価・診断）欄は LLM が鑑別診断を勝手に生成しうる（プログラム医療機器該当性のグレーゾーンにも接近）。
- 修正方針: 全医療テンプレート冒頭に「原文にない情報の推測・追加を禁止」「診断・評価は発話内容の整形のみで、新たな診断提案をしない」「薬剤名・数値・単位は変更しない」を追記。system プロンプト（`llm_client.py`）にも同趣旨を追加。

**M-3. 医療用途で temperature 0.3**
- 場所: `core/llm_client.py` `process()`
- 修正方針: 医療テンプレート時は temperature=0 に固定（テンプレートごとの温度指定を可能にする）。

**M-4. 数値・薬剤名の誤転写リスクへの注意喚起がない**
- Whisper は用量・数値・薬剤名を誤認識しやすい。
- 修正方針: M-1 の確認ウィンドウに「数値・薬剤名・用量は必ず原文と照合してください」の注意文を常設表示。README にも運用上の注意（AI出力の最終確認義務、患者への録音説明）を明記。

### 【P3 — Low〜Medium: その他セキュリティ堅牢化】

**L-1. osascript へのインジェクション対策が不完全**
- 場所: `core/notifier.py`
- `"` → `'` 置換のみで `\` が未処理。転写テキスト由来の文字列が AppleScript ソースに埋め込まれる。C-2 対応で転写内容を通知に出さなくなればリスクは大幅減だが、堅牢化として `\` と `"` の両方をエスケープするか、`osascript -e 'on run argv...' 引数渡し` 方式に変更。

**L-2. オンラインAPI URL に http:// を設定できてしまう**
- 場所: `main.py` `_run_online_config_dialog()`、`llm_client.py`
- 修正方針: online モードの URL は https:// のみ許可（localhost/127.0.0.1 を除く）。保存時にバリデーションしエラー表示。

**L-3. pyautogui.FAILSAFE = False と誤ペースト先リスク**
- 場所: `core/inserter.py`
- フォーカスが意図しないアプリ（チャット・ブラウザ等）にあると患者情報を誤送信する。M-1 の挿入前確認で大部分は緩和されるが、確認ダイアログに「挿入先アプリ名」（NSWorkspace frontmostApplication）を表示するとより安全。FAILSAFE は True に戻す（マウス操作をしないため実害なし）。

**L-4. 依存パッケージのバージョン固定なし**
- 場所: `requirements.txt`（下限指定のみ）
- 修正方針: `pip freeze` ベースの lock ファイル（requirements.lock.txt）を導入し、setup.sh はそれを参照。定期的な `pip-audit` 実行を README に記載。

**L-5. 録音中であることの本人・第三者への明示が弱い**
- メニューバー表示のみ。ホットキー誤爆で気づかず録音が始まりうる。
- 修正方針: 録音開始・停止時にシステムサウンドを鳴らす設定（デフォルトON）。README に「患者の音声を録音する場合は説明と同意を得る」運用ルールを明記。

---

## 実装フェーズ計画（Opus 4.8 向け）

各フェーズは独立してコミット可能。上から順に実施。

### Phase 1: 外部流出の遮断（C-1, C-2, C-3）
1. `config_manager.py` DEFAULTS に `medical_templates_offline_only: true`, `notify_content_preview: false` を追加
2. `llm_client.py` に `is_medical_template()` ヘルパー（shosin/soap/medical_summary 判定、テンプレートのメタ情報化も可）
3. `main.py` `_process_audio()`: 医療テンプレ×online の組合せをブロック→アラート→オフライン処理 or 中断
4. `main.py` `toggle_llm_mode()`: online 切替時の警告ダイアログ
5. `notifier.py` 呼び出し全箇所から転写テキストを除去（メタ情報のみに）
6. `inserter.py` を NSPasteboard + ConcealedType 実装に置換
- 検証: 医療テンプレ選択→online で送信されないこと(ネットワークログ)、通知に本文が出ないこと、クリップボード履歴アプリに残らないこと

### Phase 2: 保存データ保護（H-1, H-2, H-4）
1. DEFAULTS に `save_sessions: false`, `save_audio: false`, `retention_days: 7` を追加
2. `_process_audio()` 完了時に WAV を削除（save_audio=false 時）、`_save_session()` をオプトイン化
3. 起動時の期限切れデータ自動削除処理
4. メニュー「保存データを全削除」追加
5. ファイル/ディレクトリ作成の chmod 600/700 ヘルパーを recorder / main / config_manager に適用
6. `.gitignore` に config/dictionary.json・templates/custom.txt 追加＋追跡解除
- 検証: デフォルト設定で data/ に何も残らないこと、パーミッション確認

### Phase 3: APIキーの Keychain 移行（H-3）
1. requirements.txt に `keyring` 追加
2. `config_manager.py` に keychain 読み書きラッパー、旧 settings.json キーの自動移行＋JSONから削除
3. `_run_online_config_dialog()` の既存キー非表示化
- 検証: 設定→再起動→接続テスト成功、settings.json にキーが残らないこと

### Phase 4: 医療AI出力の安全策(M-1〜M-4)
1. 医療テンプレ 3 ファイルに幻覚抑制指示を追記、system プロンプト強化
2. 医療テンプレ時 temperature=0
3. 挿入前確認ウィンドウ(結果表示＋挿入先アプリ名＋数値照合の注意文、「挿入/コピーのみ/破棄」)
4. 医療テンプレ時の原文対照表示
- 検証: 医療テンプレで挿入前確認が出ること、原文にない情報を補わないことのスポットチェック

### Phase 5: 堅牢化(L-1〜L-5)
1. notifier のエスケープ修正(または argv 渡し)
2. online URL の https バリデーション
3. FAILSAFE=True 復帰
4. requirements.lock.txt 導入、pip-audit を README に記載
5. 録音開始/停止サウンド
6. README に運用ルール(FileVault 必須、AI出力の最終確認義務、録音時の患者同意、データ保持方針)を新設

### 実装時の共通注意
- 既存の settings.json を読む処理は後方互換を維持(未知キーは DEFAULTS 補完済み)
- rumps のダイアログはメインスレッドから呼ぶこと(既存の `_ui()` キュー機構を使う)
- コミットは Conventional Commits + 日本語(例: `fix: 医療テンプレート使用時のオンライン送信をブロック`)
- config/settings.json・data/・audit_logs 相当の実データは読まない/表示しない
