"""手書きSVG(U+XXXX.svg形式のzip)を、間引き済みベースフォントに差し込んで
テスト用フォントを組み立てるコア処理。

kusaフォントプロジェクト本体のscripts/patch-font.pyで検証済みのロジックをそのまま移植したもの。
- 複数の<path>要素(=別々のストローク)は、Clipperの論理和(Union)で正しく合成する
  (単純にnonzero winding計算へ投げ込むと、ストロークが持つ穴が他のストロークの陰から
  透けて見えたり、周回方向の不一致で重なりが打ち消し合ったりするため)
- CFF(CIDキー化含む)・TrueTypeの両方のベースフォントに対応
"""
import io
import re
import zipfile

import pyclipper
from fontTools.pens.t2CharStringPen import T2CharStringPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont

CLIPPER_SCALE = 1000
CURVE_SEGMENTS = 6

BOX_H = 1000
FULL_WIDTH_BOX_W = 1000
HALF_WIDTH_BOX_W = 640  # src/lib/glyph-box.ts と同じ値

NAME_RE = re.compile(r"^U\+([0-9A-Fa-f]{4,6})\.svg$")
PATH_D_RE = re.compile(r'<path\b[^>]*\bd="([^"]+)"')

ARITY = {"M": 2, "L": 2, "Q": 4, "Z": 0}
TOKEN_RE = re.compile(r"[MLQZ]|-?\d+\.?\d*(?:[eE][-+]?\d+)?|-?\.\d+(?:[eE][-+]?\d+)?")


class FontBuildError(Exception):
    pass


def parse_svg_path_d(d):
    tokens = TOKEN_RE.findall(d)
    commands = []
    i = 0
    current_type = None
    while i < len(tokens):
        tok = tokens[i]
        if tok in ARITY:
            current_type = tok
            i += 1
            if current_type == "Z":
                commands.append(("Z", ()))
                continue
        if current_type is None:
            raise FontBuildError(f"パス先頭にコマンド文字がありません: {d[:50]}...")
        arity = ARITY[current_type]
        args = tuple(float(x) for x in tokens[i : i + arity])
        if len(args) < arity:
            raise FontBuildError(f"{current_type}コマンドの引数が不足しています: {d[:50]}...")
        commands.append((current_type, args))
        i += arity
        if current_type == "M":
            current_type = "L"
    return commands


def parse_subpaths(d):
    subpaths = []
    verts, ctrls = None, None
    for cmd, args in parse_svg_path_d(d):
        if cmd == "M":
            if verts:
                ctrls.append(None)
                subpaths.append((verts, ctrls))
            verts, ctrls = [(args[0], args[1])], []
        elif cmd == "L":
            verts.append((args[0], args[1]))
            ctrls.append(None)
        elif cmd == "Q":
            verts.append((args[2], args[3]))
            ctrls.append((args[0], args[1]))
        elif cmd == "Z":
            pass
    if verts:
        ctrls.append(None)
        subpaths.append((verts, ctrls))
    return subpaths


def flatten_subpath_to_polygon(verts, ctrls):
    poly = []
    n = len(verts)
    for i in range(n):
        p0 = verts[i]
        p1 = verts[(i + 1) % n]
        ctrl = ctrls[i]
        poly.append(p0)
        if ctrl is not None:
            for step in range(1, CURVE_SEGMENTS):
                t = step / CURVE_SEGMENTS
                x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * ctrl[0] + t**2 * p1[0]
                y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * ctrl[1] + t**2 * p1[1]
                poly.append((x, y))
    return poly


def resmooth_polygon(poly):
    n = len(poly)
    verts = [poly[0]]
    ctrls = []
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        ctrls.append((x0, y0))
        verts.append(((x0 + x1) / 2, (y0 + y1) / 2))
    ctrls.append(None)
    return verts, ctrls


def merge_strokes(path_ds):
    subject_polys = []
    for d in path_ds:
        for verts, ctrls in parse_subpaths(d):
            if len(verts) < 3:
                continue
            poly = flatten_subpath_to_polygon(verts, ctrls)
            scaled = [(round(x * CLIPPER_SCALE), round(y * CLIPPER_SCALE)) for x, y in poly]
            subject_polys.append(scaled)

    if not subject_polys:
        return []

    pc = pyclipper.Pyclipper()
    pc.AddPaths(subject_polys, pyclipper.PT_SUBJECT, True)
    result = pc.Execute(pyclipper.CT_UNION, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)

    merged = []
    for poly in result:
        unscaled = [(x / CLIPPER_SCALE, y / CLIPPER_SCALE) for x, y in poly]
        if len(unscaled) < 3:
            continue
        merged.append(resmooth_polygon(unscaled))
    return merged


def is_half_width_codepoint(cp):
    return 0x21 <= cp <= 0x7E


def advance_width_for(cp):
    return HALF_WIDTH_BOX_W if is_half_width_codepoint(cp) else FULL_WIDTH_BOX_W


def load_svg_entries_from_zip_bytes(zip_bytes):
    entries = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for name in zf.namelist():
            base = name.rsplit("/", 1)[-1]
            m = NAME_RE.match(base)
            if not m:
                continue
            entries.append((int(m.group(1), 16), zf.read(name).decode("utf-8")))
    return entries


def build_glyph_ttf(svg_content, scale):
    pen = TTGlyphPen(None)

    def fx(x):
        return x * scale

    def fy(y):
        return (BOX_H - y) * scale

    path_ds = PATH_D_RE.findall(svg_content)
    for verts, ctrls in merge_strokes(path_ds):
        pen.moveTo((fx(verts[0][0]), fy(verts[0][1])))
        n = len(verts)
        for i in range(n):
            end = verts[(i + 1) % n]
            ctrl = ctrls[i]
            if ctrl is None:
                pen.lineTo((fx(end[0]), fy(end[1])))
            else:
                pen.qCurveTo((fx(ctrl[0]), fy(ctrl[1])), (fx(end[0]), fy(end[1])))
        pen.closePath()

    return pen.glyph()


def quad_to_cubic(p0, p1, p2):
    c1 = (p0[0] + 2 / 3 * (p1[0] - p0[0]), p0[1] + 2 / 3 * (p1[1] - p0[1]))
    c2 = (p2[0] + 2 / 3 * (p1[0] - p2[0]), p2[1] + 2 / 3 * (p1[1] - p2[1]))
    return c1, c2


def build_charstring_cff(svg_content, scale, width, private, global_subrs):
    pen = T2CharStringPen(width, None)

    def fx(x):
        return x * scale

    def fy(y):
        return (BOX_H - y) * scale

    path_ds = PATH_D_RE.findall(svg_content)
    for verts, ctrls in merge_strokes(path_ds):
        current = (fx(verts[0][0]), fy(verts[0][1]))
        pen.moveTo(current)
        n = len(verts)
        for i in range(n):
            end = (fx(verts[(i + 1) % n][0]), fy(verts[(i + 1) % n][1]))
            ctrl = ctrls[i]
            if ctrl is None:
                pen.lineTo(end)
            else:
                c = (fx(ctrl[0]), fy(ctrl[1]))
                c1, c2 = quad_to_cubic(current, c, end)
                pen.curveTo(c1, c2, end)
            current = end
        pen.closePath()

    return pen.getCharString(private=private, globalSubrs=global_subrs)


def patch_font(base_font_bytes, svg_entries):
    """base_font_bytes(既存フォントのバイト列)に、svg_entries([(codepoint, svg_text), ...])の
    グリフを差し替え/追加したフォントのバイト列を返す。
    """
    font = TTFont(io.BytesIO(base_font_bytes))
    is_glyf = "glyf" in font
    is_cff = "CFF " in font
    if not is_glyf and not is_cff:
        raise FontBuildError("ベースフォントがTrueType(glyf)でもCFFでもありません。")

    units_per_em = font["head"].unitsPerEm
    scale = units_per_em / BOX_H

    hmtx_table = font["hmtx"]
    cmap = font.getBestCmap()
    glyph_order = font.getGlyphOrder()

    replaced = []
    added = []
    failed = []  # [(codepoint, エラーメッセージ), ...] 1字の失敗で全体を止めないため

    if is_glyf:
        glyf_table = font["glyf"]
        for cp, svg_content in sorted(svg_entries):
            try:
                advance_width = round(advance_width_for(cp) * scale)
                glyph = build_glyph_ttf(svg_content, scale)
            except Exception as e:
                failed.append((cp, str(e)))
                continue
            existing_name = cmap.get(cp)
            if existing_name:
                glyf_table[existing_name] = glyph
                hmtx_table[existing_name] = (advance_width, glyph.xMin if hasattr(glyph, "xMin") else 0)
                replaced.append(cp)
            else:
                glyph_name = f"uni{cp:04X}" if cp <= 0xFFFF else f"u{cp:X}"
                glyph_order.append(glyph_name)
                font.setGlyphOrder(glyph_order)
                glyf_table.glyphs[glyph_name] = glyph
                hmtx_table[glyph_name] = (advance_width, 0)
                if "vmtx" in font:
                    font["vmtx"].metrics[glyph_name] = (units_per_em, 0)
                for table in font["cmap"].tables:
                    if table.isUnicode():
                        table.cmap[cp] = glyph_name
                added.append(cp)
        glyf_table.compile(font)

    else:  # CFF
        cff = font["CFF "].cff
        td = cff.topDictIndex[0]
        is_cid = hasattr(td, "ROS")
        charstrings = td.CharStrings
        global_subrs = cff.GlobalSubrs
        charset = td.charset

        default_fd_index = None
        if is_cid:
            for i, fd in enumerate(td.FDArray):
                if getattr(fd, "FontName", "").endswith("-Generic"):
                    default_fd_index = i
                    break
            if default_fd_index is None:
                default_fd_index = 0

        used_cids = {int(name[3:]) for name in charset if name.startswith("cid")}

        def next_free_cid(start=[1]):
            cid = start[0]
            while cid in used_cids:
                cid += 1
            used_cids.add(cid)
            start[0] = cid + 1
            return cid

        for cp, svg_content in sorted(svg_entries):
            advance_width = round(advance_width_for(cp) * scale)
            existing_name = cmap.get(cp)

            if existing_name:
                try:
                    gid = font.getGlyphID(existing_name)
                    private = td.FDArray[td.FDSelect[gid]].Private if is_cid else td.Private
                    charstring = build_charstring_cff(svg_content, scale, advance_width, private, global_subrs)
                except Exception as e:
                    failed.append((cp, str(e)))
                    continue
                charstrings[existing_name] = charstring
                hmtx_table[existing_name] = (advance_width, 0)
                replaced.append(cp)
            else:
                if is_cid:
                    glyph_name = f"cid{next_free_cid():05d}"
                    private = td.FDArray[default_fd_index].Private
                else:
                    glyph_name = f"uni{cp:04X}" if cp <= 0xFFFF else f"u{cp:X}"
                    private = td.Private

                try:
                    charstring = build_charstring_cff(svg_content, scale, advance_width, private, global_subrs)
                except Exception as e:
                    failed.append((cp, str(e)))
                    continue

                new_index = len(charstrings.charStringsIndex)
                charstrings.charStringsIndex.append(charstring)
                charstrings.charStrings[glyph_name] = new_index
                charset.append(glyph_name)
                if is_cid:
                    td.FDSelect.append(default_fd_index)
                font.setGlyphOrder(charset)

                hmtx_table[glyph_name] = (advance_width, 0)
                if "vmtx" in font:
                    font["vmtx"].metrics[glyph_name] = (units_per_em, 0)
                for table in font["cmap"].tables:
                    if table.isUnicode():
                        table.cmap[cp] = glyph_name
                added.append(cp)

    out = io.BytesIO()
    font.save(out)
    return out.getvalue(), replaced, added, failed


def rename_font(font_bytes, family_name, extra_copyright_note=None):
    """フォント内部の名前(Word等の一覧に出る名前)を差し替える。"""
    font = TTFont(io.BytesIO(font_bytes))
    name = font["name"]
    for rec in name.names:
        if rec.nameID == 1:
            rec.string = family_name
        elif rec.nameID == 2:
            rec.string = "Regular"
        elif rec.nameID == 4:
            rec.string = f"{family_name} Regular"
        elif rec.nameID == 6:
            rec.string = re.sub(r"[^A-Za-z0-9-]", "", family_name.replace(" ", "")) + "-Regular"
        elif rec.nameID in (16,):
            rec.string = family_name
        elif rec.nameID in (17,):
            rec.string = "Regular"
        elif rec.nameID == 0 and extra_copyright_note:
            rec.string = f"{rec.toUnicode()} {extra_copyright_note}"
    out = io.BytesIO()
    font.save(out)
    return out.getvalue()


def to_woff2(font_bytes):
    font = TTFont(io.BytesIO(font_bytes))
    font.flavor = "woff2"
    out = io.BytesIO()
    font.save(out)
    return out.getvalue()
