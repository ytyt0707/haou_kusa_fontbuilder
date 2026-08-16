"""zipをドラッグ&ドロップするだけで、Word/PC用フォント一式とWeb埋め込み用一式を
まとめて作れるツール。1つのFastAPIアプリでUI表示とビルド処理を両方担う。
"""
import io
import zipfile
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import HTMLResponse, Response

import fontbuild

app = FastAPI()

ASSETS_DIR = Path(__file__).parent / "assets"
BASE_FONT_BYTES = (ASSETS_DIR / "base-font.otf").read_bytes()
OFL_TEXT = (ASSETS_DIR / "OFL.txt").read_text(encoding="utf-8")

INDEX_HTML = """<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>kusaフォントビルダー</title>
<style>
  :root { color-scheme: dark; }
  body {
    margin: 0; min-height: 100vh; background: #12140f; color: #eaeee1;
    font-family: -apple-system, "Segoe UI", "Hiragino Sans", "Yu Gothic UI", system-ui, sans-serif;
    display: flex; justify-content: center;
  }
  main { max-width: 640px; width: 100%; padding: 48px 24px 80px; }
  h1 { font-size: 1.5rem; margin: 0 0 8px; }
  p.lede { color: #9aa38c; line-height: 1.7; }
  .field { margin-top: 24px; }
  label { display: block; font-size: 0.85rem; color: #9aa38c; margin-bottom: 6px; }
  input[type=text] {
    width: 100%; box-sizing: border-box; padding: 10px 12px; border-radius: 8px;
    border: 1px solid #33392a; background: #1a1d15; color: #eaeee1; font-size: 1rem;
  }
  #drop {
    margin-top: 12px; border: 2px dashed #33392a; border-radius: 12px; padding: 40px 20px;
    text-align: center; color: #9aa38c; cursor: pointer; transition: border-color 0.15s, background 0.15s;
  }
  #drop.hover { border-color: #8fd45a; background: #1a1d15; }
  #drop strong { color: #eaeee1; }
  #file-name { margin-top: 10px; font-size: 0.85rem; color: #8fd45a; min-height: 1.2em; }
  button {
    margin-top: 20px; width: 100%; padding: 12px 20px; border-radius: 8px; border: none;
    background: #8fd45a; color: #12140f; font-size: 1rem; font-weight: 600; cursor: pointer;
  }
  button:disabled { background: #33392a; color: #9aa38c; cursor: default; }
  #status { margin-top: 16px; font-size: 0.9rem; color: #9aa38c; white-space: pre-wrap; line-height: 1.6; }
  #status.error { color: #e29b9b; }
  #status.ok { color: #8fd45a; }
  input[type=file] { display: none; }
</style>
</head>
<body>
<main>
  <h1>kusaフォントビルダー</h1>
  <p class="lede">U+XXXX.svg形式の手書きSVGを詰めたzipをドロップすると、
  ベースフォント(Noto Sans CJK JP 間引き版)に差し込んで、
  Word/PCにインストールできる.otfと、Webサイトに埋め込める.woff2をまとめて作ります。</p>

  <div class="field">
    <label for="family">フォント名(Wordの一覧などに出る名前)</label>
    <input type="text" id="family" value="My Kusa Font" maxlength="60">
  </div>

  <div class="field">
    <label>SVG zip</label>
    <div id="drop">
      <div><strong>ここにzipをドロップ</strong>、またはクリックして選択</div>
      <div id="file-name"></div>
    </div>
    <input type="file" id="file-input" accept=".zip">
  </div>

  <button id="build-btn" disabled>ビルドする</button>
  <div id="status"></div>
</main>
<script>
const drop = document.getElementById('drop');
const fileInput = document.getElementById('file-input');
const fileName = document.getElementById('file-name');
const buildBtn = document.getElementById('build-btn');
const statusEl = document.getElementById('status');
const familyInput = document.getElementById('family');
let selectedFile = null;

function setFile(f) {
  if (!f) return;
  selectedFile = f;
  fileName.textContent = f.name + ' (' + Math.round(f.size / 1024) + ' KB)';
  buildBtn.disabled = false;
}

drop.addEventListener('click', () => fileInput.click());
fileInput.addEventListener('change', () => setFile(fileInput.files[0]));
drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('hover'); });
drop.addEventListener('dragleave', () => drop.classList.remove('hover'));
drop.addEventListener('drop', (e) => {
  e.preventDefault();
  drop.classList.remove('hover');
  setFile(e.dataTransfer.files[0]);
});

buildBtn.addEventListener('click', async () => {
  if (!selectedFile) return;
  buildBtn.disabled = true;
  statusEl.className = '';
  statusEl.textContent = 'ビルド中...';

  const form = new FormData();
  form.append('svg_zip', selectedFile);
  form.append('family_name', familyInput.value || 'My Kusa Font');

  try {
    const res = await fetch('/api/build', { method: 'POST', body: form });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(text);
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'kusa-font-output.zip';
    document.body.appendChild(a);
    a.click();
    a.remove();
    const report = res.headers.get('X-Build-Report');
    statusEl.className = 'ok';
    statusEl.textContent = 'できました。ダウンロードが始まります。\\n' + (report ? decodeURIComponent(report) : '');
  } catch (err) {
    statusEl.className = 'error';
    statusEl.textContent = 'エラー: ' + err.message;
  } finally {
    buildBtn.disabled = false;
  }
});
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def index():
    return INDEX_HTML


@app.post("/api/build")
async def build(svg_zip: UploadFile = File(...), family_name: str = Form("My Kusa Font")):
    zip_bytes = await svg_zip.read()

    try:
        entries = fontbuild.load_svg_entries_from_zip_bytes(zip_bytes)
    except zipfile.BadZipFile:
        return Response("zipファイルとして読み込めませんでした。", status_code=400)

    if not entries:
        return Response(
            "U+XXXX.svg形式のファイルが1つも見つかりませんでした。ファイル名を確認してください。",
            status_code=400,
        )

    try:
        patched_bytes, replaced, added, failed = fontbuild.patch_font(BASE_FONT_BYTES, entries)
    except fontbuild.FontBuildError as e:
        return Response(str(e), status_code=400)

    safe_family = (family_name or "My Kusa Font").strip()[:60] or "My Kusa Font"
    otf_bytes = fontbuild.rename_font(
        patched_bytes,
        safe_family,
        extra_copyright_note="Modified glyphs added via kusa font builder.",
    )
    woff2_bytes = fontbuild.to_woff2(otf_bytes)

    readme = f"""{safe_family} - 使い方

## Word / PC にインストールする
「{safe_family}.otf」をダブルクリックして「インストール」を押してください。
インストール後、Wordなどのフォント一覧に「{safe_family}」として表示されます。

## Webサイトに埋め込む
「{safe_family}.woff2」を自分のサイトにアップロードして、以下のCSSを追加してください。

@font-face {{
  font-family: "{safe_family}";
  src: url("(アップロード先のURL)/{safe_family}.woff2") format("woff2");
  font-display: swap;
}}

.example {{
  font-family: "{safe_family}", sans-serif;
}}

## 収録内容
- 差し替え/追加した文字数: {len(replaced)}字(既存グリフを上書き) + {len(added)}字(新規追加)
- 失敗した文字数: {len(failed)}字
{chr(10).join(f"  - U+{cp:04X}: {msg}" for cp, msg in failed) if failed else ""}

## ライセンス
ベースにしたNoto Sans CJK JP(Google/Adobe, SIL Open Font License)の上に
手書きグリフを追加したものです。同梱のOFL.txtをご確認ください。
"""

    out_zip = io.BytesIO()
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{safe_family}.otf", otf_bytes)
        zf.writestr(f"{safe_family}.woff2", woff2_bytes)
        zf.writestr("OFL.txt", OFL_TEXT)
        zf.writestr("README.txt", readme)

    report = f"差し替え{len(replaced)}字・新規{len(added)}字・失敗{len(failed)}字"
    return Response(
        out_zip.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": "attachment; filename=kusa-font-output.zip",
            "X-Build-Report": quote(report),
        },
    )
