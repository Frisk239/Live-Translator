"""真听译：PCM 进（WS 二进制帧，f32le/mono/16kHz）→ Silero VAD → SenseVoice-Small →
CTranslate2（int8；缺模型时回退 OPUS-MT onnx int8）→ 按 ADR 0002
切条 → 只回三类缝事件（草稿 / 定稿 / 提示）。

模型（壳首次打开下载，目录由 --models-dir 传入）：
  sense-voice/model.int8.onnx + tokens.txt   （sherpa-onnx）
  vad/silero_vad.onnx                        （sherpa-onnx）
  opus-*/tokenizer.json                      （Xenova，给 CT2 分词）
  opus-*-ct2/                                （CTranslate2 int8）
可选 LLM 定稿：desktop/llm.local.json（不进 git）。草稿仍 CT2，缺配置或超时回退 CT2。
ja/ko 官方无 →zh 权重，经 en 转译。

依赖：numpy websockets sherpa-onnx onnxruntime tokenizers ctranslate2（可选，缺则回退 onnx）
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

# ---------- ADR 0002 切条常量（写死，观众不能调） ----------
TRANS_MAX_CHARS = 20   # 硬顶：简中电影字幕约 16 字/行，一条最多约一行半，不等整句
SEG_MAX_SECONDS = 3.5  # 硬顶：连说也不让一条超过约半句的阅读窗
PAUSE_CUT_MS = 600     # 口气停顿：静音到这个长度就切
IMMATURE_PAUSE_CUT_MS = 1500  # 文字还不够成一条时的停顿切条阈值：再等一段，不出标题式定稿
CLAUSE_MIN_CHARS = 10  # 从句/逗号：译文够一条就切，不跟到句号
COMMA_MIN_CHARS = 10   # 逗号切条：与从句同一条门槛
PUNCT_CUT_MIN_S = 1.2        # 标点切条：句段至少说这么久（增量前缀不切）
PUNCT_CUT_MIN_WORDS = 4      # 标点切条：至少这么多个词
PUNCT_CUT_MIN_PAUSE_MS = 250  # 句末切条还要等一小口气：没换气的句号是句中停顿，
                              # 切出去的片段没有上下文，译文会碎（实测 en 演说体
                              # "for those / who here gave their lives" 类碎片）
DRAFT_INTERVAL_MS = 350  # 草稿刷新周期（开口→草稿的主要延迟来源）
# 试过每条开头 2 秒内加密到 200ms 轮询：满载下空解码白烧 CPU，还会把 SenseVoice
# 停顿处的异语 glitch 窗口放大成真事件（ko 夹具串进寝よう？），已回退。
# 阶段2 VAD 驱动（新有声≥200ms / 有声→静音）8×3：ja/ko 首草稿 -125/-249ms，
# 但 ko_fast 放出「맞だ.」孤立定稿（红线），已回退。
DRAFT_MIN_GAP_MS = 100   # 上一轮重活结束后至少缓一口气，避免解码连打占满 CPU
DRAFT_EMPTY_RETRY_MS = 150  # 开头缓冲太短识别为空：快点重试，别等满一轮
LLM_BAR_HOLD_S = 1.0  # 定稿后最短亮这么久
LLM_BAR_HOLD_MAX_S = 2.0  # 改写还在飞就再等到这个上限，避免 1s 一到 bump 把改写掐死
# 不成条（1 词 / 2 字）不翻译；有声过 0.5s 后再按空识别节奏重试。
# 更早重试会打中开口 200–300ms 的异语 glitch（うん / なんか？）。
IMMATURE_RETRY_MIN_VOICED_S = 0.5
DRAFT_MIN_EN_WORDS = 2   # 最小成条量：英文两词、日韩三字即可先亮草稿；再短不成一条
DRAFT_MIN_CJK_CHARS = 3
NO_AUDIO_MS = 10_000     # 开听后一直没等到 PCM → 音源抓不到
NO_SPEECH_MS = 12_000    # 一直没人声 → 面板提示（周期重发）
NOT_LANG_EMPTY_HITS = 3  # 连续这么多次「有声但识别不出字」→ 不是英日韩
NOT_LANG_COOLDOWN_MS = 8_000
# SenseVoice 是非流式模型。auto 在一个短词上偶尔会跳到另一种语言；
# 不锁定源语言，而是仅在「切换语言」时等一点可验证的上下文。
LANG_SWITCH_MIN_VOICED_S = 1.2
LANG_SWITCH_DROP_SILENCE_MS = 1_200
# 定稿才多走一轮候选搜索：草稿保留低延迟，整句以少量额外等待换更稳的译文。
FINAL_BEAM_SIZE = 2
FINAL_BEAM_LENGTH_PENALTY = 0.6

SAMPLE_RATE = 16_000
VAD_WINDOW = 512  # 32ms
# SenseVoice 结果里残留的控制标记（<|Speech|> / <|en|> / <|NEUTRAL|> …）
_CONTROL_TAG_RE = re.compile(r"<\|[^|]+\|>")

# 数词规范：韩/日 ASR 常把年份、数量说成数词（천 오백 삼십 육 / 千五百三十六），
# 枢轴翻译在这一步最容易把数字译错（实测 1536→1530）。翻前先把成组数词换成
# 阿拉伯数字，译文和观众看到的原文都更稳。保守规则：只转「含量级字（십/백/천/
# 만/억、十/百/千/万/億）的连续数词组」，且多 token 组、或 ≥2 字的单 token 才转
# ——单字量级词（백/천/만）同时是韩语姓氏（백 씨/천 씨），孤立出现不动。
_KO_DIGITS = {"일": 1, "이": 2, "삼": 3, "사": 4, "오": 5, "육": 6, "칠": 7, "팔": 8, "구": 9}
_CJK_DIGITS = {"〇": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_MAGNITUDES = {"십": 10, "백": 100, "천": 1000, "만": 10**4, "억": 10**8,
               "十": 10, "百": 100, "千": 1000, "万": 10**4, "億": 10**8}
# 快语速下 SenseVoice 的 ITN 会输出「1오백3십6」这类 ASCII 数字与数词混排，
# 字符集必须接纳两者，否则该 token 原样进翻译（实测被译成 1503:06）。
_ASCII_DIGITS = {str(d): d for d in range(10)}
_NUMBER_CHARS = set(_KO_DIGITS) | set(_CJK_DIGITS) | set(_MAGNITUDES) | set(_ASCII_DIGITS)


def _parse_number_word(word: str) -> int | None:
    """把一段纯数词（오백삼십 / 千五百三十六 / 一億二千万）解析成数值；非数词返回 None。"""
    total = 0    # 已闭合的大节（万/亿）累计
    section = 0  # 当前节
    current = 0  # 正在累计的个位串
    for ch in word:
        digit = _KO_DIGITS.get(ch, _CJK_DIGITS.get(ch, _ASCII_DIGITS.get(ch)))
        if digit is not None:
            current = current * 10 + digit
            continue
        mag = _MAGNITUDES.get(ch)
        if mag is None:
            return None
        if mag >= 10**4:
            # 만/万、억/億 把当前节闭合进总额（一億二千万 = 1e8 + 2000e4）
            total += ((section + current) or 1) * mag
            section = 0
        else:
            section += (current if current else 1) * mag
        current = 0
    return total + section + current


def _has_magnitude(word: str) -> bool:
    return any(ch in _MAGNITUDES for ch in word)


def normalize_cjk_numbers(text: str) -> str:
    if not text:
        return text
    tokens = text.split(" ")
    out: list[str] = []
    group: list[str] = []

    def flush_group():
        nonlocal group
        convertible = len(group) >= 2 or (len(group) == 1 and len(group[0]) >= 2)
        if group and convertible and any(_has_magnitude(t) for t in group):
            # 组内整体解析：이+천 → 이천 → 2000；천+오백+삼십+육 → 1536
            value = _parse_number_word("".join(group))
            out.append(str(value) if value is not None else " ".join(group))
        else:
            out.extend(group)
        group = []

    for token in tokens:
        if token and all(ch in _NUMBER_CHARS for ch in token):
            group.append(token)
        else:
            flush_group()
            out.append(token)
    flush_group()

    def ja_repl(match: "re.Match[str]") -> str:
        value = _parse_number_word(match.group(0))
        return str(value) if value is not None else match.group(0)

    return re.sub(r"[〇一二三四五六七八九十百千万億]{2,}", ja_repl, " ".join(out))


def send_obj(ws, obj):
    return ws.send(json.dumps(obj, ensure_ascii=False))


def _ort_session_options(intra_threads: int):
    """观众机器上 CPU 被浏览器/播放器抢是常态，线程池按竞争环境配置：
    线程数给小（小模型并行开销反而大，且 6 个会话各一个池）。
    注意：allow_spinning=0 与 dynamic_block_base 在本机实测会引发 ORT 线程池
    死锁（Session.run 永不返回），不要加。"""
    import onnxruntime as ort

    opts = ort.SessionOptions()
    opts.inter_op_num_threads = 1
    opts.intra_op_num_threads = max(1, intra_threads)
    return opts


LLM_FINAL_TIMEOUT_S = 8.0

# 测试用：LT_DEBUG_LOG=路径 时把每条真正发出的草稿/定稿事件落盘，
# 方便回放与真窗验证对账（每句最终译文 = 同一原文最后一条定稿）。
_DEBUG_LOG_PATH = os.environ.get("LT_DEBUG_LOG")


def _debug_log(kind: str, orig: str, trans: str) -> None:
    if not _DEBUG_LOG_PATH:
        return
    try:
        with open(_DEBUG_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {kind} | {orig} | {trans}\n")
    except OSError:
        pass


def _normalize_llm_config(data: dict) -> dict:
    out = dict(data)
    for camel, snake in (
        ("baseUrl", "base_url"),
        ("apiKey", "api_key"),
        ("timeoutS", "timeout_s"),
        ("maxTokens", "max_tokens"),
        ("reasoningEffort", "reasoning_effort"),
        ("thinkingParam", "thinking_param"),
    ):
        if not out.get(snake) and out.get(camel):
            out[snake] = out[camel]
    return out


def load_llm_config(explicit: Path | None = None) -> dict | None:
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(explicit)
    env = os.environ.get("LT_LLM_CONFIG")
    if env:
        candidates.append(Path(env))
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / "com.livetranslator.desktop" / "llm.local.json")
    here = Path(__file__).resolve()
    candidates.append(here.parents[1] / "llm.local.json")
    candidates.append(Path.cwd() / "llm.local.json")
    seen: set[Path] = set()
    for path in candidates:
        path = path.resolve()
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        try:
            # utf-8-sig：Windows 记事本会写 UTF-8 BOM，纯 utf-8 读会带 \ufeff 令 json 解析失败
            data = _normalize_llm_config(json.loads(path.read_text(encoding="utf-8-sig")))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("enabled") is False:
            return None
        if data.get("base_url") and data.get("model") and data.get("api_key"):
            return data
    return None


_LLM_SYSTEM = (
    "你是听译员。把听到的话译成简体中文。"
    "只输出一句译文，不要解释，不要原文，不要引号。"
    "按常识修正明显的识别错误；专名可用通行译法。"
    "直播多为游戏与闲聊语境：游戏术语按通行说法译"
    "（如 boss、HP、buff 不要硬译成生造词），不要加括号注释。"
)


_PUNCT_STRIP_RE = re.compile(
    r"[\s,，.。、!！?？;；:：'\"`「」『』()（）\[\]…\-—_~·]+"
)


def _norm_prefix_text(s: str) -> str:
    """前缀匹配用的归一化：剥标点和空白。ASR 草稿期会来回摆标点
    （"So we." → "So, we are going."），逗号之差不构成译文对不上的证据。"""
    return _PUNCT_STRIP_RE.sub("", s)


def llm_prefetch_usable(pref_src: str, final_src: str) -> bool:
    if not pref_src or not final_src:
        return False
    if pref_src == final_src:
        return True
    a = _norm_prefix_text(pref_src)
    b = _norm_prefix_text(final_src)
    return len(a) >= 4 and b.startswith(a)


def mask_tail(text: str) -> str:
    """草稿译文藏不稳的尾巴：最后 2 个汉字，或最后 2 个英文词。"""
    if not text:
        return ""
    if re.search(r"[\u4e00-\u9fff]", text):
        idxs = [i for i, ch in enumerate(text) if "\u4e00" <= ch <= "\u9fff"]
        if len(idxs) <= 2:
            return ""
        return text[: idxs[-2]].rstrip()
    words = text.split()
    if len(words) <= 2:
        return ""
    return " ".join(words[:-2])


def stable_trans(shown: str, nxt: str) -> str:
    """冻前缀：新译文能接上已亮的字就只追加（仍藏尾巴）；对不上整行不动。不定稿拼接。"""
    if not nxt:
        return shown or ""
    masked = mask_tail(nxt)
    if not shown:
        return masked
    if masked.startswith(shown):
        return masked
    return shown


def draft_worth_llm(text: str) -> bool:
    """本机 LLM 直译：草稿太短不发请求，等接近整句再预取（仍每条一次）。"""
    words = re.findall(r"[A-Za-z0-9]+", text or "")
    if len(words) >= 4:
        return True
    compact = _norm_prefix_text(text or "")
    return len(compact) >= 8


def llm_covers(src: str, target: str, ratio: float = 0.6) -> bool:
    """src 对 target 的覆盖度。碎片译文（"So, we are."→「我们确实如此。」）
    只有盖住大半句才配上屏，否则观众看到的是驴唇不对马嘴的定稿。"""
    a = _norm_prefix_text(src)
    b = _norm_prefix_text(target)
    if not a or not b:
        return False
    return len(a) * 10 >= len(b) * (ratio * 10)


def _llm_looks_repetition(text: str) -> bool:
    if not text or len(text) < 40:
        return False
    for plen in range(8, len(text) // 2 + 1):
        if text[plen : plen * 2] == text[:plen]:
            return True
    return False


def _llm_partial_ready(text: str) -> bool:
    if len(re.findall(r"[\u4e00-\u9fff]", text)) >= 2:
        return True
    return len(text.strip()) >= 4


def _llm_messages(text: str, context: list[tuple[str, str]] | None) -> list[dict]:
    msgs = [{"role": "system", "content": _LLM_SYSTEM}]
    if context:
        for orig, trans in context[-2:]:
            if orig and trans:
                msgs.append({"role": "user", "content": orig})
                msgs.append({"role": "assistant", "content": trans})
    msgs.append({"role": "user", "content": text})
    return msgs


def _iter_sse_deltas(resp):
    buf = b""
    while True:
        chunk = resp.read(256)
        if not chunk:
            break
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            line = line.strip()
            if not line.startswith(b"data:"):
                continue
            data = line[5:].strip()
            if data == b"[DONE]":
                return
            try:
                obj = json.loads(data.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            delta = ((obj.get("choices") or [{}])[0].get("delta") or {}).get("content") or ""
            if delta:
                yield str(delta)


def _llm_thinking_fields(cfg: dict, disable_thinking: bool) -> dict:
    param = str(cfg.get("thinking_param") or cfg.get("thinkingParam") or "").strip()
    val = cfg.get("thinking")
    if isinstance(val, str):
        val = val.strip()
    if disable_thinking:
        return {}
    if not param or val in (None, "", "plain"):
        # 没探测到也不吃接口默认（默认可能是重度思考，听译要快）：
        # 先按最通用的参数带最低档，接口不认由 400 兜底去掉重试
        return {"reasoning_effort": "low"}
    if param == "enable_thinking":
        return {param: str(val).lower() in ("true", "1", "on", "yes")}
    return {param: val}


def llm_translate(
    cfg: dict,
    text: str,
    *,
    context: list[tuple[str, str]] | None = None,
    on_partial=None,
    should_abort=None,
    disable_thinking: bool = False,
    stream: bool = False,
) -> str:
    url = str(cfg["base_url"]).rstrip("/") + "/chat/completions"
    think = cfg.get("thinking")
    max_tokens = int(cfg.get("max_tokens") or (1024 if think else 256))
    payload: dict = {
        "model": cfg["model"],
        "messages": _llm_messages(text, context),
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }
    if stream:
        payload["stream"] = True
    payload.update(_llm_thinking_fields(cfg, disable_thinking))
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + str(cfg["api_key"]),
            "User-Agent": "livetranslator/0.1",
        },
    )
    timeout = float(cfg.get("timeout_s") or LLM_FINAL_TIMEOUT_S)
    _t0 = time.monotonic()
    _ttft_logged = False
    _debug_log("llm-req", text[:36], "start")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if stream:
                chunks: list[str] = []
                for delta in _iter_sse_deltas(resp):
                    if should_abort and should_abort():
                        _debug_log("llm-req", text[:36], f"abort {int((time.monotonic()-_t0)*1000)}ms")
                        return ""
                    chunks.append(delta)
                    got = "".join(chunks).strip().strip("「」\"'")
                    if on_partial and _llm_partial_ready(got):
                        if not _ttft_logged:
                            _ttft_logged = True
                            _debug_log("llm-ttft", text[:36], f"{int((time.monotonic()-_t0)*1000)}ms")
                        on_partial(got)
                out = "".join(chunks).strip().strip("「」\"'")
            else:
                raw = json.loads(resp.read().decode("utf-8"))
                out = str((raw.get("choices") or [{}])[0].get("message", {}).get("content") or "")
                out = out.strip().strip("「」\"'")
    except urllib.error.HTTPError as err:
        _debug_log("llm-err", text[:36], f"HTTP {err.code} {int((time.monotonic()-_t0)*1000)}ms")
        raise
    except Exception as err:  # 超时等网络错：记一笔再抛，调用侧各自兜底
        _debug_log("llm-err", text[:36], f"{type(err).__name__} {int((time.monotonic()-_t0)*1000)}ms")
        raise
    _debug_log("llm-done", text[:36], f"{int((time.monotonic()-_t0)*1000)}ms len={len(out)}")
    if not out or _llm_looks_repetition(out):
        return ""
    return out


def _raise_process_priority_above_normal():
    """听译是观众正在等字幕的实时任务，给 AboveNormal 调度优先级
    （音视频应用的标准做法）；绝不 REALTIME——官方警告会饿死系统。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ABOVE_NORMAL_PRIORITY_CLASS = 0x00008000
        kernel32 = ctypes.windll.kernel32
        kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), ABOVE_NORMAL_PRIORITY_CLASS)
    except Exception:
        pass


class Translator:
    """翻译后端：CTranslate2 优先（int8、增量解码，单句几十毫秒），缺模型回退
    OPUS-MT onnx int8（Xenova 导出，贪心/beam 每步全量重算，慢一个量级）。
    ja/ko 先译成英文再用 en-zh 转成中文。两套后端 to_chinese 接口一致。"""

    # 这批 Xenova decoder_model_merged 导出：no-cache 分支吐出的 encoder present
    # 是零长度透传，回喂 use_cache_branch=True 会在 If/Reshape 上崩（已实测），
    # 增量 KV 走不通。保留验证机制，换过导出后把开关打开即可自动重验。
    TRY_INCREMENTAL_KV = False
    # 同字幕条内草稿：原文是上一版前缀时只贪心译新增后缀，定稿仍整句 beam。
    # 质量门不过就改 False，不删实现。
    TRY_INCREMENTAL_PREFIX = True
    SMOKE = {"opus-en-zh": "hello", "opus-ja-en": "こんにちは", "opus-ko-en": "안녕하세요"}
    _LEADING_PUNCT_RE = re.compile(r"^[\s\u3000,，、。．.!?？！;；:：…—\-]+")

    def __init__(self, models_dir: Path, *, enable_llm: bool = True):
        from tokenizers import Tokenizer
        import onnxruntime as ort

        self.pairs: dict[str, tuple] = {}
        self._extras: dict[int, dict] = {}
        for pair in ("opus-en-zh", "opus-ja-en", "opus-ko-en"):
            d = models_dir / pair
            need = [
                d / "tokenizer.json",
                d / "config.json",
                d / "onnx" / "encoder_model_int8.onnx",
                d / "onnx" / "decoder_model_merged_int8.onnx",
            ]
            # 文件齐全且不可能是限流期下坏的小文件，才尝试加载
            if not all(f.is_file() and f.stat().st_size > 100 for f in need):
                continue
            # Xenova 导出的 tokenizer.json 里 normalizer.precompiled_charsmap 为 null，
            # 原生 tokenizers 库不认 —— 运行时去掉该 normalizer 再加载
            tok_data = json.loads((d / "tokenizer.json").read_text(encoding="utf-8"))
            tok_data["normalizer"] = None
            tok = Tokenizer.from_str(json.dumps(tok_data))
            opts = _ort_session_options(min(8, self._cpu_count()))
            enc = ort.InferenceSession(str(d / "onnx" / "encoder_model_int8.onnx"), opts, providers=["CPUExecutionProvider"])
            dec = ort.InferenceSession(str(d / "onnx" / "decoder_model_merged_int8.onnx"), opts, providers=["CPUExecutionProvider"])
            cfg = json.loads((d / "config.json").read_text(encoding="utf-8"))
            self.pairs[pair] = (tok, enc, dec, cfg)

        # CTranslate2 后端：<pair>-ct2/ 目录存在即启用（tools/fetch_ct2_models.py 下载）。
        # 注意：那批仓库附带的 .spm 与模型词表对不上（实测喂错 token 导致灾难复读），
        # 正确编码器是本仓已有的 tokenizer.json（与 CT2 词表逐 id 对齐，加载期自检）。
        # 加载失败或依赖缺失时静默回退 ONNX。
        self.ct2: dict[str, tuple] = {}
        self._enable_llm = enable_llm
        try:
            import ctranslate2

            for pair in ("opus-en-zh", "opus-ja-en", "opus-ko-en"):
                d = models_dir / f"{pair}-ct2"
                tok_file = models_dir / pair / "tokenizer.json"
                if not (d / "model.bin").is_file() or not (d / "shared_vocabulary.json").is_file() or not tok_file.is_file():
                    continue
                tok_data = json.loads(tok_file.read_text(encoding="utf-8"))
                tok_data["normalizer"] = None
                tok = Tokenizer.from_str(json.dumps(tok_data))
                vocab = json.loads((d / "shared_vocabulary.json").read_text(encoding="utf-8"))
                probe = tok.encode("test")
                if not all(0 <= i < len(vocab) and vocab[i] == t for i, t in zip(probe.ids, probe.tokens)):
                    continue  # 词表不对齐：宁可用慢的 ONNX，不喂错 token
                self.ct2[pair] = (
                    ctranslate2.Translator(str(d), device="cpu", inter_threads=1, intra_threads=4),
                    tok,
                    vocab,
                )
        except Exception:
            self.ct2 = {}
        self._llm_cfg = load_llm_config() if enable_llm else None
        self.clear_incremental()

    def clear_incremental(self):
        self._inc = {"src": "", "tgt": "", "lang": None}

    def _inc_state(self) -> dict:
        state = getattr(self, "_inc", None)
        if state is None:
            state = {"src": "", "tgt": "", "lang": None}
            self._inc = state
        return state

    def _remember_inc(self, src: str, tgt: str, lang: str | None):
        self._inc = {"src": src, "tgt": tgt, "lang": lang}

    def _pair_name(self, pair_stuff) -> str:
        for name, stuff in self.pairs.items():
            if stuff is pair_stuff:
                return name
        return ""

    def _extra_for(self, pair_stuff) -> dict:
        """该语言对是否启用增量 KV。构造器里跑推理会在部分机器上把 ORT 线程池
        卡死（py-spy 实锤 Session.run 永不返回），绝不能在加载期做；验证放到
        首次真实翻译：同一句话增量 KV 与全量重算输出一致才启用，异常即永久退回。"""
        extra = self._extras.get(id(pair_stuff))
        if extra is None:
            extra = {"cache": False, "pp": []}
            if self.TRY_INCREMENTAL_KV:
                try:
                    pp = self._pp_map(pair_stuff[2]) or []
                    smoke = self.SMOKE.get(self._pair_name(pair_stuff), "hello")
                    plain = self._generate(pair_stuff, smoke, 10, beam_size=1, use_cache=False, pp=[])
                    cached = self._generate(pair_stuff, smoke, 10, beam_size=1, use_cache=True, pp=pp)
                    ok = bool(plain) and plain == cached and bool(pp)
                    extra = {"cache": ok, "pp": pp if ok else []}
                except Exception:
                    extra = {"cache": False, "pp": []}
            self._extras[id(pair_stuff)] = extra
        return extra

    @staticmethod
    def _pp_map(dec):
        """decoder 的 past_key_values 输入与 present 输出按名序配对。"""
        pasts = sorted(i.name for i in dec.get_inputs() if i.name.startswith("past_key_values"))
        presents = sorted(o.name for o in dec.get_outputs() if o.name.startswith("present"))
        if not pasts or len(pasts) != len(presents):
            return None
        return list(zip(pasts, presents))

    @staticmethod
    def _cpu_count():
        try:
            import os

            return os.cpu_count() or 4
        except Exception:
            return 4

    def _has(self, pair) -> bool:
        return pair in self.pairs or pair in getattr(self, "ct2", {})

    def prefer_beam_finals(self) -> bool:
        """CT2 跑得起 beam：日/韩定稿也能整句重译，不必再冻结草稿贪心结果。"""
        return bool(getattr(self, "ct2", {}))

    _CT2_SPECIALS = frozenset({"</s>", "<s>", "<pad>", "<unk>"})

    @staticmethod
    def _translate_ct2(entry, text: str, final: bool) -> str:
        translator, tok, vocab = entry
        tokens = tok.encode(text).tokens
        results = translator.translate_batch(
            [tokens],
            beam_size=FINAL_BEAM_SIZE if final else 1,
            max_decoding_length=96,
            return_scores=False,
            # 半句（32 字硬顶截断的尾巴）上贪心会复读；轻度防护成本可忽略
            repetition_penalty=1.1,
            no_repeat_ngram_size=4,
        )
        hyp = [t for t in results[0].hypotheses[0] if t not in Translator._CT2_SPECIALS]
        try:
            return tok.decode([vocab.index(t) for t in hyp]).strip()
        except ValueError:  # 假设外的 token：直接拼片
            return "".join(hyp).replace("▁", " ").strip()

    @staticmethod
    def _zero_past(dec):
        past = {}
        for inp in dec.get_inputs():
            if inp.name.startswith("past_key_values"):
                shape = [d if isinstance(d, int) else 0 for d in inp.shape]
                past[inp.name] = np.zeros(shape, dtype=np.float32)
        return past

    def _encode(self, pair_stuff, text: str):
        tok, enc, dec, cfg = pair_stuff
        input_ids = np.array([tok.encode(text).ids], dtype=np.int64)
        enc_out = enc.run(None, {"input_ids": input_ids, "attention_mask": np.ones_like(input_ids)})
        hidden = next(
            (enc_out[i] for i, output in enumerate(enc.get_outputs()) if "last_hidden" in output.name),
            enc_out[0],
        )
        return input_ids, np.asarray(hidden, dtype=np.float32)

    @staticmethod
    def _log_softmax(logits: np.ndarray) -> np.ndarray:
        logits = logits.astype(np.float64)
        logits = logits - logits.max()
        return logits - np.log(np.exp(logits).sum())

    def _generate(self, pair_stuff, text: str, max_new_tokens: int, beam_size: int, use_cache=None, pp=None) -> str:
        """统一解码：beam_size=1 贪心。增量 KV 时每步只喂新 token，past 沿用 present；
        use_cache 未指定时按语言对取验证结论（首次会触发一次惰性验证）。"""
        tok, enc, dec, cfg = pair_stuff
        if use_cache is None:
            extra = self._extra_for(pair_stuff)
            use_cache = extra["cache"]
            pp = extra["pp"]
        pp = pp or []
        input_ids, hidden = self._encode(pair_stuff, text)
        src_mask = np.ones_like(input_ids)
        pad = int(cfg.get("pad_token_id", 0)) or 0
        eos = int(cfg.get("eos_token_id", 0)) or 0
        logits_name = next(
            (o.name for o in dec.get_outputs() if "logits" in o.name), dec.get_outputs()[0].name
        )

        def step(token_ids: list[int], past: dict, branch: bool, want_past: bool):
            """跑一步 decoder。branch=True 走增量 KV（past 必须非零长度）；
            branch=False 全量重算。want_past 时取 present 以便后续增量。"""
            feed = dict(past)
            for inp in dec.get_inputs():
                n = inp.name
                if n.startswith("past_key_values"):
                    continue
                if n == "input_ids":
                    feed[n] = np.array([token_ids], dtype=np.int64)
                elif n in ("encoder_attention_mask", "attention_mask"):
                    feed[n] = src_mask
                elif "encoder_hidden_states" in n:
                    feed[n] = hidden
                elif "use_cache" in n:
                    feed[n] = np.array([branch])
            if not want_past:
                return dec.run([logits_name], feed)[0][0, -1], None
            outs = dec.run(None, feed)
            by_name = {o.name: arr for o, arr in zip(dec.get_outputs(), outs)}
            new_past = {past_name: by_name[present_name] for past_name, present_name in pp}
            return by_name[logits_name][0, -1], new_past

        def past_has_tokens(past: dict) -> bool:
            first = next(iter(past.values()), None)
            return first is not None and first.shape[2] > 0

        def rank(tokens_len: int, score: float) -> float:
            return score / (max(1, tokens_len) ** FINAL_BEAM_LENGTH_PENALTY)

        # 该导出把 past_key_values.* 声明为必填输入：全量重算也要喂零 past
        # （use_cache_branch=False 时图内不读它，但输入不能缺）。
        past_zero = self._zero_past(dec)

        if beam_size <= 1:
            ids = [pad]
            past = past_zero
            tokens: list[int] = []
            for _ in range(max_new_tokens):
                if use_cache:
                    # 零长度 past 不被缓存分支接受：第一步先全量算并取 present，
                    # 之后每步只喂新 token。past 恒覆盖 ids[:-1]。
                    branch = past_has_tokens(past)
                    logits, past = step(ids[-1:], past, branch, want_past=True)
                else:
                    logits, _ = step(ids, past, branch=False, want_past=False)
                nxt = int(np.argmax(logits))
                if nxt in (eos, pad):
                    break
                tokens.append(nxt)
                ids.append(nxt)
            return tok.decode(tokens, skip_special_tokens=True).strip()

        # 小 beam：已到 eos 的候选留在 beam 里参与排序，避免其余候选无谓算满步数。
        beams: list[dict] = [
            {"ids": [pad], "past": past_zero, "score": 0.0, "done": False}
        ]
        for _ in range(max_new_tokens):
            candidates: list[dict] = []
            for b in beams:
                if b["done"]:
                    candidates.append(b)
                    continue
                if use_cache:
                    branch = past_has_tokens(b["past"])
                    logits, new_past = step(b["ids"][-1:], b["past"], branch, want_past=True)
                else:
                    logits, new_past = step(b["ids"], b["past"], branch=False, want_past=False)
                log_probs = self._log_softmax(logits)
                top = np.argpartition(log_probs, -beam_size)[-beam_size:]
                for token in top:
                    token_id = int(token)
                    candidates.append(
                        {
                            "ids": b["ids"] + [token_id],
                            "past": new_past if use_cache else past_zero,
                            "score": b["score"] + float(log_probs[token_id]),
                            "done": token_id in (eos, pad),
                        }
                    )
            candidates.sort(key=lambda c: rank(len(c["ids"]) - 1, c["score"]), reverse=True)
            beams = candidates[:beam_size]
            if all(b["done"] for b in beams):
                break
        best = max(beams, key=lambda c: rank(len(c["ids"]) - 1, c["score"]))
        tokens = [t for t in best["ids"][1:] if t not in (eos, pad)]
        return tok.decode(tokens, skip_special_tokens=True).strip()

    def _greedy(self, pair_stuff, text: str, max_new_tokens: int = 64) -> str:
        return self._generate(pair_stuff, text, max_new_tokens, beam_size=1)

    def _beam(
        self,
        pair_stuff,
        text: str,
        beam_size: int = FINAL_BEAM_SIZE,
        max_new_tokens: int = 48,
    ) -> str:
        """Marian 的小 beam search；只给定稿用，避免草稿延迟成倍增加。"""
        if beam_size <= 1:
            return self._greedy(pair_stuff, text, max_new_tokens)
        return self._generate(pair_stuff, text, max_new_tokens, beam_size)

    def _translate_pair(self, pair: str, text: str, final: bool) -> str:
        ct2_entry = getattr(self, "ct2", {}).get(pair)
        if ct2_entry is not None:
            return self._translate_ct2(ct2_entry, text, final)
        if final:
            return self._beam(self.pairs[pair], text, FINAL_BEAM_SIZE)
        return self._greedy(self.pairs[pair], text)

    @staticmethod
    def incremental_suffix(prev_src: str, new_src: str) -> str | None:
        """new 是 prev 的前缀延长则返回后缀；否则 None（整句重译）。"""
        if not prev_src or not new_src.startswith(prev_src):
            return None
        return new_src[len(prev_src):]

    @classmethod
    def suffix_worth_translating(cls, suffix: str, lang: str | None) -> str:
        """去掉前导标点后，后缀还有没有可译内容；没有则返回空串。"""
        piece = cls._LEADING_PUNCT_RE.sub("", suffix).strip()
        if not piece:
            return ""
        if lang == "en":
            return piece if re.search(r"[A-Za-z0-9]", piece) else ""
        if lang in ("ja", "ko", "zh"):
            return piece if re.search(r"[\u3040-\u30ff\uac00-\ud7af\u4e00-\u9fff0-9]", piece) else ""
        return piece if re.sub(r"\s", "", piece) else ""

    def _to_chinese_as(self, lang: str | None, text: str, final: bool) -> str:
        """按已判定的源语言走对应语言对，后缀不再自己认语言（汉字后缀会误判成中文透传）。"""
        if not text.strip():
            return ""
        if lang == "zh":
            return text
        if lang == "ja":
            if self._has("opus-ja-en") and self._has("opus-en-zh"):
                en = self._translate_pair("opus-ja-en", text, final)
                return self._translate_pair("opus-en-zh", en, final) if en else ""
            return ""
        if lang == "ko":
            if self._has("opus-ko-en") and self._has("opus-en-zh"):
                en = self._translate_pair("opus-ko-en", text, final)
                return self._translate_pair("opus-en-zh", en, final) if en else ""
            return ""
        if self._has("opus-en-zh"):
            return self._translate_pair("opus-en-zh", text, final)
        return ""

    def _to_chinese_full(self, text: str, final: bool) -> str:
        if not text.strip():
            return ""
        # 判定顺序：假名 → 日文，谚文 → 韩文，纯汉字 → 中文透传（日文汉字会落进 CJK 区，必须后判）
        if self._looks_chinese(text):
            return text
        if self._looks_kana(text):
            return self._to_chinese_as("ja", text, final)
        if self._looks_hangul(text):
            return self._to_chinese_as("ko", text, final)
        return self._to_chinese_as("en", text, final)

    def _try_llm_final(
        self,
        text: str,
        context: list[tuple[str, str]] | None = None,
        on_partial=None,
        should_abort=None,
        stream: bool = False,
    ) -> str:
        if not getattr(self, "_enable_llm", True):
            return ""
        cfg = load_llm_config()
        self._llm_cfg = cfg
        if not cfg:
            return ""
        omit = bool(getattr(self, "_llm_omit_thinking", False))
        try:
            return llm_translate(
                cfg,
                text,
                context=context,
                on_partial=on_partial,
                should_abort=should_abort,
                disable_thinking=omit,
                stream=stream,
            )
        except urllib.error.HTTPError as err:
            if err.code == 400 and not omit:
                self._llm_omit_thinking = True
                try:
                    return llm_translate(
                        cfg,
                        text,
                        context=context,
                        on_partial=on_partial,
                        should_abort=should_abort,
                        disable_thinking=True,
                        stream=stream,
                    )
                except (urllib.error.URLError, TimeoutError, KeyError, IndexError, ValueError, OSError):
                    return ""
            return ""
        except (urllib.error.URLError, TimeoutError, KeyError, IndexError, ValueError, OSError):
            return ""

    def to_chinese(self, text: str, final: bool = False) -> str:
        if not text.strip():
            return ""
        lang = LanguageStability.language_of(text)
        if not self.TRY_INCREMENTAL_PREFIX or final:
            result = self._to_chinese_full(text, final)
            if self.TRY_INCREMENTAL_PREFIX:
                self._remember_inc(text, result, lang)
            return result

        inc = self._inc_state()
        if text == inc["src"] and inc["tgt"]:
            return inc["tgt"]

        suffix = self.incremental_suffix(inc["src"], text) if inc["lang"] == lang else None
        if suffix is not None and inc["tgt"]:
            piece = self.suffix_worth_translating(suffix, lang or inc["lang"])
            if not piece:
                result = inc["tgt"]
            else:
                added = self._to_chinese_as(inc["lang"] or lang, piece, final=False)
                result = (inc["tgt"] + added) if added else self._to_chinese_full(text, False)
            self._remember_inc(text, result, lang or inc["lang"])
            return result

        result = self._to_chinese_full(text, False)
        self._remember_inc(text, result, lang)
        return result

    @staticmethod
    def _looks_chinese(t):
        # 日文汉字混在 CJK 区：含假名 / 谚文先排掉
        if re.search(r"[\u3040-\u30ff\uac00-\ud7af]", t):
            return False
        return bool(re.search(r"[\u4e00-\u9fff]", t))

    @staticmethod
    def _looks_kana(t):
        return bool(re.search(r"[\u3040-\u30ff]", t))

    @staticmethod
    def _looks_hangul(t):
        return bool(re.search(r"[\uac00-\ud7af]", t))


def stream_en_draft_ok(text: str) -> bool:
    """英一流式草稿门：必须是英文，且至少两个 ≥3 字母的词，挡住韩/日上的英文幻觉。"""
    if LanguageStability.language_of(text) != "en":
        return False
    if re.search(r"[\u3040-\u30ff\uac00-\ud7af\u4e00-\u9fff]", text):
        return False
    words = re.findall(r"[A-Za-z]{3,}", text)
    return len(words) >= 2 and any(len(w) >= 4 for w in words)


class StreamingEn:
    """英一流式 Zipformer：只抢首显草稿。缺模型则 disabled。"""

    _FILES = (
        "encoder-epoch-99-avg-1-chunk-16-left-128.int8.onnx",
        "decoder-epoch-99-avg-1-chunk-16-left-128.onnx",
        "joiner-epoch-99-avg-1-chunk-16-left-128.int8.onnx",
        "tokens.txt",
    )

    def __init__(self, models_dir: Path):
        self.rec = None
        self.stream = None
        d = models_dir / "zipformer-en-online"
        if not all((d / name).is_file() and (d / name).stat().st_size > 100 for name in self._FILES):
            return
        import sherpa_onnx

        self.rec = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=str(d / "tokens.txt"),
            encoder=str(d / "encoder-epoch-99-avg-1-chunk-16-left-128.int8.onnx"),
            decoder=str(d / "decoder-epoch-99-avg-1-chunk-16-left-128.onnx"),
            joiner=str(d / "joiner-epoch-99-avg-1-chunk-16-left-128.int8.onnx"),
            num_threads=1,
            sample_rate=SAMPLE_RATE,
            decoding_method="greedy_search",
            enable_endpoint_detection=False,
            provider="cpu",
        )
        self.stream = self.rec.create_stream()

    def enabled(self) -> bool:
        return self.rec is not None

    def accept(self, samples: np.ndarray):
        if self.rec is None or samples.size == 0:
            return
        self.stream.accept_waveform(SAMPLE_RATE, samples.astype(np.float32))
        while self.rec.is_ready(self.stream):
            self.rec.decode_stream(self.stream)

    def text(self) -> str:
        if self.rec is None:
            return ""
        return (self.rec.get_result(self.stream) or "").strip()

    def reset(self):
        if self.rec is None:
            return
        self.rec.reset(self.stream)


class Recognizer:
    """SenseVoice-Small（sherpa-onnx，自动认英/日/韩/粤/中）。"""

    def __init__(self, models_dir: Path):
        import sherpa_onnx

        self.rec = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(models_dir / "sense-voice" / "model.int8.onnx"),
            tokens=str(models_dir / "sense-voice" / "tokens.txt"),
            language="auto",
            use_itn=True,
        )

    def decode(self, samples: np.ndarray) -> str:
        import sherpa_onnx

        s = self.rec.create_stream()
        s.accept_waveform(SAMPLE_RATE, samples.astype(np.float32))
        self.rec.decode_stream(s)
        text = s.result.text.strip()
        # SenseVoice auto 会带语言前缀 token 残留时去掉
        return text


class Vad:
    """Silero VAD（sherpa-onnx 的 onnx，逐 32ms 窗出概率）。"""

    def __init__(self, models_dir: Path):
        import onnxruntime as ort

        # VAD 每 100ms 跑一次、单窗 32ms，1 个线程足够；默认线程池会吃满物理核
        self.sess = ort.InferenceSession(
            str(models_dir / "vad" / "silero_vad.onnx"),
            _ort_session_options(1),
            providers=["CPUExecutionProvider"],
        )
        self.names = {i.name for i in self.sess.get_inputs()}
        if "x" not in self.names:
            raise RuntimeError(f"认不出的 VAD 输入：{self.names}")
        self.v5 = "c" not in self.names  # v5 只有 h（128），v4 是 h/c（各 64）
        if self.v5:
            self.h = np.zeros((2, 1, 128), dtype=np.float32)
            self.c = None
        else:
            self.h = np.zeros((2, 1, 64), dtype=np.float32)
            self.c = np.zeros((2, 1, 64), dtype=np.float32)

    def probs(self, samples: np.ndarray) -> np.ndarray:
        """任意长度（512 的倍数最佳），返回逐窗概率。"""
        out = []
        for i in range(0, len(samples) - VAD_WINDOW + 1, VAD_WINDOW):
            w = samples[i : i + VAD_WINDOW].astype(np.float32).reshape(1, -1)
            feed = {"x": w, "h": self.h}
            if not self.v5:
                feed["c"] = self.c
            if "sr" in self.names:
                feed["sr"] = np.array(SAMPLE_RATE, dtype=np.int64)
            res = self.sess.run(None, feed)
            prob = float(res[0][0][0])
            self.h = res[1]
            if not self.v5:
                self.c = res[2]
            out.append(prob)
        return np.array(out, dtype=np.float32)


class LanguageStability:
    """auto 多语的轻量防抖，不把会话锁到任一语言。

    SenseVoice 经 sherpa-onnx 不提供 LID 置信度或多语言候选。因此只用已经
    识别出的文字体系判断「可能切换」，并只暂缓那个很短的候选：累计到约 1.2 秒
    有声，或后续新增有声后再次识别为同一体系，才确认真正切换。
    """

    def __init__(self):
        self.stable_language: str | None = None
        self.pending: dict[str, int | str] | None = None

    def reset(self):
        self.stable_language = None
        self.pending = None

    def clear_pending(self):
        self.pending = None

    @staticmethod
    def language_of(text: str) -> str | None:
        # sherpa 偶尔会把 <|Speech|> 这类控制 token 留在文本里；它不是英文。
        plain = re.sub(r"<\|[^|]+\|>", "", text)
        has_kana = bool(re.search(r"[\u3040-\u30fa\u30ff]", plain))
        has_hangul = bool(re.search(r"[\uac00-\ud7af]", plain))
        has_han = bool(re.search(r"[\u4e00-\u9fff]", plain))
        has_latin = bool(re.search(r"[A-Za-z]", plain))
        # 假名 / 谚文分别是日文 / 韩文的确定证据；日文汉字不应因此被误当成
        # 「混写」。纯汉字才按中文看待，汉字与拉丁混写则不贸然判断。
        if has_kana:
            return "ja"
        if has_hangul:
            return "ko"
        if has_han and not has_latin:
            return "zh"
        if has_latin and not has_han:
            return "en"
        return None

    def waiting_without_new_voice(self, voiced_samples: int) -> bool:
        return self.pending is not None and voiced_samples <= int(self.pending["last_samples"])

    def should_drop(self, silence_ms: float) -> bool:
        return self.pending is not None and silence_ms >= LANG_SWITCH_DROP_SILENCE_MS

    def observe(self, text: str, voiced_samples: int) -> bool:
        """这次识别能否发给观众。False 表示继续攒上下文。"""
        language = self.language_of(text)
        if self.pending is None:
            if self.stable_language is None or language is None or language == self.stable_language:
                return True
            self.pending = {
                "language": language,
                "last_samples": voiced_samples,
                "confirmations": 1,
            }
        else:
            target = str(self.pending["language"])
            if language != target:
                # 回到原语言（或没得到语言证据）说明刚才的短候选不可靠。
                if language is None or language == self.stable_language:
                    self.pending = None
                    return True
                # 另一个新语言从头计数，避免一次短片段跳两次语言就被确认。
                self.pending = {
                    "language": language,
                    "last_samples": voiced_samples,
                    "confirmations": 1,
                }
            elif voiced_samples > int(self.pending["last_samples"]):
                # 只有新增过有声 PCM 的第二次识别才算第二个证据；静音时的重复
                # decode 不能把同一条误识别「确认」出来。
                self.pending["last_samples"] = voiced_samples
                self.pending["confirmations"] = int(self.pending["confirmations"]) + 1

        assert self.pending is not None
        confirmed = (
            voiced_samples >= int(LANG_SWITCH_MIN_VOICED_S * SAMPLE_RATE)
            or int(self.pending["confirmations"]) >= 2
        )
        if not confirmed:
            return False
        self.stable_language = str(self.pending["language"])
        self.pending = None
        return True

    def commit_final(self, text: str):
        """定稿后的语言才成为下一条的基线；首条不会被无依据地锁住。"""
        language = self.language_of(text)
        if language is not None:
            self.stable_language = language
        self.pending = None


_CLAUSE_END = "，、,;；"
_JA_CLAUSE_TAILS = ("けど", "ので", "から", "て、", "で、")
_KO_CLAUSE_TAILS = ("고", "는데", "지만", "니까")


def clause_closed(text: str) -> bool:
    """从句已闭合：末尾是逗号/顿号，或日韩常见从句尾。不在 and/but 这种未说完的连词上切。"""
    t = (text or "").rstrip()
    if not t:
        return False
    if t[-1] in _CLAUSE_END:
        return True
    return t.endswith(_JA_CLAUSE_TAILS) or t.endswith(_KO_CLAUSE_TAILS)


def min_content_met(text: str) -> bool:
    """最少成条量：英文至少两词，日/韩/中至少三字。再短不成一条字幕。"""
    language = LanguageStability.language_of(text)
    if language == "en":
        return len(re.findall(r"[A-Za-z0-9]+(?:['’][A-Za-z0-9]+)?", text)) >= DRAFT_MIN_EN_WORDS
    if language in ("ja", "ko", "zh"):
        # 只计假名/谚文/汉字：ASCII 句号曾让「맞だ.」这种 2 字异语 glitch 混过 3 字门
        return len(re.findall(r"[\u3040-\u30ff\uac00-\ud7af\u4e00-\u9fff]", text)) >= DRAFT_MIN_CJK_CHARS
    return len(re.sub(r"\s", "", text)) >= DRAFT_MIN_EN_WORDS


class DraftPolicy:
    """草稿显示策略：够最小成条量就先亮，之后识别文本有变化就刷新。

    旧版要求「上一版原文完整保留到下一次识别」才显示上一版译文，每条草稿因此
    晚一整轮，短句整段被定稿抢走，观众全程看不到草稿。CONTEXT.md 的草稿本来
    就允许「切条前可能改掉」，所以这里直接显示当前识别整句的贪心译文。同一文本
    不重发。翻译复用：静音期缓冲没长就整套复用（见 Engine）；同条原文前缀延长
    时草稿只译新增后缀（Translator.TRY_INCREMENTAL_PREFIX），定稿仍整句 beam。
    """

    def __init__(self):
        self.reset()

    def reset(self):
        self.last_shown_text: str | None = None

    def observe(self, text: str, trans: str) -> tuple[str, str] | None:
        if not min_content_met(text):
            return None
        if text == self.last_shown_text:
            return None
        self.last_shown_text = text
        return (text, trans)


class Segmenter:
    """ADR 0002 切条：口气 → 句末 → 从句/逗号 → 字数/时长硬顶（电影字幕阅读窗）。"""

    def __init__(self):
        self.reset()

    def reset(self):
        self.buf = np.zeros(0, dtype=np.float32)
        self.seg_start = None
        self.last_voice_at = None
        self.last_text = ""
        self.last_trans = ""

    def feed_speech(self, samples, now):
        if self.seg_start is None:
            self.seg_start = now
        self.buf = np.concatenate([self.buf, samples])
        self.last_voice_at = now

    def in_speech(self):
        return self.buf.size > 0

    def voiced_seconds(self):
        return self.buf.size / SAMPLE_RATE

    def seg_seconds(self, now):
        return 0.0 if self.seg_start is None else now - self.seg_start

    def silence_ms(self, now):
        return 10**9 if self.last_voice_at is None else (now - self.last_voice_at) * 1000

    def should_cut_no_trans(self, text: str, now: float) -> bool:
        """不看译文就能判的切条：句末标点、时长硬顶、口气停顿。
        定稿路径先查这里，句末/停顿切条不必再跑一轮贪心翻译。"""
        words = len(text.split()) if text else 0
        mature = self.seg_seconds(now) >= PUNCT_CUT_MIN_S and words >= PUNCT_CUT_MIN_WORDS
        # 1) 句末标点（增量识别的短前缀 “So.” 先不切；还要真换气——没停顿的
        # 句号多半是句中小憩，切了就是无上下文碎片）
        if (
            mature
            and text
            and text[-1] in "。？！.?!"
            and self.silence_ms(now) >= PUNCT_CUT_MIN_PAUSE_MS
        ):
            return True
        # 2) 时长硬顶
        if self.seg_seconds(now) >= SEG_MAX_SECONDS:
            return True
        # 3) 口气停顿：已成条的文字等 600ms；孤立少量词再等一段，不出标题式定稿
        pause_ms = PAUSE_CUT_MS if min_content_met(text) else IMMATURE_PAUSE_CUT_MS
        if self.silence_ms(now) >= pause_ms:
            return True
        return False

    def should_cut(self, text: str, trans: str, now: float) -> bool:
        if self.should_cut_no_trans(text, now):
            return True
        trans_n = len(re.sub(r"\s", "", trans or ""))
        # 4) 从句 / 逗号：够一条就切，不等句号
        if (
            self.seg_seconds(now) >= PUNCT_CUT_MIN_S
            and trans_n >= CLAUSE_MIN_CHARS
            and clause_closed(text)
        ):
            return True
        # 5) 字数硬顶：约一行半电影字幕，连说也切
        if trans_n >= TRANS_MAX_CHARS:
            return True
        return False

    def take(self):
        buf, text, trans = self.buf, self.last_text, self.last_trans
        self.reset()
        return buf, text, trans


def models_present(models_dir: Path) -> bool:
    need = [
        models_dir / "sense-voice" / "model.int8.onnx",
        models_dir / "sense-voice" / "tokens.txt",
        models_dir / "vad" / "silero_vad.onnx",
    ]
    return all(p.is_file() and p.stat().st_size > 100 for p in need)


class Engine:
    def __init__(self, models_dir: Path, *, enable_llm: bool = True):
        self.recognizer = Recognizer(models_dir)
        self.vad = Vad(models_dir)
        try:
            self.translator = Translator(models_dir, enable_llm=enable_llm)
        except Exception:
            self.translator = Translator.__new__(Translator)
            self.translator.pairs = {}
            self.translator.ct2 = {}
            self.translator._extras = {}
            self.translator._enable_llm = enable_llm
            self.translator._llm_cfg = None
        self.pcm_rest = np.zeros(0, dtype=np.float32)
        self.empty_hits = 0
        self.last_not_lang_at = -1e9
        self.language_stability = LanguageStability()
        self.draft_policy = DraftPolicy()
        self._last_draft_end = -1e9
        self._decoded_samples = -1
        self._bar_seq = 0
        self._bar_lock = threading.Lock()
        self._need_new_bar = True
        self._llm_prefetch: tuple[int, str, str] | None = None
        self._llm_epoch = 0
        self._llm_context: list[tuple[str, str]] = []
        self._llm_prefetch_inflight = False
        self._llm_revise_inflight = False
        self._pending_final_orig = ""
        self._pending_final_applied = False
        self._llm_revise_pending: tuple[int, str, object] | None = None
        self._bar_draft_text = ""
        self._llm_draft_src = ""
        self._llm_draft_trans = ""
        self._llm_draft_at = 0.0
        self._llm_prefetch_launched: tuple[int, int] | None = None
        self.llm_direct = False
        self._hold_from = 0.0
        self._hold_resets = 0
        self._hold_touch_text = ""
        self._bar_revise_launched: int | None = None
        self._lookahead_src = ""
        self._lookahead_llm = ""
        self._lookahead_gen = 0
        self._lookahead_inflight = False
        self._shown_trans = ""

    def reset_session(self):
        self.pcm_rest = np.zeros(0, dtype=np.float32)
        self.empty_hits = 0
        self._last_draft_at = -1e9
        self._last_draft_end = -1e9
        self._decoded_samples = -1
        self.language_stability.reset()
        self.draft_policy.reset()
        self._clear_inc()
        self._need_new_bar = True
        self._llm_prefetch = None
        self._llm_context = []
        self._llm_prefetch_inflight = False
        self._llm_revise_inflight = False
        self._pending_final_orig = ""
        self._pending_final_applied = False
        self._llm_revise_pending = None
        self._bar_draft_text = ""
        self._llm_draft_src = ""
        self._llm_draft_trans = ""
        self._llm_draft_at = 0.0
        self._hold_from = 0.0
        self._hold_resets = 0
        self._hold_touch_text = ""
        self._bar_revise_launched = None
        self._lookahead_src = ""
        self._lookahead_llm = ""
        self._lookahead_gen += 1
        self._lookahead_inflight = False
        self._shown_trans = ""
        self._bump_bar()

    def _still_holding(self, now: float) -> bool:
        start = getattr(self, "_hold_from", 0.0)
        if start <= 0:
            return False
        elapsed = now - start
        if elapsed < LLM_BAR_HOLD_S:
            return True
        waiting = (
            bool(getattr(self.translator, "_llm_cfg", None))
            and not getattr(self, "_pending_final_applied", False)
            and (
                getattr(self, "_llm_revise_inflight", False)
                or getattr(self, "_llm_revise_pending", None)
            )
        )
        return waiting and elapsed < LLM_BAR_HOLD_MAX_S

    def _touch_hold_for_revise(self, shown: str):
        """纠正版换上屏后，从这一刻重新保底显示：观众读到的最后一版
        不该刚换好就被下一条挤掉。流式生长（前缀延长）不算换版；
        每条最多两次（首次上屏 + 换说法落地），防长流把条钉死在屏上。"""
        if getattr(self, "_hold_from", 0.0) <= 0:
            return
        if getattr(self, "_hold_resets", 0) >= 2:
            return
        prev = getattr(self, "_hold_touch_text", "")
        if prev and (shown == prev or shown.startswith(prev) or prev.startswith(shown)):
            return
        self._hold_touch_text = shown
        self._hold_resets = getattr(self, "_hold_resets", 0) + 1
        self._hold_from = time.monotonic()

    def _prefetch_lookahead(self, text: str):
        if not getattr(self.translator, "_llm_cfg", None) or not draft_worth_llm(text):
            return
        if getattr(self, "_lookahead_src", "") == text and (
            getattr(self, "_lookahead_inflight", False) or getattr(self, "_lookahead_llm", "")
        ):
            return
        self._lookahead_src = text
        self._lookahead_llm = ""
        self._lookahead_gen = getattr(self, "_lookahead_gen", 0) + 1
        gen = self._lookahead_gen
        self._lookahead_inflight = True
        ctx = list(getattr(self, "_llm_context", []) or [])

        def work():
            try:
                llm = self.translator._try_llm_final(
                    text,
                    context=ctx,
                    should_abort=lambda: gen != getattr(self, "_lookahead_gen", -1),
                    stream=True,
                )
                if llm and gen == getattr(self, "_lookahead_gen", -1):
                    self._lookahead_llm = llm
            finally:
                if gen == getattr(self, "_lookahead_gen", -1):
                    self._lookahead_inflight = False

        threading.Thread(target=work, daemon=True).start()

    def wait_llm(self, timeout: float = 3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not (
                getattr(self, "_llm_revise_inflight", False)
                or getattr(self, "_llm_prefetch_inflight", False)
                or getattr(self, "_lookahead_inflight", False)
            ):
                return
            time.sleep(0.05)

    def _on_new_bar_if_needed(self):
        if getattr(self, "_need_new_bar", True):
            self._bump_bar()
            self._need_new_bar = False
            self._llm_prefetch = None
            self._pending_final_orig = ""
            self._pending_final_applied = False
            self._bar_draft_text = ""
            self._shown_trans = ""
        self._llm_draft_src = ""
        self._llm_draft_trans = ""
        self._llm_draft_at = 0.0

    def _bump_bar(self) -> int:
        lock = getattr(self, "_bar_lock", None)
        if lock is None:
            self._bar_lock = threading.Lock()
            self._bar_seq = 0
            lock = self._bar_lock
        with lock:
            self._bar_seq += 1
            self._llm_epoch = getattr(self, "_llm_epoch", 0) + 1
            self._hold_resets = 0
            self._hold_touch_text = ""
            self._bar_revise_launched = None
            return self._bar_seq

    def current_bar_seq(self) -> int:
        """缝事件附带的字幕条号：同一条草稿/定稿同号，切条递增。
        供探针与 A/B 脚本按条统计，壳不依赖它判条。"""
        return int(getattr(self, "_bar_seq", 0))

    def _next_llm_epoch(self) -> int:
        lock = getattr(self, "_bar_lock", None)
        if lock is None:
            self._llm_epoch = getattr(self, "_llm_epoch", 0) + 1
            return self._llm_epoch
        with lock:
            self._llm_epoch = getattr(self, "_llm_epoch", 0) + 1
            return self._llm_epoch

    def _llm_alive(self, seq: int, epoch: int) -> bool:
        return getattr(self, "_bar_seq", -1) == seq and getattr(self, "_llm_epoch", -1) == epoch

    def _remember_llm_context(self, orig: str, trans: str):
        if not orig or not trans:
            return
        ctx = getattr(self, "_llm_context", None)
        if ctx is None:
            self._llm_context = []
            ctx = self._llm_context
        if ctx and ctx[-1][0] == orig:
            ctx[-1] = (orig, trans)
        else:
            ctx.append((orig, trans))
        if len(ctx) > 2:
            del ctx[:-2]

    def _apply_llm_if_current(self, seq: int, src: str, llm: str, on_final):
        pending = getattr(self, "_pending_final_orig", "")
        if not pending:
            return
        if src != pending and not (llm_prefetch_usable(src, pending) and llm_covers(src, pending)):
            return
        lock = self._bar_lock
        with lock:
            if getattr(self, "_bar_seq", -1) != seq:
                return
            # 最新一代的结果总能上屏；旧一代的只有这条定稿还什么都没上过才允许，
            # 否则先完成的旧译文会盖掉已经改好的新译文。
            alive = self._llm_alive(seq, getattr(self, "_llm_epoch", -1))
            if not alive and getattr(self, "_pending_final_applied", False):
                return
        on_final(pending, llm)
        with lock:
            self._pending_final_applied = True
        self._touch_hold_for_revise(llm)
        self._remember_llm_context(pending, llm)

    def _schedule_llm_revise(self, seq: int, text: str, on_final):
        if not getattr(self.translator, "_llm_cfg", None):
            return
        # 改写是确定要做的，预取只是预测：两条各占各的槽，改写绝不等预取。
        # 改写起跑时的代数提升会作废并发中的预取（定稿文本已定，预测无意义）。
        with self._bar_lock:
            revise_busy = getattr(self, "_llm_revise_inflight", False)
            if revise_busy:
                # 只留最新一条——被顶掉的说明那条字幕条已经没机会再上屏了。
                self._llm_revise_pending = (seq, text, on_final)
        if not revise_busy:
            self._launch_llm_revise(seq, text, on_final)

    def _drain_llm_pending(self):
        pending = getattr(self, "_llm_revise_pending", None)
        if not pending:
            return
        self._llm_revise_pending = None
        seq, text, on_final = pending
        if getattr(self, "_bar_seq", -1) != seq:
            return  # 字幕条已被替换，晚到的改写无处上屏
        with self._bar_lock:
            if self._llm_revise_inflight:
                self._llm_revise_pending = (seq, text, on_final)
                return
        self._launch_llm_revise(seq, text, on_final)

    def _launch_llm_revise(self, seq: int, text: str, on_final):
        epoch = self._next_llm_epoch()  # 作废并发中的预取
        ctx = list(getattr(self, "_llm_context", []) or [])
        with self._bar_lock:
            self._llm_revise_inflight = True

        def work():
            try:
                def on_partial(got: str):
                    if not self._llm_alive(seq, epoch):
                        return
                    if got:
                        with self._bar_lock:
                            if getattr(self, "_bar_seq", -1) == seq:
                                # 半截也入存货：提前发出的改写在定稿前落地时，
                                # 定稿一出生就能直接用，不用再等完整结果。
                                self._llm_prefetch = (seq, text, got)
                    if getattr(self, "_pending_final_orig", ""):
                        first = not getattr(self, "_pending_final_applied", False)
                        on_final(text, got)
                        self._pending_final_applied = True
                        if first:
                            self._touch_hold_for_revise(got)

                llm = self.translator._try_llm_final(
                    text,
                    context=ctx,
                    on_partial=on_partial,
                    should_abort=lambda: not self._llm_alive(seq, epoch),
                    stream=True,
                )
                if not llm or not self._llm_alive(seq, epoch):
                    return
                with self._bar_lock:
                    if getattr(self, "_bar_seq", -1) == seq:
                        self._llm_prefetch = (seq, text, llm)
                self._apply_llm_if_current(seq, text, llm, on_final)
            finally:
                with self._bar_lock:
                    self._llm_revise_inflight = False
                self._drain_llm_pending()

        threading.Thread(target=work, daemon=True).start()

    def _apply_llm_draft(self, seq: int, text: str, got: str, on_draft):
        """草稿期纠正（B 层）：草稿还在长的时候，就用流式译文把草稿条上的 CT2 译文
        换成 LLM 版。只在流式文本仍是当前草稿的前缀时应用——ASR 改开头（Okay→O）
        时前缀断掉，自然跳过，等下一次请求。"""
        if not got or not _llm_partial_ready(got):
            return
        lock = self._bar_lock
        with lock:
            if getattr(self, "_bar_seq", -1) != seq:
                return
            cur = getattr(self, "_bar_draft_text", "")
            if not cur or (text != cur and not llm_prefetch_usable(text, cur)):
                return
            # 覆盖率门槛：碎片译文（如「我们确实如此。」只盖住句子前四成）
            # 不许替换整句 CT2，那比糙但完整的译文更误导观众。
            if not llm_covers(text, cur):
                return
            last = getattr(self, "_llm_draft_trans", "")
            if len(got) <= len(last):
                return  # 只前进不回退，防译文来回跳
            now = time.monotonic()
            if now - getattr(self, "_llm_draft_at", 0.0) < 0.3:
                return
            self._llm_draft_trans = got
            self._llm_draft_src = text
            self._llm_draft_at = now
            emit = cur
        on_draft(emit, got)

    def _maybe_early_revise(self, seq: int, text: str, on_final):
        """句末已现、换气确认前的空转窗里先把整句改写发出去（每条一次）。
        切条要等 ≥250ms 换气才确认，这段空转正好吃掉 LLM 首字；提前发 +
        结果入存货（_launch_llm_revise），定稿一出生就是完整 LLM 译文，
        观众不再先看半句预取的硬译再等纠正。请求数不变：预取+改写各一次。"""
        if not getattr(self.translator, "_llm_cfg", None):
            return
        if getattr(self, "_bar_revise_launched", None) == seq:
            return
        if not text or text[-1] not in "。？！.?!，、,;；":
            return
        if getattr(self, "_llm_prefetch_inflight", False):
            return  # 不杀在飞请求：等它落地，下一轮草稿再提前
        stored = getattr(self, "_llm_prefetch", None)
        if (
            stored
            and stored[0] == seq
            and llm_prefetch_usable(stored[1], text)
            and llm_covers(stored[1], text)
        ):
            return  # 存货已盖住整句：定稿会直接用，不必再发
        self._bar_revise_launched = seq
        self._schedule_llm_revise(seq, text, on_final)

    def _prefetch_llm(self, seq: int, text: str, on_final=None, on_draft=None):
        if not getattr(self.translator, "_llm_cfg", None) or not text:
            return
        prev = getattr(self, "_llm_prefetch", None)
        if prev and prev[0] == seq and prev[1] == text:
            return
        # 在途请求不杀、也不重叠发：草稿间隔（~700ms）小于模型首字（~1.2s），
        # 逢新草稿就重发只会永远杀掉跑不完的请求。等它落地（前缀仍可用），
        # 空下来再为更长的草稿发新的。
        with self._bar_lock:
            if getattr(self, "_llm_prefetch_inflight", False):
                return
            # 每条只预取一次。翻倍重取实测会把请求量推回 ~2.3 req/s，
            # 端点一限流改写全部排队串行化，覆盖率反而从高掉到一半（真窗日志）。
            # 一次预取 + 定稿改写 ≈ 0.6 req/s，稳定在健康区。
            launched = getattr(self, "_llm_prefetch_launched", None)
            if launched and launched[0] == seq:
                return
            self._llm_prefetch_launched = (seq, 0)
            self._llm_prefetch_inflight = True
            # 不提代数：预取与在途改写并发时互不作废（改写才是权威请求）
            epoch = self._llm_epoch
        ctx = list(getattr(self, "_llm_context", []) or [])

        def work():
            try:
                def on_partial(got: str):
                    if on_draft is not None:
                        self._apply_llm_draft(seq, text, got, on_draft)
                    if on_final is not None:
                        self._apply_llm_if_current(seq, text, got, on_final)
                    if got:
                        # 半截译文也入存货：定稿等不到完整结果时，
                        # 出生至少能用半截 LLM，改写随后补全。
                        with self._bar_lock:
                            if getattr(self, "_bar_seq", -1) == seq:
                                self._llm_prefetch = (seq, text, got)

                llm = self.translator._try_llm_final(
                    text,
                    context=ctx,
                    on_partial=on_partial,
                    should_abort=lambda: not self._llm_alive(seq, epoch),
                    stream=True,
                )
                if not llm:
                    return
                with self._bar_lock:
                    # 定稿可能已经动过代数；只要还是这条字幕条，结果就值得存，
                    # 定稿时 _take_llm_prefetch 按前缀挑用。
                    if getattr(self, "_bar_seq", -1) != seq:
                        return
                    self._llm_prefetch = (seq, text, llm)
                if on_draft is not None:
                    # 完整结果回头刷一次草稿：partial 常落在两次草稿发射之间，
                    # 不补这一下草稿期就永远看不到完整版。
                    self._apply_llm_draft(seq, text, llm, on_draft)
                if on_final is not None:
                    self._apply_llm_if_current(seq, text, llm, on_final)
            finally:
                with self._bar_lock:
                    self._llm_prefetch_inflight = False
                self._drain_llm_pending()

        threading.Thread(target=work, daemon=True).start()

    def _take_llm_prefetch(self, seq: int, text: str) -> str:
        prev = getattr(self, "_llm_prefetch", None)
        if (
            prev
            and prev[0] == seq
            and prev[2]
            and llm_prefetch_usable(prev[1], text)
            and llm_covers(prev[1], text)
        ):
            return prev[2]
        la_src = getattr(self, "_lookahead_src", "")
        la = getattr(self, "_lookahead_llm", "")
        if (
            la
            and la_src
            and (la_src == text or (llm_prefetch_usable(la_src, text) and llm_covers(la_src, text)))
        ):
            return la
        return ""

    def _clear_inc(self):
        clear = getattr(self.translator, "clear_incremental", None)
        if clear:
            clear()

    def warmup_translation(self):
        """后台守护线程预热：目的是触发推理首跑初始化（内存池/线程池），
        不是翻译质量。CT2 对整句也便宜；ONNX 对每语言对只解码 2 个 token。
        句间给重活让位（busy 标志由 heavy_worker 维护），卡住也不影响主流程。"""
        names = sorted(set(self.translator.pairs) | set(getattr(self.translator, "ct2", {})))
        for pair in names:
            try:
                while getattr(self, "busy", False):
                    time.sleep(0.05)
                smoke = self.translator.SMOKE.get(pair, "hello")
                if pair in getattr(self.translator, "ct2", {}):
                    self.translator._translate_pair(pair, smoke, final=False)
                elif pair in self.translator.pairs:
                    self.translator._greedy(self.translator.pairs[pair], smoke, max_new_tokens=2)
            except Exception:
                return

    def process(self, pcm: np.ndarray, seg: Segmenter, now: float, on_draft, on_final):
        """一块 PCM 进，可能回调草稿 / 定稿。返回事件列表由回调发。"""
        data = np.concatenate([self.pcm_rest, pcm.astype(np.float32)])
        keep = len(data) % VAD_WINDOW
        self.pcm_rest = data[len(data) - keep :] if keep else np.zeros(0, dtype=np.float32)
        data = data[: len(data) - keep] if keep else data

        if data.size == 0:
            return
        probs = self.vad.probs(data)
        voiced = probs > 0.5
        for i, v in enumerate(voiced):
            chunk = data[i * VAD_WINDOW : (i + 1) * VAD_WINDOW]
            if v:
                seg.feed_speech(chunk, now)
        self._maybe_draft_final(seg, now, on_draft, on_final)

    def flush_silence(self, seg: Segmenter, now: float, on_draft, on_final):
        """无声块也要推进切条（撤条 / 停顿切条判定靠时间）。"""
        self._maybe_draft_final(seg, now, on_draft, on_final)

    def _maybe_draft_final(self, seg, now, on_draft, on_final):
        if not seg.in_speech():
            return
        # 未确认的短语言切换遇到静音时，保留缓冲等后一句；没有后一句就静默丢掉，
        # 不把「変な？」这种误判作为定稿发给观众。
        if self.language_stability.waiting_without_new_voice(seg.buf.size):
            if self.language_stability.should_drop(seg.silence_ms(now)):
                seg.reset()
                self.language_stability.clear_pending()
                self.draft_policy.reset()
                self._clear_inc()
                self.empty_hits = 0
            return

        next_due = max(
            getattr(self, "_last_draft_at", -1e9) + DRAFT_INTERVAL_MS / 1000,
            getattr(self, "_last_draft_end", -1e9) + DRAFT_MIN_GAP_MS / 1000,
        )
        due_draft = now >= next_due
        possible_cut = seg.should_cut(seg.last_text, seg.last_trans, now)
        if not due_draft and not possible_cut:
            return

        self._last_draft_at = now
        # 复用：缓冲自上次识别后没有新增有声（静音期）→ 原文/草稿译文都不会变，
        # 定稿只补 beam，不重跑识别和贪心。
        if seg.last_text and seg.buf.size == getattr(self, "_decoded_samples", -1):
            text = seg.last_text
            trans = seg.last_trans
        else:
            text = self.recognizer.decode(seg.buf)
            self._decoded_samples = seg.buf.size
            self._last_draft_end = now
            if text:
                # sherpa 偶尔把 <|Speech|> 这类控制 token 留在文本里：
                # 先剥掉再进任何门，否则它会单独成条定稿发给观众
                text = _CONTROL_TAG_RE.sub("", text).strip()
            if text:
                text = normalize_cjk_numbers(text)
            if not text:
                if self.language_stability.should_drop(seg.silence_ms(now)):
                    seg.reset()
                    self.language_stability.clear_pending()
                    self.draft_policy.reset()
                    self._clear_inc()
                    self.empty_hits = 0
                elif possible_cut:
                    seg.take()
                    self.draft_policy.reset()
                    self._clear_inc()
                    self.empty_hits = 0
                elif seg.voiced_seconds() >= 1.0:
                    self.empty_hits += 1
                else:
                    # 开头缓冲还太短：快点重试；短缓冲的空识别不算「不是英日韩」的证据
                    self._last_draft_at = now - DRAFT_INTERVAL_MS / 1000 + DRAFT_EMPTY_RETRY_MS / 1000
                return

            if not self.language_stability.observe(text, seg.buf.size):
                if self.language_stability.should_drop(seg.silence_ms(now)):
                    seg.reset()
                    self.language_stability.clear_pending()
                    self.draft_policy.reset()
                    self._clear_inc()
                    self.empty_hits = 0
                return
            trans = None  # 惰性：句末/停顿/时长切条不需要草稿译文；本机译时定稿直接 beam

        pending = getattr(self, "_pending_final_orig", "")
        if (
            self._still_holding(now)
            and pending
            and text != pending
            and not llm_prefetch_usable(pending, text)
        ):
            self._prefetch_lookahead(text)
            return

        if not seg.should_cut_no_trans(text, now):
            if not min_content_met(text):
                seg.last_text, seg.last_trans = text, trans or ""
                if seg.voiced_seconds() >= IMMATURE_RETRY_MIN_VOICED_S:
                    self._last_draft_at = now - DRAFT_INTERVAL_MS / 1000 + DRAFT_EMPTY_RETRY_MS / 1000
                return
            if trans is None:
                if getattr(self, "llm_direct", False):
                    trans = ""
                else:
                    trans = self.translator.to_chinese(text)
                    self._last_draft_end = now
            seg.last_text, seg.last_trans = text, trans
            if not seg.should_cut(text, trans, now):
                self.empty_hits = 0
                draft = self.draft_policy.observe(text, trans)
                if draft is not None:
                    self._on_new_bar_if_needed()
                    with self._bar_lock:
                        self._bar_draft_text = text
                    shown = stable_trans(getattr(self, "_shown_trans", ""), trans)
                    self._shown_trans = shown
                    on_draft(draft[0], shown)
                    if draft_worth_llm(text):
                        self._prefetch_llm(getattr(self, "_bar_seq", 0), text, on_final, None)
                        self._maybe_early_revise(getattr(self, "_bar_seq", 0), text, on_final)
                return

        # 不成条的定稿不出给观众：停顿/时长兜底切出来的孤立标点、单词静默丢掉。
        if not min_content_met(text):
            seg.take()
            self.language_stability.commit_final(text)
            self.draft_policy.reset()
            self._clear_inc()
            self.empty_hits = 0
            self._decoded_samples = -1
            return
        # 定稿重译策略：CT2 快得起 beam 的机器上三语都整句重译（beam=2）；
        # 只有慢 ONNX 后端时，双跳语言（日/韩经英转中）beam 要跑两遍全量重算
        # （实测 median 2.5-4.2s），压不进「停顿后 1.5s 定稿」——退而冻结草稿。
        if getattr(self, "llm_direct", False):
            final_trans = trans or ""
        elif LanguageStability.language_of(text) in ("ja", "ko") and not self.translator.prefer_beam_finals():
            final_trans = trans if trans is not None else self.translator.to_chinese(text)
        else:
            final_trans = self.translator.to_chinese(text, final=True)
            self._last_draft_end = now
        if getattr(self, "_need_new_bar", True) or not getattr(self, "_bar_seq", 0):
            self._on_new_bar_if_needed()
        seq = getattr(self, "_bar_seq", 0)
        llm_ready = self._take_llm_prefetch(seq, text)
        if not llm_ready:
            # 冻结前进：草稿期已经亮过 LLM 译文，定稿就沿着 LLM 冻，不退回 CT2。
            # 否则观众看到 好→差→好 的闪动，而差的那版常是被下一条顶掉前最后看到的。
            # 覆盖率门槛同样适用：没盖住六成句子的碎片译文不配当定稿。
            with self._bar_lock:
                draft_llm = getattr(self, "_llm_draft_trans", "")
                draft_src = getattr(self, "_llm_draft_src", "")
            if (
                draft_llm
                and draft_src
                and len(draft_llm) >= 4
                and llm_prefetch_usable(draft_src, text)
                and llm_covers(draft_src, text)
            ):
                llm_ready = draft_llm
        if llm_ready:
            final_trans = llm_ready
        seq = seq or self._bump_bar()
        shown = final_trans or trans or ""
        self._pending_final_orig = text
        self._pending_final_applied = bool(llm_ready)
        with self._bar_lock:
            self._bar_draft_text = ""  # 已定稿：草稿期的流式纠正不再上屏
        on_final(text, shown)
        self._remember_llm_context(text, shown)
        stored = getattr(self, "_llm_prefetch", None)
        # 存货只是定稿的前缀（缺尾巴）也要立刻补一次整句改写；完全一致才不用
        if not llm_ready or not stored or stored[1] != text:
            self._schedule_llm_revise(seq, text, on_final)
        self._hold_from = now
        seg.take()
        self._need_new_bar = True
        self.language_stability.commit_final(text)
        self.draft_policy.reset()
        self._clear_inc()
        self.empty_hits = 0
        self._decoded_samples = -1
