#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
encord 録画ファイル リネームアプリ  v1.4

操作:
  ・[...] ボタン → フォルダを選択
  ・スキャン → ファイルをスキャンしてプレビュー表示
  ・Shift+クリック / Ctrl+クリック → 複数行を選択
  ・カテゴリ列をクリック → 選択行のカテゴリを一括変更（1行でも可）
  ・変換後列をダブルクリック → 手動編集
  ・☑ 列をクリック → 個別にリネーム対象から除外
  ・設定 → 番組表記号・デフォルト値・! プレフィックス等を変更
  ・実行 → ☑ のファイルを一括リネーム
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import os
import re
import json
import struct
from datetime import date, timedelta

# ────────────────────────────────────────────────────────────
#  設定デフォルト値
# ────────────────────────────────────────────────────────────

DEFAULT_DIR   = r"Z:\\"
DEFAULT_CODEC = "[1024x576 H264aac]"

SUPPORTED_EXTS = {'.mp4', '.avi', '.mkv'}   # リネーム対象の拡張子

MOVIE_TAG_RE = re.compile(r'\s*[\[［]\s*映\s*[\]］]\s*')  # [映] タグ検出
LIVE_TAG_RE  = re.compile(r'\bLIVE\b|ライブ|コンサート|CONCERT', re.IGNORECASE)  # LIVEキーワード検出
# 曜ドラマ・曜劇場等の枠名検出（【ドラマ】カテゴリの自動判定用）
DRAMA_FRAME_RE = re.compile(
    r'^(?:[月火水木金土日]曜(?:ドラマ|劇場)|プレミアムドラマ)[「『]')

CATEGORIES = ['【アニメ】', '【ドラマ】', '【TV】', '【映画】', '[LIVE]', '[TV]', '[MV]']

DEFAULT_CAT_EPISODE = '【アニメ】'
DEFAULT_CAT_NORMAL  = '【TV】'

# 番組表記号デフォルト除去リスト（[吹] [字幕] は保持するためデフォルト外）
DEFAULT_STRIP_SYMBOLS = ['字', '解', '新', '二', '多', 'SS', 'S', 'HD',
                         'デ', '再', '無', '初', '生', '5.1', '音声']

CONFIG_PATH = os.path.join(os.path.expanduser('~'), '.encord_rename_config.json')


def load_config() -> dict:
    try:
        # utf-8-sig: BOM付きUTF-8（Windowsメモ帳保存）にも対応
        with open(CONFIG_PATH, encoding='utf-8-sig') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, ValueError):
        # JSONが壊れている場合はバックアップを残して空を返す
        backup = CONFIG_PATH + '.bak'
        try:
            import shutil
            shutil.copy2(CONFIG_PATH, backup)
        except Exception:
            pass
        return {'_parse_error': True}   # 壊れている印


def save_config(data: dict):
    try:
        cfg = load_config()
        if cfg.get('_parse_error'):
            return   # JSONが壊れているときは上書きしない
        cfg.update(data)
        with open(CONFIG_PATH, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def load_folder_history() -> list:
    """フォルダ履歴を読み込む（最大10件、先頭が最新）"""
    cfg = load_config()
    return cfg.get('folder_history', [])


def record_folder(folder: str):
    """フォルダを履歴に記録（最大10件、重複排除して先頭に追加）"""
    folder = folder.strip()
    if not folder:
        return
    cfg     = load_config()
    history = cfg.get('folder_history', [])
    if folder in history:
        history.remove(folder)
    history.insert(0, folder)
    save_config({'last_folder': folder, 'folder_history': history[:10]})


def load_prog_history() -> dict:
    """番組名履歴を読み込む。1年以上未使用のものは自動削除。
    返値: {番組名: 最終使用日(ISO文字列)} を最終使用日の新しい順にソートしたdict
    """
    cfg     = load_config()
    raw     = cfg.get('prog_name_history', {})
    cutoff  = (date.today() - timedelta(days=365)).isoformat()
    pruned  = {name: d for name, d in raw.items() if d >= cutoff}
    if len(pruned) != len(raw):
        save_config({'prog_name_history': pruned})
    return dict(sorted(pruned.items(), key=lambda x: x[1], reverse=True))


def record_prog_name(name: str):
    """番組名を履歴に記録（最終使用日を今日に更新）"""
    if not name.strip():
        return
    cfg     = load_config()
    history = cfg.get('prog_name_history', {})
    history[name.strip()] = date.today().isoformat()
    save_config({'prog_name_history': history})


def load_title_prefix_map() -> dict:
    """番組名→タイトルプレフィックスの辞書を返す。例: {"豊臣兄弟!": "大河"}"""
    raw = load_config().get('title_prefix_map', {})
    # キーを prog_name に正規化
    normalized: dict = {}
    for key, val in raw.items():
        prog = auto_split_title(auto_split_title(key)[0])[0]
        normalized[prog] = val
    return normalized


def load_prog_cat_map() -> dict:
    """番組名→カテゴリ の記憶辞書を返す。例: {"EIGHT-JAM": "[TV]"}
    キーが誤ってフルタイトルで保存されている場合も auto_split_title で正規化して返す。
    """
    raw = load_config().get('prog_cat_map', {})
    normalized: dict = {}
    for key, val in raw.items():
        prog = auto_split_title(auto_split_title(key)[0])[0]
        normalized[prog] = val
    return normalized


def save_prog_cat(prog_name: str, category: str):
    """番組名とカテゴリの対応を記憶する（次回スキャン時に自動適用）"""
    if not prog_name.strip():
        return
    cfg = load_config()
    mapping = cfg.get('prog_cat_map', {})
    mapping[prog_name.strip()] = category
    save_config({'prog_cat_map': mapping})


# ────────────────────────────────────────────────────────────
#  変換ロジック
# ────────────────────────────────────────────────────────────

def build_bs_regex(symbols: list) -> re.Pattern:
    """番組表記号除去用の正規表現を記号リストから生成"""
    if not symbols:
        return re.compile(r'(?!)')   # マッチしない正規表現
    parts = sorted(symbols, key=len, reverse=True)   # 長い順（SS > S など）
    alts  = '|'.join(re.escape(s) for s in parts)
    return re.compile(r'[\[［]\s*(?:' + alts + r')\s*[\]］]')


def fw_to_hw(text: str) -> str:
    """全角英数字・全角スペース → 半角（Windows禁止文字・全角ハイフンは保持）"""
    WIN_FORBIDDEN_FW = {0xFF02, 0xFF0A, 0xFF0F, 0xFF1A,
                        0xFF1C, 0xFF1E, 0xFF1F, 0xFF3C, 0xFF5C}
    out = []
    for ch in text:
        cp = ord(ch)
        if ch == '－':
            out.append(ch)
        elif ch == '　':
            out.append(' ')
        elif cp in WIN_FORBIDDEN_FW:
            out.append(ch)
        elif 0xFF01 <= cp <= 0xFF5E:
            out.append(chr(cp - 0xFEE0))
        else:
            out.append(ch)
    return ''.join(out)


def normalize_spaces(text: str) -> str:
    text = text.replace('　', ' ')                       # 全角スペース→半角スペース
    # 枠名『番組名』形式を番組名だけに短縮: 日曜劇場『GIFT』→ GIFT
    # ※【...】内のゲスト紹介等に入れ子の『』がある場合はこの短縮を行わない
    text = re.sub(r'^[^『【\s]+\s*『([^』]+)』', r'\1', text)
    # 枠名「番組名」形式（単一かぎ括弧）を番組名だけに短縮: 火曜ドラマ「君の好きは無敵」→ 君の好きは無敵
    # ※話数サブタイトルにも「」が使われるため、曜ドラマ・曜劇場等の既知の枠名のみ対象にする
    text = re.sub(r'^(?:[月火水木金土日]曜(?:ドラマ|劇場)|プレミアムドラマ)「([^」]+)」', r'\1', text)
    # 残った『』はタイトル内の入れ子表記なので括弧だけ除去（内容は残す）
    # 例: 【舘ひろし×林◆『パパとムスメの7日間』新垣結衣との演技を再現!】
    #   → 【舘ひろし×林◆パパとムスメの7日間新垣結衣との演技を再現!】
    text = re.sub(r'『([^』]+)』', r'\1', text)
    text = re.sub(r'([^\s])【', r'\1 【', text)          # 【 の前にスペースを確保
    text = re.sub(r'([^\s])★', r'\1 ★', text)          # ★ の前にスペースを確保
    text = re.sub(r'(話)([「])', r'\1 \2', text)        # 第XX話「→ 第XX話 「
    # カタカナ・ASCII直後の「選」（総集編・セレクトの意）の前にスペースを確保
    # 例: ワイルドライフ選 → ワイルドライフ 選 / SONGS選 → SONGS 選
    # ※当選・予選など漢字直後は対象外
    text = re.sub(r'([ァ-ヶーA-Za-z])(選)(?=[ ]|$)', r'\1 \2', text)
    return re.sub(r' +', ' ', text).strip()


# ────────────────────────────────────────────────────────────
#  MP4 再生時間取得（純正 Python）
# ────────────────────────────────────────────────────────────

def _open_file_shared(filepath: str):
    """ファイルを読み取り専用で開く。
    PermissionError 時は Windows 共有モード（録画中ファイル対応）でリトライ。
    開けなかった場合は None を返す。
    """
    try:
        return open(filepath, 'rb')
    except PermissionError:
        pass
    # Windows 共有読み取りモードでリトライ（ctypes + msvcrt）
    try:
        import ctypes, msvcrt
        GENERIC_READ      = 0x80000000
        FILE_SHARE_ALL    = 0x00000007   # READ | WRITE | DELETE
        OPEN_EXISTING     = 3
        FILE_ATTRIBUTE_NORMAL = 0x80
        handle = ctypes.windll.kernel32.CreateFileW(
            filepath, GENERIC_READ, FILE_SHARE_ALL,
            None, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None)
        if handle in (0, -1, 0xFFFFFFFF, ctypes.c_void_p(-1).value):
            return None
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        return os.fdopen(fd, 'rb')
    except Exception:
        return None


def read_mp4_duration(filepath: str):
    """MP4 ファイルの再生時間を秒（int）で返す（純正 Python のみ）。取得失敗時は None。

    ファイルをシークしながらボックスを辿る方式のため、先頭に数 GB の mdat がある
    非 faststart 録画ファイル（moov が末尾）でも正しく動作する。
    """

    def _find_mvhd(f, start: int, length: int):
        """ファイル f の [start, start+length) 範囲を再帰的に走査し
        mvhd ペイロード（ヘッダ 8 バイト除き）を返す。見つからなければ None。
        """
        CONTAINERS = {b'moov', b'trak', b'mdia', b'minf',
                      b'stbl', b'udta', b'edts', b'dinf'}
        pos = start
        end = start + length
        while pos + 8 <= end:
            f.seek(pos)
            hdr = f.read(8)
            if len(hdr) < 8:
                break
            sz = struct.unpack('>I', hdr[:4])[0]
            bt = hdr[4:8]
            hdr_size = 8
            if sz == 1:             # 64 bit サイズ拡張
                ext = f.read(8)
                if len(ext) < 8:
                    break
                sz = struct.unpack('>Q', ext)[0]
                hdr_size = 16
            elif sz == 0:           # ボックス末尾 = ファイル末尾
                sz = end - pos
            if sz < hdr_size:
                break

            if bt == b'mvhd':
                f.seek(pos + hdr_size)
                return f.read(min(sz - hdr_size, 40))   # mvhd は高々 40 バイトで十分

            if bt in CONTAINERS:
                result = _find_mvhd(f, pos + hdr_size, sz - hdr_size)
                if result is not None:
                    return result

            pos += sz   # 次のボックスへシーク（mdat 等は読まずにスキップ）
        return None

    try:
        file_size = os.path.getsize(filepath)
        f = _open_file_shared(filepath)
        if f is None:
            return None
        with f:
            mvhd = _find_mvhd(f, 0, file_size)

        if not mvhd or len(mvhd) < 20:
            return None

        # mvhd ペイロードのオフセット（ボックスヘッダ 8 バイトを除く）:
        #   [0]     version
        #   [1:4]   flags
        #   v0: creation(4) mod(4) timescale(4)@12 duration(4)@16
        #   v1: creation(8) mod(8) timescale(4)@20 duration(8)@24
        version = mvhd[0]
        if version == 0:
            timescale = struct.unpack_from('>I', mvhd, 12)[0]
            duration  = struct.unpack_from('>I', mvhd, 16)[0]
        else:
            if len(mvhd) < 32:
                return None
            timescale = struct.unpack_from('>I', mvhd, 20)[0]
            duration  = struct.unpack_from('>Q', mvhd, 24)[0]

        return int(duration / timescale) if timescale else None
    except Exception:
        return None


def read_avi_duration(filepath: str):
    """AVI (RIFF) ファイルの再生時間を秒（int）で返す。取得失敗時は None。
    LIST hdrl 内の avih チャンクから dwMicroSecPerFrame × dwTotalFrames で算出。
    """
    try:
        f = _open_file_shared(filepath)
        if f is None:
            return None
        with f:
            # RIFF / AVI  シグネチャ確認
            sig = f.read(12)
            if len(sig) < 12 or sig[:4] != b'RIFF' or sig[8:12] != b'AVI ':
                return None

            f.seek(0, 2)
            file_size = f.tell()
            pos = 12   # RIFF ヘッダ (12 bytes) の直後から走査

            while pos + 8 <= file_size:
                f.seek(pos)
                hdr = f.read(8)
                if len(hdr) < 8:
                    break
                chunk_id   = hdr[:4]
                chunk_size = struct.unpack_from('<I', hdr, 4)[0]   # little-endian

                if chunk_id == b'LIST':
                    list_type = f.read(4)
                    if list_type == b'hdrl':
                        # hdrl 内で avih チャンクを探す
                        sub_pos = pos + 12      # LIST hdr(8) + list type(4)
                        sub_end = pos + 8 + chunk_size
                        while sub_pos + 8 <= sub_end:
                            f.seek(sub_pos)
                            sub_hdr = f.read(8)
                            if len(sub_hdr) < 8:
                                break
                            sub_id   = sub_hdr[:4]
                            sub_size = struct.unpack_from('<I', sub_hdr, 4)[0]
                            if sub_id == b'avih' and sub_size >= 20:
                                avih = f.read(20)
                                if len(avih) < 20:
                                    return None
                                # MainAVIHeader
                                us_per_frame = struct.unpack_from('<I', avih,  0)[0]
                                total_frames = struct.unpack_from('<I', avih, 16)[0]
                                if us_per_frame == 0:
                                    return None
                                return int(us_per_frame * total_frames / 1_000_000)
                            sub_pos += 8 + sub_size + (sub_size % 2)  # RIFF パディング
                        break   # hdrl 処理済み

                pos += 8 + chunk_size + (chunk_size % 2)
        return None
    except Exception:
        return None


def format_duration(seconds: int, fmt: str = '[{h}h]?{m}m{ss}s') -> str:
    """秒を時間文字列にフォーマット。
    プレースホルダ:
      {h}/{m}/{s}   … ゼロ埋めなし
      {hh}/{mm}/{ss} … 2桁固定
    条件ブロック:
      [{h}h]?  … 時間が 0 のとき [] 内ごと省略、1以上なら展開
    例: '[{h}h]?{m}m{ss}s' → '45m09s' (h=0) / '1h45m09s' (h>0)
        '[{h}:]?{mm}:{ss}' → '45:09'  (h=0) / '1:45:09'  (h>0)
        '{hh}:{mm}:{ss}'   → '00:45:09' / '01:45:09'  (常に3桁表示)
    """
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    result = (fmt
              .replace('{hh}', f'{h:02d}')
              .replace('{mm}', f'{m:02d}')
              .replace('{ss}', f'{s:02d}')
              .replace('{h}', str(h))
              .replace('{m}', str(m))
              .replace('{s}', str(s)))
    # [...]? ブロック: h > 0 なら中身を残す、h == 0 なら空文字に置換
    result = re.sub(r'\[([^\]]*)\]\?',
                    lambda mo: mo.group(1) if h > 0 else '', result)
    return result


def normalize_episode(text: str) -> str:
    text = re.sub(r'\s*[＃#]\s*(\d+)話?', lambda m: f' 第{int(m.group(1)):02d}話', text)
    text = re.sub(r'\s*Episode\s*(\d+)',
                  lambda m: f' 第{int(m.group(1)):02d}話', text, flags=re.IGNORECASE)

    def fix_ep(m):
        n_str = m.group(1)
        n_str = ''.join(chr(ord(c) - 0xFEE0) if '０' <= c <= '９' else c
                        for c in n_str)
        return f'第{int(n_str):02d}話'

    text = re.sub(r'第([０-９0-9]+)話', fix_ep, text)
    # （N） / (N) 形式 → 第NN話（例: 豊臣兄弟!(18)羽柴兄弟! → 豊臣兄弟! 第18話 羽柴兄弟!）
    text = re.sub(r'\s*[（(](\d{1,3})[）)]\s*',
                  lambda m: f' 第{int(m.group(1)):02d}話 ', text)
    return text


def has_episode(text: str) -> bool:
    return bool(re.search(
        r'[＃#]\s*\d+|Episode\s*\d+|第[０-９0-9]+話|[（(]\d{1,3}[）)]',
        text, re.IGNORECASE))


def normalize_cours_season(text: str) -> str:
    """『番組名』第Nクール → 番組名 SN（シーズン表記に変換）
    例: 『Dr.STONE SCIENCE FUTURE』第3クール → Dr.STONE SCIENCE FUTURE S3
    """
    return re.sub(r'『([^』]+)』\s*第(\d+)クール', r'\1 S\2', text)


def process_title(title: str, bs_regex: re.Pattern) -> str:
    title = fw_to_hw(title)
    title = normalize_cours_season(title)
    title = bs_regex.sub('', title).strip()
    title = normalize_episode(title)
    title = normalize_spaces(title)
    return title


def analyze_file(filename: str, bs_regex: re.Pattern,
                 cat_normal: str = DEFAULT_CAT_NORMAL,
                 cat_episode: str = DEFAULT_CAT_EPISODE) -> dict:
    ext = os.path.splitext(filename)[1].lower()
    if ext not in SUPPORTED_EXTS:
        return {'skip': True, 'reason': '対象外'}
    if filename.startswith('!'):
        return {'skip': True, 'reason': 'リネーム済み'}

    base = filename[: -len(ext)]   # 拡張子を除いたベース名
    base = re.sub(r'(_dec|_fixed)+$', '', base)  # 修正ツールサフィックスを除去

    def _finalize(title, ep, date, pattern, is_drama_frame=False):
        """[映] タグ / LIVEキーワード / 曜ドラマ枠 検出 → カテゴリ自動判定。"""
        if MOVIE_TAG_RE.search(title):
            title = normalize_spaces(MOVIE_TAG_RE.sub(' ', title))
            return {'skip': False, 'pattern': pattern, 'ext': ext,
                    'has_ep': False, 'title': title, 'date': date,
                    'category': '【映画】'}
        if LIVE_TAG_RE.search(title):
            return {'skip': False, 'pattern': pattern, 'ext': ext,
                    'has_ep': ep, 'title': title, 'date': date,
                    'category': '[LIVE]'}
        if is_drama_frame:
            return {'skip': False, 'pattern': pattern, 'ext': ext,
                    'has_ep': ep, 'title': title, 'date': date,
                    'category': '【ドラマ】'}
        return {'skip': False, 'pattern': pattern, 'ext': ext,
                'has_ep': ep, 'title': title, 'date': date,
                'category': cat_episode if ep else cat_normal}

    m1 = re.match(r'^(\d{8})\d+-(.+)$', base)
    if m1:
        ds, raw = m1.group(1), m1.group(2)
        date  = f"({ds[:4]}.{ds[4:6]}.{ds[6:8]})"
        is_drama_frame = bool(DRAMA_FRAME_RE.match(raw))
        title = process_title(raw, bs_regex)
        ep    = has_episode(title)
        return _finalize(title, ep, date, 1, is_drama_frame)

    m2 = re.match(r'^(\(\d{4}\.\d{2}\.\d{2}\))\s*(.+)$', base)
    if m2:
        date, raw = m2.group(1), m2.group(2).strip()
        is_drama_frame = bool(DRAMA_FRAME_RE.match(raw))
        title = process_title(raw, bs_regex)
        title = re.sub(r'\s*\[1024x576 H264aac\]\s*$', '', title).strip()
        ep    = has_episode(title)
        return _finalize(title, ep, date, 2, is_drama_frame)

    return {'skip': True, 'reason': 'パターン不一致'}


def build_new_name(info: dict, category: str = None, codec: str = None,
                   use_prefix: bool = True, prefix_str: str = "!",
                   duration_str: str = '', title_prefix: str = '') -> str:
    title  = info['title']
    if title_prefix and not title.startswith(title_prefix):
        title = f"{title_prefix} {title}"
    cat    = category or info.get('category', DEFAULT_CAT_NORMAL)
    cod    = codec or DEFAULT_CODEC
    # 時間文字列をコーデック括弧の先頭に挿入: [1024x576 H264aac] → [1h45m30s 1024x576 H264aac]
    if duration_str:
        if cod.startswith('[') and cod.endswith(']'):
            cod = f'[{duration_str} {cod[1:]}'
        else:
            cod = f'[{duration_str}] {cod}'
    prefix = f"{prefix_str} " if (use_prefix and prefix_str) else ""
    ext    = info.get('ext', '.mp4')
    # ドラマ・アニメは 第XX話 の後ろに「」がなければ自動追加
    if cat in ('【ドラマ】', '【アニメ】'):
        title = re.sub(r'(第\d+話)\s+([^「].*)', r'\1 「\2」', title)
    if info.get('has_ep'):
        return f"{prefix}{cat} {title} {cod}{ext}"
    else:
        return f"{prefix}{cat} {info['date']} {title} {cod}{ext}"


# ────────────────────────────────────────────────────────────
#  アーカイブ整理ロジック
# ────────────────────────────────────────────────────────────

def auto_split_title(title: str) -> tuple:
    """タイトルを番組名とエピソードタイトルに自動分割。
    区切り文字の優先順位: ▽ ▼ > ★ > 【 > 「 > 　(全角スペース) > 半角スペース
    ▽ ▼ と空白は区切り文字として除去、★ 【 「 は ep_title 側に残す。
    半角スペース分割では THE など単体では番組名にならない語頭はスキップ。
    """
    # 半角スペース分割時に番組名として認めない語（大文字で比較）
    SPACE_EXCLUDE = {'THE', 'A', 'AN'}

    patterns = [
        ('▽', False),
        ('▼', False),
        ('◆', False),
        ('◇', False),
        ('★', True),
        ('【', True),
        ('「', True),
        ('　', False),
    ]
    for delim, keep in patterns:
        idx = title.find(delim)
        if idx > 0:
            prog = title[:idx].strip()
            ep   = (title[idx:] if keep else title[idx + len(delim):]).strip()
            if prog:
                return prog, ep

    # 半角スペース: 除外語はスキップして次のスペースを探す
    pos = 0
    while True:
        idx = title.find(' ', pos)
        if idx <= 0:
            break
        prog = title[:idx].strip()
        ep   = title[idx + 1:].strip()
        if prog.upper() not in SPACE_EXCLUDE and prog:
            return prog, ep
        pos = idx + 1

    return title, ''


def parse_renamed_file(filename: str) -> dict:
    """リネーム済みファイルを解析して番組名・タイトル・日付等を返す。
    対象:
      <prefix> 【カテゴリ】 (YYYY.MM.DD) タイトル [codec].<ext>  ← ! あり
      【カテゴリ】 番組名 (YYYY.MM.DD) タイトル [codec].<ext>    ← ! なし（アーカイブ）
    """
    ext = os.path.splitext(filename)[1].lower()
    if ext not in SUPPORTED_EXTS:
        return None
    base = filename[: -len(ext)]

    # カテゴリが先頭にある場合（プレフィックスなし）
    m_cat_top = re.match(r'^(【[^】]+】|\[[^\]]+\])\s*', base)
    if m_cat_top:
        prefix   = ''
        category = m_cat_top.group(1)
        rest     = base[m_cat_top.end():]
    else:
        # プレフィックス（先頭の非スペース文字列）を除去・保存
        m = re.match(r'^(\S+)\s+', base)
        if not m:
            return None
        prefix = m.group(1)   # 例: "!" や "!!" など
        rest = base[m.end():]

        # カテゴリ: 【...】 または [...]
        m = re.match(r'^(【[^】]+】|\[[^\]]+\])\s*', rest)
        if not m:
            return None
        category = m.group(1)
        rest = rest[m.end():]

    # コーデック: 末尾の [...]
    m_cod = re.search(r'\s*(\[[^\]]+\])\s*$', rest)
    codec = m_cod.group(1) if m_cod else ''
    if m_cod:
        rest = rest[:m_cod.start()].strip()

    # 日付: (YYYY.MM.DD)
    m_date = re.match(r'^(\(\d{4}\.\d{2}\.\d{2}\))\s*', rest)
    date  = m_date.group(1) if m_date else ''
    title = rest[m_date.end():].strip() if m_date else rest.strip()

    prog, ep = auto_split_title(title)
    return {'category': category, 'date': date, 'codec': codec,
            'ext': ext, 'prefix': prefix, 'prog_name': prog, 'ep_title': ep}


def build_archive_name(info: dict) -> str:
    """【カテゴリ】 番組名 (日付) タイトル [codec].<ext> を生成（プレフィックスは常に除去）"""
    parts = [info['category']]
    if info['prog_name'].strip():
        parts.append(info['prog_name'].strip())
    if info['date']:
        parts.append(info['date'])
    if info['ep_title'].strip():
        parts.append(info['ep_title'].strip())
    if info['codec']:
        parts.append(info['codec'])
    return ' '.join(parts) + info.get('ext', '.mp4')


# ────────────────────────────────────────────────────────────
#  フォルダ分けダイアログ
# ────────────────────────────────────────────────────────────

def _sanitize_folder_name(name: str) -> str:
    """Windows フォルダ名として使えない文字を全角に変換"""
    table = str.maketrans(r'\/:*?"<>|', '＼／：＊？"＜＞｜')
    return name.translate(table).strip()


# 接尾辞として除去するキーワード（シーズン・形態）
_SUFFIX_WORDS = r'特別編|続章|外伝|スピンオフ|OVA|SP|リベンジ|完結編|総集編'

def _normalize_folder_name(prog: str) -> str:
    """フォルダ分け用に番組名を正規化する。
    ・ハイフン類を統一（キノの旅 -...‐... → 同一フォルダ）
    ・日付・話数・シーズン番号・接尾辞を除去
    """
    # ハイフン類（‐‑‒–—―－FullwidthHyphen等）を半角ハイフンに統一
    prog = re.sub(r'[‐‑‒–—―－]', '-', prog)
    # アーカイブ形式の日付以降を除去
    prog = re.sub(r'\s*\(\d{4}\.\d{2}\.\d{2}\).*$', '', prog).strip()
    # 第NN話 / ＃NN / #NN を除去
    prog = re.sub(r'\s*第\d+話.*$', '', prog).strip()
    prog = re.sub(r'\s*[＃#]\d+.*$', '', prog).strip()
    # S\d+（シーズン番号）とそれ以降を除去（S1, S2 リベンジ, S2 続章 等）
    prog = re.sub(r'\s+S\d+(\s.*)?$', '', prog, flags=re.IGNORECASE).strip()
    # 接尾辞キーワードとそれ以降を除去（特別編, 続章, 外伝, リベンジ 等）
    prog = re.sub(r'\s+(?:' + _SUFFIX_WORDS + r')(\s.*)?$', '', prog).strip()
    return prog


class FolderSortDialog(tk.Toplevel):

    def __init__(self, app):
        super().__init__(app)
        self.app    = app
        self.rows   = []   # {'folder': str, 'files': [str], 'iid': str}
        self.title("フォルダ分け")
        self.geometry("1100x640")
        self.minsize(700, 400)
        self.transient(app)
        self._apply_style()
        self._build()
        self.after(150, self._scan)

    def _apply_style(self):
        s = ttk.Style(self)
        s.configure('Treeview',
                    font=('Meiryo UI', 9), rowheight=24,
                    background='#FFFFFF', fieldbackground='#FFFFFF')
        s.configure('Treeview.Heading',
                    font=('Meiryo UI', 9, 'bold'),
                    background='#1F6B38', foreground='white', relief='flat')

    def _build(self):
        hdr = tk.Frame(self, bg='#1F6B38', height=46)
        hdr.pack(fill='x')
        hdr.pack_propagate(False)
        tk.Label(hdr, text="フォルダ分け",
                 font=('Meiryo UI', 13, 'bold'), fg='white', bg='#1F6B38'
                 ).pack(side='left', padx=18, pady=10)
        tk.Label(hdr, text="番組名ごとにサブフォルダを作成してファイルを移動します",
                 font=('Meiryo UI', 9), fg='#A8D5B0', bg='#1F6B38'
                 ).pack(side='left', pady=14)

        tb = tk.Frame(self, bg='#E8ECF0', pady=6)
        tb.pack(fill='x')
        tk.Label(tb, text="  フォルダ:", bg='#E8ECF0',
                 font=('Meiryo UI', 9)).pack(side='left')
        self.dir_var = tk.StringVar(value=self.app.dir_var.get())
        self.dir_cb = ttk.Combobox(tb, textvariable=self.dir_var,
                                   values=load_folder_history(),
                                   width=40, font=('Meiryo UI', 9))
        self.dir_cb.pack(side='left', padx=2)
        tk.Button(tb, text="...", command=self._pick_folder,
                  bg='#D0D4DA', font=('Meiryo UI', 9),
                  relief='flat', padx=6, cursor='hand2').pack(side='left', padx=2)
        tk.Button(tb, text=" スキャン ", command=self._scan,
                  bg='#2E75B6', fg='white', font=('Meiryo UI', 9),
                  relief='flat', cursor='hand2').pack(side='left', padx=6)

        tf = tk.Frame(self)
        tf.pack(fill='both', expand=True, padx=8, pady=(4, 0))

        cols = ('sel', 'folder', 'count', 'files')
        self.tv = ttk.Treeview(tf, columns=cols, show='headings',
                               selectmode='extended')
        self.tv.heading('sel',    text='✓')
        self.tv.heading('folder', text='作成フォルダ名（クリックで編集）')
        self.tv.heading('count',  text='件数')
        self.tv.heading('files',  text='ファイル例')

        self.tv.column('sel',    width=32,  anchor='center', stretch=False, minwidth=32)
        self.tv.column('folder', width=260, minwidth=100)
        self.tv.column('count',  width=55,  anchor='center', minwidth=40)
        self.tv.column('files',  width=600, minwidth=200)

        vsb = ttk.Scrollbar(tf, orient='vertical',   command=self.tv.yview)
        hsb = ttk.Scrollbar(tf, orient='horizontal', command=self.tv.xview)
        self.tv.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tv.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.grid(row=1, column=0, sticky='ew')
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)

        self.tv.tag_configure('normal', foreground='#1F3864')
        self.tv.tag_configure('done',   foreground='#375623')
        self.tv.tag_configure('error',  foreground='#C00000', background='#FFF0F0')
        self.tv.tag_configure('exists', foreground='#7F6000', background='#FFFBE6')

        self.tv.bind('<ButtonRelease-1>', self._on_click)

        bb = tk.Frame(self, bg='#E0E4EA', pady=7)
        bb.pack(fill='x', side='bottom')
        self.status_var = tk.StringVar(value="スキャン待機中")
        tk.Label(bb, textvariable=self.status_var,
                 bg='#E0E4EA', font=('Meiryo UI', 9)).pack(side='left', padx=14)
        tk.Button(bb, text="  実行  ", command=self._execute,
                  bg='#1F6B38', fg='white', font=('Meiryo UI', 11, 'bold'),
                  relief='flat', cursor='hand2', pady=4).pack(side='right', padx=10)
        tk.Button(bb, text="全解除", command=lambda: self._set_all(False),
                  bg='#E0E4EA', font=('Meiryo UI', 9),
                  relief='flat', padx=8).pack(side='right', padx=2)
        tk.Button(bb, text="全選択", command=lambda: self._set_all(True),
                  bg='#E0E4EA', font=('Meiryo UI', 9),
                  relief='flat', padx=8).pack(side='right', padx=2)

    def _pick_folder(self):
        cur = self.dir_var.get().rstrip('\\')
        chosen = filedialog.askdirectory(
            title="対象フォルダを選択",
            initialdir=cur if os.path.isdir(cur) else '/',
            parent=self)
        if chosen:
            self.dir_var.set(chosen.replace('/', '\\').rstrip('\\') + '\\')
            self._scan()

    def _scan(self):
        folder = self.dir_var.get().rstrip('\\') + '\\'
        if not os.path.isdir(folder):
            messagebox.showerror("エラー", f"フォルダが見つかりません:\n{folder}", parent=self)
            return

        self.tv.delete(*self.tv.get_children())
        self.rows.clear()

        try:
            files = sorted(f for f in os.listdir(folder)
                           if os.path.isfile(os.path.join(folder, f)))
        except PermissionError as e:
            messagebox.showerror("エラー", str(e), parent=self)
            return

        record_folder(folder)
        self.dir_cb['values'] = load_folder_history()

        # 番組名ごとにグループ化
        groups: dict = {}   # folder_name -> [filename, ...]
        for filename in files:
            ext = os.path.splitext(filename)[1].lower()
            if ext not in SUPPORTED_EXTS:
                continue
            info = parse_renamed_file(filename)
            if info:
                raw_prog = info.get('prog_name', '').strip()
            else:
                # カテゴリなし・プレフィックスなしのファイル
                # 未処理の録画ファイル（8桁数字始まり・!始まり）はスキップ
                if re.match(r'^\d{8}', filename) or filename.startswith('!'):
                    continue
                base = os.path.splitext(filename)[0]
                raw_prog, _ = auto_split_title(base)
            prog = _normalize_folder_name(raw_prog)
            if not prog:
                prog = '(番組名なし)'
            folder_name = _sanitize_folder_name(prog)
            groups.setdefault(folder_name, []).append(filename)

        for idx, (folder_name, file_list) in enumerate(sorted(groups.items())):
            iid     = str(idx)
            example = '  /  '.join(file_list[:2])
            if len(file_list) > 2:
                example += f'  … 他{len(file_list)-2}件'
            dest_exists = os.path.isdir(os.path.join(folder, folder_name))
            tag  = 'exists' if dest_exists else 'normal'
            vals = ('☑', folder_name, str(len(file_list)), example)
            self.tv.insert('', 'end', iid=iid, values=vals, tags=(tag,))
            self.rows.append({'iid': iid, 'folder': folder_name,
                              'files': file_list, 'selected': True,
                              'exists': dest_exists})

        n = len(self.rows)
        total = sum(len(r['files']) for r in self.rows)
        exists_count = sum(1 for r in self.rows if r['exists'])
        msg = f"スキャン完了  {n} 番組  合計 {total} ファイル"
        if exists_count:
            msg += f"  （フォルダ既存: {exists_count} 件 — 黄色表示、追加移動します）"
        self.status_var.set(msg if n else "対象ファイルが見つかりませんでした")

    def _get_row(self, iid):
        try:
            return self.rows[int(iid)]
        except (IndexError, ValueError):
            return None

    def _on_click(self, event):
        iid = self.tv.identify_row(event.y)
        col = self.tv.identify_column(event.x)
        if not iid:
            return
        row = self._get_row(iid)
        if not row:
            return
        if col == '#1':
            row['selected'] = not row['selected']
            v = list(self.tv.item(iid, 'values'))
            v[0] = '☑' if row['selected'] else '☐'
            self.tv.item(iid, values=v)
        elif col == '#2':
            self._edit_folder_name(iid, row)

    def _edit_folder_name(self, iid, row):
        try:
            x, y, w, h = self.tv.bbox(iid, '#2')
        except Exception:
            return
        var = tk.StringVar(value=row['folder'])
        ent = tk.Entry(self.tv, textvariable=var, font=('Meiryo UI', 9))
        ent.place(x=x, y=y, width=max(w, 160), height=h)
        ent.focus_set()
        ent.icursor('end')
        self._popup = ent

        def apply(e=None):
            new_name = _sanitize_folder_name(var.get())
            if new_name:
                row['folder'] = new_name
                v = list(self.tv.item(iid, 'values'))
                v[1] = new_name
                self.tv.item(iid, values=v)
            try:
                ent.destroy()
            except Exception:
                pass
            self._popup = None

        ent.bind('<Return>',   apply)
        ent.bind('<Tab>',      apply)
        ent.bind('<Escape>',   lambda e: (ent.destroy(), setattr(self, '_popup', None)))
        ent.bind('<FocusOut>', apply)

    def _set_all(self, sel: bool):
        icon = '☑' if sel else '☐'
        for row in self.rows:
            row['selected'] = sel
            v = list(self.tv.item(row['iid'], 'values'))
            v[0] = icon
            self.tv.item(row['iid'], values=v)

    def _execute(self):
        targets = [r for r in self.rows if r['selected']]
        if not targets:
            messagebox.showinfo("情報", "実行対象がありません。", parent=self)
            return

        total = sum(len(r['files']) for r in targets)
        preview = '\n'.join(
            f"  📂 {r['folder']}  ({len(r['files'])}件)" for r in targets[:8])
        if len(targets) > 8:
            preview += f'\n  … 他 {len(targets)-8} フォルダ'

        if not messagebox.askyesno("実行確認",
                f"{len(targets)} フォルダ、計 {total} ファイルを移動します。\n\n{preview}\n\n続行しますか？",
                parent=self):
            return

        folder  = self.dir_var.get().rstrip('\\') + '\\'
        ok = skip = err_cnt = 0
        err_msgs = []

        for row in targets:
            dest_dir = os.path.join(folder, row['folder'])
            try:
                os.makedirs(dest_dir, exist_ok=True)
            except Exception as ex:
                err_msgs.append(f"フォルダ作成失敗 {row['folder']}: {ex}")
                v = list(self.tv.item(row['iid'], 'values'))
                v[0] = '✗'
                self.tv.item(row['iid'], values=v, tags=('error',))
                err_cnt += 1
                continue

            for filename in row['files']:
                src = os.path.join(folder, filename)
                dst = os.path.join(dest_dir, filename)
                try:
                    if os.path.exists(dst):
                        skip += 1
                        continue
                    os.rename(src, dst)
                    ok += 1
                except Exception as ex:
                    err_msgs.append(f"{filename}: {ex}")
                    err_cnt += 1

            v = list(self.tv.item(row['iid'], 'values'))
            v[0] = '✓'
            self.tv.item(row['iid'], values=v, tags=('done',))

        parts = [f"成功: {ok} ファイル"]
        if skip:
            parts.append(f"既存スキップ: {skip}")
        if err_cnt:
            parts.append(f"エラー: {err_cnt}")
        self.status_var.set("完了  " + "  ".join(parts))

        if err_msgs:
            messagebox.showerror("エラーあり", '\n'.join(err_msgs[:15]), parent=self)
        else:
            messagebox.showinfo("完了",
                f"✓  {ok} ファイルを移動しました！\n{len(targets)} 個のフォルダに整理されました。",
                parent=self)


# ────────────────────────────────────────────────────────────
#  アーカイブ整理ダイアログ
# ────────────────────────────────────────────────────────────

class ArchiveDialog(tk.Toplevel):

    def __init__(self, app):
        super().__init__(app)
        self.app        = app
        self.rows       = []
        self._popup     = None
        self._popup_save = None
        self._saved_sel = ()
        self._prog_history = load_prog_history()   # {番組名: 最終使用日}
        self.title("アーカイブ整理")
        self.geometry("1400x680")
        self.minsize(800, 400)
        self.transient(app)
        self._apply_style()
        self._build()
        self.after(150, self._scan)

    def _apply_style(self):
        s = ttk.Style(self)
        s.configure('Treeview',
                    font=('Meiryo UI', 9), rowheight=25,
                    background='#FFFFFF', fieldbackground='#FFFFFF')
        s.configure('Treeview.Heading',
                    font=('Meiryo UI', 9, 'bold'),
                    background='#2E4057', foreground='white', relief='flat')

    def _build(self):
        hdr = tk.Frame(self, bg='#2E4057', height=46)
        hdr.pack(fill='x')
        hdr.pack_propagate(False)
        tk.Label(hdr, text="アーカイブ整理",
                 font=('Meiryo UI', 13, 'bold'), fg='white', bg='#2E4057'
                 ).pack(side='left', padx=18, pady=10)
        tk.Label(hdr,
                 text="【カテゴリ】 番組名 (日付) タイトル [codec].mp4  形式に変換",
                 font=('Meiryo UI', 9), fg='#A0B8CC', bg='#2E4057'
                 ).pack(side='left', pady=14)

        tb = tk.Frame(self, bg='#E8ECF0', pady=6)
        tb.pack(fill='x')
        tk.Label(tb, text="  フォルダ:", bg='#E8ECF0',
                 font=('Meiryo UI', 9)).pack(side='left')
        self.dir_var = tk.StringVar(value=self.app.dir_var.get())
        self.dir_cb = ttk.Combobox(tb, textvariable=self.dir_var,
                                   values=load_folder_history(),
                                   width=36, font=('Meiryo UI', 9))
        self.dir_cb.pack(side='left', padx=2)
        tk.Button(tb, text=" スキャン ", command=self._scan,
                  bg='#2E75B6', fg='white', font=('Meiryo UI', 9),
                  relief='flat', cursor='hand2').pack(side='left', padx=6)
        tk.Label(tb,
                 text="番組名・タイトル・カテゴリ列をクリックして編集  →  変換後プレビューに即反映",
                 bg='#E8ECF0', fg='#555', font=('Meiryo UI', 8)).pack(side='left')

        tf = tk.Frame(self)
        tf.pack(fill='both', expand=True, padx=8, pady=(4, 0))

        cols = ('sel', 'original', 'prog', 'eptitle', 'cat', 'new_name', 'status')
        self.tv = ttk.Treeview(tf, columns=cols, show='headings',
                               selectmode='extended')
        self.tv.heading('sel',      text='✓')
        self.tv.heading('original', text='変換前ファイル名')
        self.tv.heading('prog',     text='番組名 / アーティスト名  ▶ クリックで編集')
        self.tv.heading('eptitle',  text='タイトル  ▶ クリックで編集')
        self.tv.heading('cat',      text='カテゴリ ▶ 変更')
        self.tv.heading('new_name', text='変換後プレビュー')
        self.tv.heading('status',   text='状態')

        self.tv.column('sel',      width=32,  anchor='center', stretch=False, minwidth=32)
        self.tv.column('original', width=300, minwidth=120)
        self.tv.column('prog',     width=180, minwidth=80)
        self.tv.column('eptitle',  width=150, minwidth=60)
        self.tv.column('cat',      width=90,  anchor='center', minwidth=70)
        self.tv.column('new_name', width=390, minwidth=150)
        self.tv.column('status',   width=70,  anchor='center', minwidth=60)

        vsb = ttk.Scrollbar(tf, orient='vertical',   command=self.tv.yview)
        hsb = ttk.Scrollbar(tf, orient='horizontal', command=self.tv.xview)
        self.tv.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tv.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.grid(row=1, column=0, sticky='ew')
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)

        self.tv.tag_configure('normal', foreground='#2E4057')
        self.tv.tag_configure('done',   foreground='#375623')
        self.tv.tag_configure('error',  foreground='#C00000', background='#FFF0F0')
        self.tv.tag_configure('skip',   foreground='#AAAAAA', background='#F8F8F8')

        self.tv.bind('<ButtonPress-1>',   self._on_press)
        self.tv.bind('<ButtonRelease-1>', self._on_click)
        self.tv.bind('<Button-3>',        self._on_right_click)

        bb = tk.Frame(self, bg='#E0E4EA', pady=7)
        bb.pack(fill='x', side='bottom')
        self.status_var = tk.StringVar(value="スキャン待機中")
        tk.Label(bb, textvariable=self.status_var,
                 bg='#E0E4EA', font=('Meiryo UI', 9)).pack(side='left', padx=14)
        tk.Button(bb, text="  実行  ", command=self._execute,
                  bg='#375623', fg='white', font=('Meiryo UI', 11, 'bold'),
                  relief='flat', cursor='hand2', pady=4).pack(side='right', padx=10)
        tk.Button(bb, text="全解除", command=lambda: self._set_all(False),
                  bg='#E0E4EA', font=('Meiryo UI', 9),
                  relief='flat', padx=8).pack(side='right', padx=2)
        tk.Button(bb, text="全選択", command=lambda: self._set_all(True),
                  bg='#E0E4EA', font=('Meiryo UI', 9),
                  relief='flat', padx=8).pack(side='right', padx=2)

    # ── スキャン ─────────────────────────────────────────────
    def _scan(self):
        self._destroy_popup()
        folder = self.dir_var.get().rstrip('\\') + '\\'
        if not os.path.isdir(folder):
            messagebox.showerror("エラー", f"フォルダが見つかりません:\n{folder}", parent=self)
            return

        self.tv.delete(*self.tv.get_children())
        self.rows.clear()

        try:
            files = sorted(f for f in os.listdir(folder)
                           if os.path.isfile(os.path.join(folder, f)))
        except PermissionError as e:
            messagebox.showerror("エラー", str(e), parent=self)
            return

        record_folder(folder)
        self.dir_cb['values'] = load_folder_history()

        for idx, filename in enumerate(files):
            info = parse_renamed_file(filename)
            if not info:
                continue
            iid      = str(idx)
            new_name = build_archive_name(info)
            cat      = info.get('category', '【TV】')
            vals     = ('☐', filename, info['prog_name'], info['ep_title'], cat, new_name, '待機')
            self.tv.insert('', 'end', iid=iid, values=vals, tags=('normal',))
            self.rows.append({'iid': iid, 'original': filename,
                              'info': info, 'selected': False, 'new_name': new_name})

        n = len(self.rows)
        self.status_var.set(
            f"スキャン完了  {n} 件  ← 番組名列をクリックして番組名を入力、タイトル列でタイトルを入力"
            if n else "リネーム済みファイルが見つかりませんでした")

    # ── 行取得 ───────────────────────────────────────────────
    def _get_row(self, iid: str):
        return next((r for r in self.rows if r['iid'] == iid), None)

    # ── 右クリックメニュー ───────────────────────────────────
    def _on_right_click(self, event):
        iid = self.tv.identify_row(event.y)
        if not iid:
            return
        sel = self.tv.selection()
        if iid not in sel:
            self.tv.selection_set(iid)
            sel = (iid,)
        targets = [i for i in sel if self._get_row(i)]
        if not targets:
            return
        menu = tk.Menu(self, tearoff=0, font=('Meiryo UI', 9))
        menu.add_command(
            label=f"☑  対象に追加  （{len(targets)} 件）",
            command=lambda: self._check_highlighted(targets, True))
        menu.add_command(
            label=f"☐  対象から除外（{len(targets)} 件）",
            command=lambda: self._check_highlighted(targets, False))
        menu.tk_popup(event.x_root, event.y_root)

    def _check_highlighted(self, iids, sel: bool):
        icon = '☑' if sel else '☐'
        for iid in iids:
            row = self._get_row(iid)
            if row:
                row['selected'] = sel
                v = list(self.tv.item(iid, 'values'))
                v[0] = icon
                self.tv.item(iid, values=v)

    # ── クリック ─────────────────────────────────────────────
    def _on_press(self, event):
        self._saved_sel = self.tv.selection()
        col = self.tv.identify_column(event.x)
        iid = self.tv.identify_row(event.y)
        if col in ('#3', '#4', '#5') and iid and self._get_row(iid):
            return "break"

    def _on_click(self, event):
        self._destroy_popup()
        iid = self.tv.identify_row(event.y)
        col = self.tv.identify_column(event.x)
        if not iid:
            return
        row = self._get_row(iid)
        if not row:
            return

        if col == '#1':
            row['selected'] = not row['selected']
            v = list(self.tv.item(iid, 'values'))
            v[0] = '☑' if row['selected'] else '☐'
            self.tv.item(iid, values=v)
        elif col in ('#3', '#4'):
            sel = self._saved_sel
            if len(sel) > 1 and iid in sel:
                targets = [i for i in sel if self._get_row(i)]
            else:
                targets = [iid]
            field    = 'prog_name' if col == '#3' else 'ep_title'
            col_idx  = 2           if col == '#3' else 3
            self._show_entry(iid, targets, col, field, col_idx)
        elif col == '#5':
            sel = self._saved_sel
            if len(sel) > 1 and iid in sel:
                targets = [i for i in sel if self._get_row(i)]
            else:
                targets = [iid]
            self._show_cat_combo(iid, targets)

    # ── インライン Entry / Combobox（複数行対応） ────────────
    def _show_entry(self, anchor_iid, target_iids: list, col_id, field, col_idx):
        try:
            x, y, w, h = self.tv.bbox(anchor_iid, col_id)
        except Exception:
            return
        anchor_row = self._get_row(anchor_iid)
        var = tk.StringVar(value=anchor_row['info'][field] if anchor_row else '')

        if field == 'prog_name':
            # 番組名: 学習済み候補をドロップダウンで表示
            names = list(self._prog_history.keys())   # 最終使用日の新しい順
            widget = ttk.Combobox(self.tv, textvariable=var, values=names,
                                  font=('Meiryo UI', 9))
        else:
            widget = tk.Entry(self.tv, textvariable=var, font=('Meiryo UI', 9))

        widget.place(x=x, y=y, width=max(w, 160), height=h)
        widget.focus_set()
        if hasattr(widget, 'icursor'):
            widget.icursor('end')
        self._popup = widget

        def save():
            val = var.get().strip()
            if field == 'prog_name' and val:
                record_prog_name(val)
                self._prog_history[val] = date.today().isoformat()
            for tid in target_iids:
                r = self._get_row(tid)
                if r:
                    r['info'][field] = val
                    r['new_name']    = build_archive_name(r['info'])
                    v = list(self.tv.item(tid, 'values'))
                    v[col_idx] = val
                    v[5]       = r['new_name']
                    self.tv.item(tid, values=v)
            if len(target_iids) > 1:
                label = '番組名' if field == 'prog_name' else 'タイトル'
                self.status_var.set(
                    f"{len(target_iids)} 件の{label}を「{val}」に一括変更しました")

        self._popup_save = save

        def apply(e=None):
            self._popup_save = None
            save()
            self._destroy_popup()

        if field == 'prog_name':
            widget.bind('<<ComboboxSelected>>', apply)
        widget.bind('<Return>',   apply)
        widget.bind('<Tab>',      apply)
        widget.bind('<Escape>',   lambda e: self._cancel_popup())
        widget.bind('<FocusOut>', apply)

    # ── カテゴリ変更コンボ ────────────────────────────────────
    def _show_cat_combo(self, anchor_iid: str, target_iids: list):
        try:
            x, y, w, h = self.tv.bbox(anchor_iid, '#5')
        except Exception:
            return
        anchor_row = self._get_row(anchor_iid)
        cur_cat = list(self.tv.item(anchor_iid, 'values'))[4] if anchor_row else ''
        var = tk.StringVar(value=cur_cat)
        cats = getattr(self.app, 'categories_list', ['【TV】', '[TV]', '【映画】'])
        cb = ttk.Combobox(self.tv, textvariable=var, values=cats,
                          state='readonly', font=('Meiryo UI', 9))
        cb.place(x=x, y=y, width=max(w, 110), height=h)
        cb.focus_set()
        self._popup = cb

        def save():
            new_cat = var.get()
            if not new_cat:
                return
            for tid in target_iids:
                r = self._get_row(tid)
                if r:
                    r['info']['category'] = new_cat
                    r['new_name'] = build_archive_name(r['info'])
                    v = list(self.tv.item(tid, 'values'))
                    v[4] = new_cat
                    v[5] = r['new_name']
                    self.tv.item(tid, values=v)
            if len(target_iids) > 1:
                self.status_var.set(
                    f"{len(target_iids)} 件のカテゴリを「{new_cat}」に変更しました")

        self._popup_save = save

        def apply(e=None):
            self._popup_save = None
            save()
            self._destroy_popup()

        cb.bind('<<ComboboxSelected>>', apply)
        cb.bind('<Return>',   apply)
        cb.bind('<Escape>',   lambda e: self._cancel_popup())
        cb.bind('<FocusOut>', apply)

    def _destroy_popup(self):
        if self._popup:
            # クリック等で直接 destroy される場合も保存を実行
            fn = getattr(self, '_popup_save', None)
            self._popup_save = None
            if fn:
                try:
                    fn()
                except Exception:
                    pass
            try:
                self._popup.destroy()
            except Exception:
                pass
            self._popup = None

    def _cancel_popup(self):
        """保存せずにポップアップを閉じる（Escape 用）"""
        self._popup_save = None
        if self._popup:
            try:
                self._popup.destroy()
            except Exception:
                pass
            self._popup = None

    # ── 全選択 / 全解除 ──────────────────────────────────────
    def _set_all(self, sel: bool):
        icon = '☑' if sel else '☐'
        for row in self.rows:
            row['selected'] = sel
            v = list(self.tv.item(row['iid'], 'values'))
            v[0] = icon
            self.tv.item(row['iid'], values=v)

    # ── 実行 ─────────────────────────────────────────────────
    def _execute(self):
        self._destroy_popup()
        targets = [r for r in self.rows if r.get('selected') and r.get('new_name')]
        if not targets:
            messagebox.showinfo("情報", "実行対象がありません。", parent=self)
            return

        preview = '\n\n'.join(
            f"  {r['original']}\n  → {r['new_name']}" for r in targets[:5])
        if len(targets) > 5:
            preview += f'\n\n  … 他 {len(targets)-5} 件'

        if not messagebox.askyesno("実行確認",
                f"{len(targets)} 件をリネームします。\n\n{preview}\n\n続行しますか？",
                parent=self):
            return

        folder   = self.dir_var.get().rstrip('\\') + '\\'
        ok = skip = 0
        errs = []

        for row in targets:
            old_path = os.path.join(folder, row['original'])
            new_path = os.path.join(folder, row['new_name'])
            iid = row['iid']
            v   = list(self.tv.item(iid, 'values'))
            try:
                if (os.path.exists(new_path) and
                        os.path.abspath(old_path).lower() !=
                        os.path.abspath(new_path).lower()):
                    skip += 1
                    v[6] = '既存'
                    self.tv.item(iid, values=v, tags=('skip',))
                    continue
                os.rename(old_path, new_path)
                ok += 1
                v[6] = '✓完了'
                self.tv.item(iid, values=v, tags=('done',))
            except Exception as ex:
                errs.append(f"{row['original']}: {ex}")
                v[6] = 'エラー'
                self.tv.item(iid, values=v, tags=('error',))

        parts = [f"成功: {ok} 件"]
        if skip:
            parts.append(f"既存スキップ: {skip} 件")
        if errs:
            parts.append(f"エラー: {len(errs)} 件")
        self.status_var.set("完了  " + "  ".join(parts))

        if errs:
            messagebox.showerror("エラーあり", '\n'.join(errs[:15]), parent=self)
        else:
            msg = f"✓  {ok} 件のリネームが完了しました！"
            if skip:
                msg += f"\n（{skip} 件は既存ファイルのためスキップ）"
            messagebox.showinfo("完了", msg, parent=self)


# ────────────────────────────────────────────────────────────
#  設定ダイアログ
# ────────────────────────────────────────────────────────────

class SettingsDialog(tk.Toplevel):

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("設定")
        self.resizable(False, False)
        self.grab_set()
        self.transient(app)
        self._build()
        self.update_idletasks()
        x = app.winfo_x() + (app.winfo_width()  - self.winfo_width())  // 2
        y = app.winfo_y() + (app.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{x}+{y}")

    def _build(self):
        px = dict(padx=14, fill='x')

        # ── プレフィックス ────────────────────────────────────
        f0 = tk.LabelFrame(self, text="ファイル名プレフィックス",
                           font=('Meiryo UI', 9), padx=10, pady=6)
        f0.pack(**px, pady=(12, 5))
        self._prefix_var = tk.BooleanVar(value=self.app.use_prefix_var.get())
        self._prefix_str_var = tk.StringVar(value=self.app.prefix_str_var.get())

        row0 = tk.Frame(f0)
        row0.pack(anchor='w')
        self._prefix_cb = tk.Checkbutton(row0, text='先頭にプレフィックスを付ける',
                                         variable=self._prefix_var,
                                         font=('Meiryo UI', 10),
                                         command=self._toggle_prefix_entry)
        self._prefix_cb.pack(side='left')
        tk.Label(row0, text="文字:", font=('Meiryo UI', 9)).pack(side='left', padx=(12, 2))
        vcmd = (self.register(lambda s: len(s) <= 5), '%P')
        self._prefix_entry = tk.Entry(row0, textvariable=self._prefix_str_var,
                                      width=6, font=('Meiryo UI', 10),
                                      validate='key', validatecommand=vcmd)
        self._prefix_entry.pack(side='left')
        tk.Label(row0, text="（最大5文字）", font=('Meiryo UI', 8),
                 fg='#666').pack(side='left', padx=4)
        self._toggle_prefix_entry()   # 初期状態を反映

        self._preview_lbl = tk.Label(f0, font=('Meiryo UI', 8), fg='#666', justify='left')
        self._preview_lbl.pack(anchor='w')
        self._update_prefix_preview()
        self._prefix_var.trace_add('write', lambda *_: self._update_prefix_preview())
        self._prefix_str_var.trace_add('write', lambda *_: self._update_prefix_preview())

        # ── 番組表記号 ─────────────────────────────────────────
        f1 = tk.LabelFrame(self, text="番組表記号の除去（スキャン時に適用）",
                           font=('Meiryo UI', 9), padx=10, pady=8)
        f1.pack(**px, pady=5)
        tk.Label(f1, text="除去する記号をチェック。外すと記号がファイル名に残ります。",
                 font=('Meiryo UI', 8), fg='#555').pack(anchor='w', pady=(0, 4))

        all_syms = ['字', '字幕', '解', '新', '二', '多', 'SS', 'S', 'HD',
                    'デ', '再', '無', '吹', '初', '生', '5.1', '音声']
        self._sym_vars = {}
        frm = tk.Frame(f1)
        frm.pack(anchor='w')
        for i, sym in enumerate(all_syms):
            var = tk.BooleanVar(value=(sym in self.app.strip_symbols))
            self._sym_vars[sym] = var
            tk.Checkbutton(frm, text=f'[{sym}]', variable=var,
                           font=('Meiryo UI', 9)).grid(
                               row=i // 6, column=i % 6, sticky='w', padx=4)

        # ── デフォルトカテゴリ ────────────────────────────────
        f2 = tk.LabelFrame(self, text="デフォルトカテゴリ（スキャン時に自動設定）",
                           font=('Meiryo UI', 9), padx=10, pady=8)
        f2.pack(**px, pady=5)
        tk.Label(f2, text="通常番組:", font=('Meiryo UI', 9)).grid(
            row=0, column=0, sticky='w', pady=2)
        self._cat_normal_var = tk.StringVar(value=self.app.cat_normal_var.get())
        ttk.Combobox(f2, textvariable=self._cat_normal_var,
                     values=self.app.categories_list, width=14,
                     font=('Meiryo UI', 9)).grid(row=0, column=1, padx=8, pady=2, sticky='w')
        tk.Label(f2, text="話数あり:", font=('Meiryo UI', 9)).grid(
            row=1, column=0, sticky='w', pady=2)
        self._cat_episode_var = tk.StringVar(value=self.app.cat_episode_var.get())
        ttk.Combobox(f2, textvariable=self._cat_episode_var,
                     values=self.app.categories_list, width=14,
                     font=('Meiryo UI', 9)).grid(row=1, column=1, padx=8, pady=2, sticky='w')

        # ── コーデック ────────────────────────────────────────
        f3 = tk.LabelFrame(self, text="コーデック情報",
                           font=('Meiryo UI', 9), padx=10, pady=8)
        f3.pack(**px, pady=5)
        self._codec_var = tk.StringVar(value=self.app.codec_var.get())
        tk.Entry(f3, textvariable=self._codec_var, width=30,
                 font=('Meiryo UI', 9)).pack(fill='x')

        # ── カテゴリ一覧 ───────────────────────────────────────
        f4 = tk.LabelFrame(self, text="カテゴリ一覧（カンマ区切り）",
                           font=('Meiryo UI', 9), padx=10, pady=8)
        f4.pack(**px, pady=5)
        self._cats_var = tk.StringVar(value=', '.join(self.app.categories_list))
        tk.Entry(f4, textvariable=self._cats_var, width=44,
                 font=('Meiryo UI', 9)).pack(fill='x', pady=2)
        tk.Label(f4,
                 text="例: 【アニメ】, 【ドラマ】, 【TV】, 【映画】, [LIVE], [TV], [MV]",
                 font=('Meiryo UI', 8), fg='#666').pack(anchor='w')

        # ── 時間付加フォーマット ───────────────────────────────
        f5 = tk.LabelFrame(self, text="時間付加フォーマット（⏱ 時間付加 チェック時に使用）",
                           font=('Meiryo UI', 9), padx=10, pady=8)
        f5.pack(**px, pady=5)
        tk.Label(f5, text="フォーマット:", font=('Meiryo UI', 9)).grid(
            row=0, column=0, sticky='w')
        self._dur_fmt_var = tk.StringVar(value=self.app.duration_fmt_var.get())
        dur_fmts = ['[{h}h]?{m}m{ss}s', '[{h}h]?{m}m{s}s',
                    '[{h}:]?{mm}:{ss}', '{h}:{mm}:{ss}', '{hh}:{mm}:{ss}']
        ttk.Combobox(f5, textvariable=self._dur_fmt_var, values=dur_fmts,
                     width=18, font=('Meiryo UI', 9)).grid(
                         row=0, column=1, padx=8, sticky='w')
        self._dur_preview = tk.Label(f5, font=('Meiryo UI', 8), fg='#555')
        self._dur_preview.grid(row=0, column=2, sticky='w')
        self._dur_fmt_var.trace_add('write', lambda *_: self._update_dur_preview())
        self._update_dur_preview()
        tk.Label(f5,
                 text="{h}/{m}/{s}=ゼロ埋めなし  {hh}/{mm}/{ss}=2桁",
                 font=('Meiryo UI', 8), fg='#888').grid(
                     row=1, column=0, columnspan=3, sticky='w', pady=(2, 0))

        tk.Label(self,
                 text="※ 番組表記号・カテゴリ変更はスキャン後に反映されます",
                 font=('Meiryo UI', 8), fg='#888').pack(pady=(4, 6))

        bf = tk.Frame(self)
        bf.pack(pady=(0, 12))
        tk.Button(bf, text="  OK  ", command=self._apply,
                  bg='#2E75B6', fg='white', font=('Meiryo UI', 10),
                  relief='flat', cursor='hand2').pack(side='left', padx=8)
        tk.Button(bf, text="キャンセル", command=self.destroy,
                  bg='#E0E4EA', font=('Meiryo UI', 9),
                  relief='flat', padx=8).pack(side='left', padx=4)

    def _update_dur_preview(self):
        fmt = self._dur_fmt_var.get()
        try:
            long_s  = format_duration(6330, fmt)   # 1h45m30s のサンプル
            short_s = format_duration(1449, fmt)   # 24m09s のサンプル
            self._dur_preview.config(text=f"→ {short_s}  /  {long_s}")
        except Exception:
            self._dur_preview.config(text="")

    def _toggle_prefix_entry(self):
        state = 'normal' if self._prefix_var.get() else 'disabled'
        self._prefix_entry.config(state=state)

    def _update_prefix_preview(self):
        use = self._prefix_var.get()
        p   = self._prefix_str_var.get()[:5]
        pre = f"{p} " if (use and p) else ""
        self._preview_lbl.config(
            text=f"  例: {pre}【TV】 (2026.05.01) タイトル [codec].mp4")

    def _apply(self):
        self.app.use_prefix_var.set(self._prefix_var.get())
        self.app.prefix_str_var.set(self._prefix_str_var.get()[:5])
        self.app.cat_normal_var.set(self._cat_normal_var.get())
        self.app.cat_episode_var.set(self._cat_episode_var.get())
        self.app.codec_var.set(self._codec_var.get())

        self.app.strip_symbols = [s for s, v in self._sym_vars.items() if v.get()]
        self.app._bs_regex = build_bs_regex(self.app.strip_symbols)

        raw  = self._cats_var.get()
        cats = [c.strip() for c in raw.split(',') if c.strip()]
        if cats:
            self.app.categories_list = cats

        self.app.duration_fmt_var.set(self._dur_fmt_var.get())

        self.app._rebuild_previews()
        self.destroy()


# ────────────────────────────────────────────────────────────
#  GUI アプリ
# ────────────────────────────────────────────────────────────

COL_SEL    = 0
COL_ORIG   = 1
COL_NEW    = 2
COL_CAT    = 3
COL_STATUS = 4


class App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("encord リネームアプリ v1.4")
        self.geometry("1500x780")
        self.minsize(900, 500)
        self.configure(bg='#F5F5F5')

        self.rows              = []
        self._popup            = None
        self._popup_save       = None
        self._saved_selection  = ()

        self._config = load_config()

        # 設定値
        self.use_prefix_var  = tk.BooleanVar(value=True)
        self.prefix_str_var  = tk.StringVar(value="!")
        self.cat_normal_var  = tk.StringVar(value=DEFAULT_CAT_NORMAL)
        self.cat_episode_var = tk.StringVar(value=DEFAULT_CAT_EPISODE)
        self.codec_var       = tk.StringVar(value=DEFAULT_CODEC)
        self.categories_list = list(CATEGORIES)
        self.strip_symbols   = list(DEFAULT_STRIP_SYMBOLS)
        self._bs_regex       = build_bs_regex(self.strip_symbols)
        self.use_duration_var = tk.BooleanVar(
            value=self._config.get('use_duration', False))
        self.duration_fmt_var = tk.StringVar(
            value=self._config.get('duration_fmt', '[{h}h]?{m}m{ss}s'))
        self._apply_style()
        self._build_ui()

        self.codec_var.trace_add('write',        lambda *_: self._rebuild_previews())
        self.use_prefix_var.trace_add('write',   lambda *_: self._rebuild_previews())
        self.prefix_str_var.trace_add('write',   lambda *_: self._rebuild_previews())
        self.use_duration_var.trace_add('write', lambda *_: (
            save_config({'use_duration': self.use_duration_var.get()}),
            self._rebuild_previews()))
        self.duration_fmt_var.trace_add('write', lambda *_: (
            save_config({'duration_fmt': self.duration_fmt_var.get()}),
            self._rebuild_previews()))

        self.after(300, self.scan)

    # ── スタイル ────────────────────────────────────────────
    def _apply_style(self):
        s = ttk.Style(self)
        s.theme_use('clam')
        s.configure('Treeview',
                    font=('Meiryo UI', 9), rowheight=25,
                    background='#FFFFFF', fieldbackground='#FFFFFF')
        s.configure('Treeview.Heading',
                    font=('Meiryo UI', 9, 'bold'),
                    background='#1F3864', foreground='white', relief='flat')
        s.map('Treeview.Heading', background=[('active', '#2E75B6')])
        s.configure('TCombobox', font=('Meiryo UI', 9))

    # ── UI 構築 ──────────────────────────────────────────────
    def _build_ui(self):
        hdr = tk.Frame(self, bg='#1F3864', height=52)
        hdr.pack(fill='x')
        hdr.pack_propagate(False)
        tk.Label(hdr, text="encord リネームアプリ",
                 font=('Meiryo UI', 15, 'bold'),
                 fg='white', bg='#1F3864').pack(side='left', padx=20, pady=12)
        tk.Label(hdr, text="v1.4",
                 font=('Meiryo UI', 9), fg='#7BA7D0', bg='#1F3864').pack(
                     side='left', pady=18)

        tb = tk.Frame(self, bg='#E8ECF0', pady=6)
        tb.pack(fill='x')
        tk.Label(tb, text="  フォルダ:", bg='#E8ECF0',
                 font=('Meiryo UI', 9)).pack(side='left')
        self.dir_var = tk.StringVar(
            value=self._config.get('last_folder', DEFAULT_DIR))
        self.dir_cb = ttk.Combobox(tb, textvariable=self.dir_var,
                                   values=load_folder_history(),
                                   width=36, font=('Meiryo UI', 9))
        self.dir_cb.pack(side='left', padx=2)
        tk.Button(tb, text="...", command=self._pick_folder,
                  bg='#D0D4DA', font=('Meiryo UI', 9),
                  relief='flat', padx=6, cursor='hand2').pack(side='left', padx=2)
        tk.Button(tb, text=" スキャン ", command=self.scan,
                  bg='#2E75B6', fg='white', font=('Meiryo UI', 9),
                  relief='flat', cursor='hand2').pack(side='left', padx=6)
        tk.Label(tb, text="コーデック:", bg='#E8ECF0',
                 font=('Meiryo UI', 9)).pack(side='left', padx=(10, 0))
        tk.Entry(tb, textvariable=self.codec_var, width=22,
                 font=('Meiryo UI', 9)).pack(side='left', padx=2)
        tk.Checkbutton(tb, text="⏱ 時間付加", variable=self.use_duration_var,
                       bg='#E8ECF0', font=('Meiryo UI', 9),
                       activebackground='#E8ECF0',
                       cursor='hand2').pack(side='left', padx=(4, 2))
        tk.Button(tb, text="⚙ 設定", command=self._open_settings,
                  bg='#D0D4DA', font=('Meiryo UI', 9),
                  relief='flat', padx=8, cursor='hand2').pack(side='left', padx=4)
        tk.Button(tb, text="📁 アーカイブ整理", command=self._open_archive,
                  bg='#5B4A8A', fg='white', font=('Meiryo UI', 9),
                  relief='flat', padx=8, cursor='hand2').pack(side='left', padx=4)
        tk.Button(tb, text="📂 フォルダ分け", command=self._open_folder_sort,
                  bg='#1F6B38', fg='white', font=('Meiryo UI', 9),
                  relief='flat', padx=8, cursor='hand2').pack(side='left', padx=4)
        tk.Label(tb,
                 text="Shift/Ctrl+クリックで複数選択 → カテゴリ列クリックで一括変更",
                 bg='#E8ECF0', fg='#555', font=('Meiryo UI', 8)).pack(side='left')

        # テーブル
        tf = tk.Frame(self)
        tf.pack(fill='both', expand=True, padx=8, pady=(4, 0))

        cols = ('sel', 'original', 'new_name', 'category', 'status')
        self.tv = ttk.Treeview(tf, columns=cols, show='headings',
                               selectmode='extended')   # 複数選択モード
        self.tv.heading('sel',      text='✓')
        self.tv.heading('original', text='変換前ファイル名')
        self.tv.heading('new_name', text='変換後ファイル名  （ダブルクリックで手動編集）')
        self.tv.heading('category', text='カテゴリ ▾（クリックで変更）')
        self.tv.heading('status',   text='状態')

        self.tv.column('sel',      width=32,  anchor='center', stretch=False, minwidth=32)
        self.tv.column('original', width=420, minwidth=150)
        self.tv.column('new_name', width=610, minwidth=150)
        self.tv.column('category', width=120, anchor='center', minwidth=90)
        self.tv.column('status',   width=80,  anchor='center', minwidth=60)

        vsb = ttk.Scrollbar(tf, orient='vertical',   command=self.tv.yview)
        hsb = ttk.Scrollbar(tf, orient='horizontal', command=self.tv.xview)
        self.tv.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tv.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.grid(row=1, column=0, sticky='ew')
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)

        self.tv.tag_configure('skip',    foreground='#AAAAAA', background='#F8F8F8')
        self.tv.tag_configure('episode', foreground='#6B238E')
        self.tv.tag_configure('normal',  foreground='#1F3864')
        self.tv.tag_configure('done',    foreground='#375623')
        self.tv.tag_configure('error',   foreground='#C00000', background='#FFF0F0')

        self.tv.bind('<ButtonPress-1>',   self._on_press)
        self.tv.bind('<ButtonRelease-1>', self._on_click)
        self.tv.bind('<Double-ButtonRelease-1>', self._on_dblclick)
        self.tv.bind('<Button-3>',        self._on_right_click)

        bb = tk.Frame(self, bg='#E0E4EA', pady=7)
        bb.pack(fill='x', side='bottom')
        self.status_var = tk.StringVar(value="スキャン待機中")
        tk.Label(bb, textvariable=self.status_var,
                 bg='#E0E4EA', font=('Meiryo UI', 9)).pack(side='left', padx=14)
        tk.Button(bb, text="  実行  ", command=self.execute,
                  bg='#375623', fg='white', font=('Meiryo UI', 11, 'bold'),
                  relief='flat', cursor='hand2', pady=4).pack(side='right', padx=10)
        tk.Button(bb, text="全解除", command=lambda: self._set_all(False),
                  bg='#E0E4EA', font=('Meiryo UI', 9),
                  relief='flat', padx=8).pack(side='right', padx=2)
        tk.Button(bb, text="全選択", command=lambda: self._set_all(True),
                  bg='#E0E4EA', font=('Meiryo UI', 9),
                  relief='flat', padx=8).pack(side='right', padx=2)

    # ── 設定ダイアログ ────────────────────────────────────────
    def _open_settings(self):
        SettingsDialog(self)

    # ── アーカイブ整理ダイアログ ──────────────────────────────
    def _open_archive(self):
        ArchiveDialog(self)

    # ── フォルダ分けダイアログ ────────────────────────────────
    def _open_folder_sort(self):
        FolderSortDialog(self)

    # ── プレビュー全再生成 ────────────────────────────────────
    def _rebuild_previews(self):
        codec      = self.codec_var.get().strip() or DEFAULT_CODEC
        use_prefix = self.use_prefix_var.get()
        prefix_str = self.prefix_str_var.get()[:5]
        use_dur    = self.use_duration_var.get()
        dur_fmt    = self.duration_fmt_var.get()
        for row in self.rows:
            if row.get('skip'):
                continue
            dur_secs = row.get('duration_secs')
            dur_str  = format_duration(dur_secs, dur_fmt) if (use_dur and dur_secs is not None) else ''
            row['new_name'] = build_new_name(
                row['info'], row['category'], codec, use_prefix, prefix_str, dur_str,
                row.get('title_prefix', ''))
            v = list(self.tv.item(row['iid'], 'values'))
            v[COL_NEW] = row['new_name']
            self.tv.item(row['iid'], values=v)

    # ── フォルダ選択 ─────────────────────────────────────────
    def _pick_folder(self):
        current = self.dir_var.get().rstrip('\\')
        chosen = filedialog.askdirectory(
            title="対象フォルダを選択",
            initialdir=current if os.path.isdir(current) else '/',
        )
        if chosen:
            chosen = chosen.replace('/', '\\').rstrip('\\') + '\\'
            self.dir_var.set(chosen)
            self.scan()

    # ── スキャン ────────────────────────────────────────────
    def scan(self):
        self._destroy_popup()
        folder = self.dir_var.get().rstrip('\\') + '\\'
        self.dir_var.set(folder)

        if not os.path.isdir(folder):
            messagebox.showerror("エラー",
                f"フォルダが見つかりません:\n{folder}\n\n"
                "ドライブが接続されているか確認してください。")
            self.status_var.set("フォルダが見つかりません")
            return

        self.tv.delete(*self.tv.get_children())
        self.rows.clear()

        try:
            files = sorted(
                f for f in os.listdir(folder)
                if os.path.isfile(os.path.join(folder, f))
            )
        except PermissionError as e:
            messagebox.showerror("エラー", f"フォルダを読み込めません:\n{e}")
            return

        record_folder(folder)
        self.dir_cb['values'] = load_folder_history()

        codec       = self.codec_var.get().strip() or DEFAULT_CODEC
        use_prefix  = self.use_prefix_var.get()
        prefix_str  = self.prefix_str_var.get()[:5]
        cat_normal  = self.cat_normal_var.get() or DEFAULT_CAT_NORMAL
        cat_episode = self.cat_episode_var.get() or DEFAULT_CAT_EPISODE

        use_dur           = self.use_duration_var.get()
        dur_fmt           = self.duration_fmt_var.get()
        prog_cat_map      = load_prog_cat_map()
        title_prefix_map  = load_title_prefix_map()

        for idx, filename in enumerate(files):
            iid  = str(idx)
            info = analyze_file(filename, self._bs_regex, cat_normal, cat_episode)

            if info.get('skip'):
                vals = ('—', filename, '（スキップ）', '—', info.get('reason', ''))
                self.tv.insert('', 'end', iid=iid, values=vals, tags=('skip',))
                self.rows.append({'iid': iid, 'original': filename,
                                  'info': info, 'skip': True, 'selected': False,
                                  'duration_secs': None})
            else:
                cat  = info.get('category', cat_normal)
                # 記憶済みカテゴリを適用（映画・LIVE は上書きしない）
                # 2段階分割: 'SONGS 鈴木雅之▽...' → 'SONGS 鈴木雅之' → 'SONGS'
                prog_name = auto_split_title(auto_split_title(info['title'])[0])[0]
                if cat not in ('【映画】', '[LIVE]'):
                    if prog_name in prog_cat_map:
                        cat = prog_cat_map[prog_name]
                # タイトルプレフィックス適用（例: 豊臣兄弟! → 大河 豊臣兄弟!）
                title_pfx = title_prefix_map.get(prog_name, '')
                # 再生時間の取得（mp4 / avi）
                _fp = os.path.join(folder, filename)
                _ex = info.get('ext', '')
                if _ex == '.mp4':
                    dur_secs = read_mp4_duration(_fp)
                elif _ex == '.avi':
                    dur_secs = read_avi_duration(_fp)
                else:
                    dur_secs = None
                dur_str  = format_duration(dur_secs, dur_fmt) if (use_dur and dur_secs is not None) else ''
                new_name = build_new_name(info, cat, codec, use_prefix, prefix_str, dur_str, title_pfx)
                tag      = 'episode' if info.get('has_ep') else 'normal'
                vals     = ('☑', filename, new_name, cat, '待機')
                self.tv.insert('', 'end', iid=iid, values=vals, tags=(tag,))
                self.rows.append({'iid': iid, 'original': filename,
                                  'info': info, 'skip': False, 'selected': True,
                                  'new_name': new_name, 'category': cat,
                                  'duration_secs': dur_secs, 'title_prefix': title_pfx})

        n        = sum(1 for r in self.rows if not r['skip'])
        s        = sum(1 for r in self.rows if r['skip'])
        ep       = sum(1 for r in self.rows if not r.get('skip') and r['info'].get('has_ep'))
        no_dur   = sum(1 for r in self.rows
                       if not r.get('skip') and r.get('duration_secs') is None
                       and r['info'].get('ext') in ('.mp4', '.avi'))
        msg = f"スキャン完了  対象: {n}件  スキップ: {s}件  話数あり: {ep}件"
        if no_dur and self.use_duration_var.get():
            msg += f"  ⚠ {no_dur}件は時間取得不可（録画中のファイルはロックされています）"
        elif n:
            msg += "  ← Shift/Ctrl+クリックで複数選択→カテゴリ一括変更"
        self.status_var.set(msg)

    # ── 右クリックメニュー ───────────────────────────────────
    def _on_right_click(self, event):
        iid = self.tv.identify_row(event.y)
        if not iid:
            return
        sel = self.tv.selection()
        if iid not in sel:
            self.tv.selection_set(iid)
            sel = (iid,)
        # スキップ行を除外
        targets = [i for i in sel if not (self._get_row(i) or {}).get('skip')]
        if not targets:
            return
        menu = tk.Menu(self, tearoff=0, font=('Meiryo UI', 9))
        menu.add_command(
            label=f"☑  対象に追加  （{len(targets)} 件）",
            command=lambda: self._check_highlighted(targets, True))
        menu.add_command(
            label=f"☐  対象から除外（{len(targets)} 件）",
            command=lambda: self._check_highlighted(targets, False))
        menu.tk_popup(event.x_root, event.y_root)

    def _check_highlighted(self, iids, sel: bool):
        icon = '☑' if sel else '☐'
        for iid in iids:
            row = self._get_row(iid)
            if row and not row.get('skip'):
                row['selected'] = sel
                v = list(self.tv.item(iid, 'values'))
                v[COL_SEL] = icon
                self.tv.item(iid, values=v)

    # ── クリック処理 ────────────────────────────────────────
    def _on_press(self, event):
        """ButtonPress: カテゴリ列のとき treeview のデフォルト選択変更を阻止して保存"""
        self._saved_selection = self.tv.selection()
        iid = self.tv.identify_row(event.y)
        col = self.tv.identify_column(event.x)
        if col == '#4' and iid:
            row = self._get_row(iid)
            if row and not row.get('skip'):
                return "break"   # treeview が selection を変更しないようにする

    def _get_row(self, iid: str):
        try:
            return self.rows[int(iid)]
        except (IndexError, ValueError):
            return None

    def _on_click(self, event):
        self._destroy_popup()
        iid = self.tv.identify_row(event.y)
        col = self.tv.identify_column(event.x)
        if not iid:
            return
        row = self._get_row(iid)
        if not row or row['skip']:
            return

        # ☑ 列: このアイテムのチェックだけ切替（treeview selection には影響しない）
        if col == '#1':
            row['selected'] = not row['selected']
            v = list(self.tv.item(iid, 'values'))
            v[COL_SEL] = '☑' if row['selected'] else '☐'
            self.tv.item(iid, values=v)
            return

        # 変換後名列: 既に選択中の行をシングルクリックで編集（未選択行は通常選択）
        if col == '#3' and iid in getattr(self, '_saved_selection', ()):
            self._show_name_popup(iid, row)
            return

        # カテゴリ列: 選択中の全非スキップ行に適用
        if col == '#4':
            # _on_press で保存した「クリック前の選択」を使う
            # (ButtonRelease 時点では treeview が既に deselect している)
            sel = getattr(self, '_saved_selection', ())
            if len(sel) > 1 and iid in sel:
                targets = [i for i in sel
                           if not (self._get_row(i) or {}).get('skip')]
            else:
                targets = [iid]
            self._show_cat_popup(iid, targets)

    def _on_dblclick(self, event):
        iid = self.tv.identify_row(event.y)
        col = self.tv.identify_column(event.x)
        if not iid:
            return
        row = self._get_row(iid)
        if not row or row['skip']:
            return
        if col == '#3':
            self._show_name_popup(iid, row)

    # ── インライン カテゴリ Combobox（複数行対応） ────────────
    def _show_cat_popup(self, anchor_iid, target_iids: list):
        try:
            x, y, w, h = self.tv.bbox(anchor_iid, '#4')
        except Exception:
            return
        anchor_row = self._get_row(anchor_iid)
        var = tk.StringVar(value=anchor_row['category'] if anchor_row else '')
        cb  = ttk.Combobox(self.tv, textvariable=var,
                           values=self.categories_list,
                           width=10, font=('Meiryo UI', 9))
        cb.place(x=x, y=y, width=max(w, 120), height=h)
        cb.focus_set()
        self._popup = cb

        def apply(e=None):
            new_cat = var.get().strip()
            if not new_cat:
                self._destroy_popup()
                return
            codec      = self.codec_var.get().strip() or DEFAULT_CODEC
            use_prefix = self.use_prefix_var.get()
            prefix_str = self.prefix_str_var.get()[:5]
            use_dur    = self.use_duration_var.get()
            dur_fmt    = self.duration_fmt_var.get()
            for tid in target_iids:
                r = self._get_row(tid)
                if r and not r.get('skip'):
                    r['category'] = new_cat
                    dur_secs = r.get('duration_secs')
                    dur_str  = format_duration(dur_secs, dur_fmt) if (use_dur and dur_secs is not None) else ''
                    r['new_name'] = build_new_name(r['info'], new_cat, codec, use_prefix, prefix_str, dur_str,
                                                   r.get('title_prefix', ''))
                    v = list(self.tv.item(tid, 'values'))
                    v[COL_CAT] = new_cat
                    v[COL_NEW] = r['new_name']
                    self.tv.item(tid, values=v)
            self._destroy_popup()
            cnt = len(target_iids)
            if cnt > 1:
                self.status_var.set(f"カテゴリを {cnt} 件まとめて「{new_cat}」に変更しました")

        cb.bind('<<ComboboxSelected>>', apply)
        cb.bind('<Return>',   apply)
        cb.bind('<Escape>',   lambda e: self._destroy_popup())
        cb.bind('<FocusOut>', apply)

    # ── インライン 変換後名 Entry ────────────────────────────
    def _show_name_popup(self, iid, row):
        try:
            x, y, w, h = self.tv.bbox(iid, '#3')
        except Exception:
            return
        var = tk.StringVar(value=row['new_name'])
        ent = tk.Entry(self.tv, textvariable=var, font=('Meiryo UI', 9))
        ent.place(x=x, y=y, width=w, height=h)
        ent.focus_set()
        ent.icursor('end')
        self._popup = ent

        def save():
            row['new_name'] = var.get().strip()
            v = list(self.tv.item(iid, 'values'))
            v[COL_NEW] = row['new_name']
            self.tv.item(iid, values=v)

        self._popup_save = save

        def apply(e=None):
            self._popup_save = None
            save()
            self._destroy_popup()

        ent.bind('<Return>',   apply)
        ent.bind('<Escape>',   lambda e: self._cancel_popup())
        ent.bind('<FocusOut>', apply)

    def _destroy_popup(self):
        if self._popup:
            fn = self._popup_save
            self._popup_save = None
            if fn:
                try:
                    fn()
                except Exception:
                    pass
            try:
                self._popup.destroy()
            except Exception:
                pass
            self._popup = None

    def _cancel_popup(self):
        """保存せずにポップアップを閉じる（Escape 用）"""
        self._popup_save = None
        if self._popup:
            try:
                self._popup.destroy()
            except Exception:
                pass
            self._popup = None

    # ── 全選択 / 全解除 ──────────────────────────────────────
    def _set_all(self, sel: bool):
        icon = '☑' if sel else '☐'
        for row in self.rows:
            if not row['skip']:
                row['selected'] = sel
                v = list(self.tv.item(row['iid'], 'values'))
                v[COL_SEL] = icon
                self.tv.item(row['iid'], values=v)

    # ── 実行 ────────────────────────────────────────────────
    def execute(self):
        self._destroy_popup()
        targets = [r for r in self.rows
                   if not r['skip'] and r.get('selected') and r.get('new_name')]
        if not targets:
            messagebox.showinfo("情報",
                "実行対象がありません。\n☑ のチェックを確認してください。")
            return

        preview_lines = [f"  {r['original']}\n  → {r['new_name']}"
                         for r in targets[:5]]
        preview = '\n\n'.join(preview_lines)
        if len(targets) > 5:
            preview += f'\n\n  … 他 {len(targets) - 5} 件'

        if not messagebox.askyesno(
                "実行確認",
                f"{len(targets)} 件のファイルをリネームします。\n\n{preview}\n\n続行しますか？"):
            return

        folder   = self.dir_var.get()
        ok_cnt   = 0
        skip_cnt = 0
        err_msgs = []

        for row in targets:
            old_path = os.path.join(folder, row['original'])
            new_path = os.path.join(folder, row['new_name'])
            iid = row['iid']
            v   = list(self.tv.item(iid, 'values'))

            try:
                if (os.path.exists(new_path) and
                        os.path.abspath(old_path).lower() !=
                        os.path.abspath(new_path).lower()):
                    skip_cnt += 1
                    v[COL_STATUS] = '既存'
                    self.tv.item(iid, values=v, tags=('skip',))
                    continue

                os.rename(old_path, new_path)
                ok_cnt += 1
                v[COL_STATUS] = '✓完了'
                self.tv.item(iid, values=v, tags=('done',))
                # 番組名→カテゴリ を記憶（次回スキャン時に自動適用）
                if not row.get('skip') and row.get('category') and row.get('info'):
                    _prog = auto_split_title(auto_split_title(row['info'].get('title', ''))[0])[0]
                    if _prog:
                        save_prog_cat(_prog, row['category'])

            except Exception as ex:
                err_msgs.append(f"{row['original']}: {ex}")
                v[COL_STATUS] = 'エラー'
                self.tv.item(iid, values=v, tags=('error',))

        parts = [f"成功: {ok_cnt} 件"]
        if skip_cnt:
            parts.append(f"既存スキップ: {skip_cnt} 件")
        if err_msgs:
            parts.append(f"エラー: {len(err_msgs)} 件")
        self.status_var.set("実行完了  " + "  ".join(parts))

        if err_msgs:
            messagebox.showerror("エラーあり",
                '\n'.join(err_msgs[:15]) +
                (f'\n\n… 他 {len(err_msgs)-15} 件' if len(err_msgs) > 15 else ''))
        else:
            msg = f"✓  {ok_cnt} 件のリネームが完了しました！"
            if skip_cnt:
                msg += f"\n（{skip_cnt} 件は既にリネーム済みのためスキップ）"
            messagebox.showinfo("完了", msg)


# ────────────────────────────────────────────────────────────

if __name__ == '__main__':
    app = App()
    app.mainloop()
