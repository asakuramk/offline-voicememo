#!/bin/bash
set -e

echo "========================================"
echo "  Offline Voice Memo Processor Setup"
echo "========================================"

# Python check
if ! command -v python3 &>/dev/null; then
    echo "[ERROR] Python 3 が見つかりません"
    exit 1
fi
echo "[OK] Python: $(python3 --version)"

# Create virtual environment
if [ ! -d "venv" ]; then
    echo "-> 仮想環境を作成中..."
    python3 -m venv venv
fi

source venv/bin/activate
echo "[OK] 仮想環境: $(which python)"

# Upgrade pip
pip install --upgrade pip -q

# Install dependencies
echo "-> 依存パッケージをインストール中..."
pip install -r requirements.txt

# Record the exact installed versions for reproducibility / auditing.
echo "-> インストール済みバージョンを requirements.lock.txt に記録..."
pip freeze > requirements.lock.txt

# Vulnerability scan (best effort; does not fail setup)
echo "-> 依存パッケージの脆弱性をスキャン (pip-audit)..."
pip install -q pip-audit && pip-audit || echo "[注意] pip-audit をスキップ/失敗しました（後で 'pip-audit' を実行してください）"

echo ""
echo "========================================"
echo "  セットアップ完了!"
echo "========================================"
echo ""
echo "起動方法:"
echo "  source venv/bin/activate && python main.py"
echo ""
echo "または:"
echo "  ./run.sh"
echo ""
echo "初回起動前に以下の権限を付与してください:"
echo "  1. アクセシビリティ権限 (Optionキー検知に必要)"
echo "     システム設定 > プライバシーとセキュリティ > アクセシビリティ"
echo "     -> このスクリプトを実行したターミナルアプリを追加"
echo ""
echo "  2. マイク権限 (録音に必要)"
echo "     システム設定 > プライバシーとセキュリティ > マイク"
echo "     -> ターミナルアプリを追加"
echo ""
echo "LM Studio の準備:"
echo "  1. LM Studio を起動してモデルをロード"
echo "  2. Local Server を開始 (デフォルト: http://localhost:1234)"
echo "  3. config/settings.json の lmstudio_model をロードしたモデル名に変更"
echo ""
echo "Whisper モデルサイズの目安:"
echo "  tiny   : 最速・精度低  (約 75MB)"
echo "  small  : バランス型   (約 460MB) ← デフォルト"
echo "  medium : 精度高め     (約 1.5GB)"
echo "  large  : 最高精度     (約 3GB)"
