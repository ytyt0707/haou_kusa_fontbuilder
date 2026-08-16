# kusaフォントビルダー

kusaフォントプロジェクトの手書きSVG(zip)から、Word/PC用フォント(.otf)とWeb埋め込み用フォント(.woff2)を
自動生成するツール。単体のFastAPIアプリ(UI表示+ビルド処理を1つで完結)。

## ローカルで試す

```
pip install -r requirements.txt
python -m uvicorn app:app --reload
```

`http://localhost:8000` を開く。

## 構成

- `app.py` — FastAPIアプリ本体(画面のHTML配信 + `/api/build`でzipを受け取りフォントを組み立てる)
- `fontbuild.py` — フォント組み立てのコアロジック(kusaフォントプロジェクト本体の
  `scripts/patch-font.py`から移植。複数ストロークの合成にClipperの論理和を使う手法など)
- `assets/base-font.otf` — 間引き済みNoto Sans CJK JP(ベースフォント)
- `assets/OFL.txt` — ベースフォントのライセンス本文(生成物にも同梱される)

## Vercelへのデプロイ

1. このディレクトリをGitHubの新しいリポジトリにpush
2. Vercelダッシュボードで「New Project」→ そのリポジトリを選択してImport
   (フレームワーク設定は自動検出されるはず。特別な設定は不要)
3. デプロイ完了後に発行されるURL(例: `https://xxxxx.vercel.app`)でそのまま使える

ベースフォントを差し替えたい場合は `assets/base-font.otf` を置き換えるだけでよい
(CFF・TrueTypeどちらの形式にも対応している)。
