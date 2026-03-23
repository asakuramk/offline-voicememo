"""
Pipeline test script (no menu bar UI).
Lets you test recording -> transcription -> LLM processing from the terminal.
Usage:
    python test_pipeline.py [--template memo|minutes|tasks|journal|summary|raw]
"""
import argparse
import sys
import time
from pathlib import Path

# Ensure project root is on the path
sys.path.insert(0, str(Path(__file__).parent))

from config.config_manager import ConfigManager
from core.recorder import Recorder
from core.transcriber import Transcriber
from core.llm_client import LLMClient

BASE_DIR = Path(__file__).parent


def main():
    parser = argparse.ArgumentParser(description="Voice memo pipeline test")
    parser.add_argument("--template", default=None, help="Template name to use")
    parser.add_argument("--audio", default=None, help="Use existing audio file instead of recording")
    args = parser.parse_args()

    config = ConfigManager(BASE_DIR / "config" / "settings.json")
    settings = config.load()

    if args.template:
        settings["active_template"] = args.template

    print(f"[設定]")
    print(f"  Whisperモデル : {settings['whisper_model']}")
    print(f"  言語          : {settings['whisper_language']}")
    print(f"  テンプレート  : {settings['active_template']}")
    print(f"  LM Studio URL : {settings['lmstudio_url']}")
    print(f"  モデル        : {settings['lmstudio_model']}")
    print()

    if args.audio:
        audio_path = Path(args.audio)
        print(f"[音声ファイル] {audio_path}")
    else:
        recorder = Recorder()
        print("Enterキーで録音開始...")
        input()
        print("[録音中] Enterキーで停止")
        recorder.start()
        input()
        audio_path = recorder.stop()
        print(f"[録音完了] {audio_path}")

    print()
    print("[文字起こし中...]")
    t0 = time.time()
    transcriber = Transcriber(settings)
    raw_text = transcriber.transcribe(audio_path)
    print(f"  完了 ({time.time() - t0:.1f}s)")
    print()
    print("=" * 50)
    print("【生文字起こし】")
    print(raw_text)
    print("=" * 50)

    if settings["active_template"] == "raw":
        print("\n[テンプレート: raw] LLM処理をスキップします")
        return

    print()
    print("[LLM処理中...]")
    t0 = time.time()
    llm = LLMClient(settings)
    try:
        processed = llm.process(raw_text)
        print(f"  完了 ({time.time() - t0:.1f}s)")
        print()
        print("=" * 50)
        print("【AI編集結果】")
        print(processed)
        print("=" * 50)
    except Exception as e:
        print(f"[エラー] LLM処理失敗: {e}")
        print("LM Studio が起動しているか、モデルがロードされているか確認してください")


if __name__ == "__main__":
    main()
