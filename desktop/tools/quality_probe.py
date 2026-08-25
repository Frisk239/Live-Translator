"""无声三语质量探针：真实 MP3 → PCM → 真听译 → 分级延时 / 质量门报告。

它不碰扬声器、系统混音或 Windows 窗口，故可在观众不在场时复跑；覆盖的是
素材解码、16 kHz PCM 缝、VAD、SenseVoice、翻译、草稿门和切条。每条素材每次
运行都独占一个真引擎进程，避免慢模型尚未清空上一条 PCM 时把旧事件记到下一条。
屏幕绘制、Application Loopback 与媒体播放器进程选择仍须在实机上另验。

与旧版的语义差别（本刀返工要求）：
- 「基础链通过」（能出事件、原文命中、译文是中文）与「翻译质量通过」（语义
  锚点命中）分开判定，不再混在一个 pass 里。
- 延时一律从「开口」起算：先用本仓库同款 Silero VAD 离线定位素材里第一个人声
  窗口，再量 首草稿 / 首定稿 / 首显 的到达时刻减开口时刻。
- 记录每个定稿的「句末→定稿」（定稿到达减它之前最后一段有声）与「草稿→定稿
  修订」（定稿前是否有同条草稿、译文是否改写）。
- 每语种默认跑 3 次，报 P50/P95；样本 ≤3 时 P95 取最大值（保守），不拿单次
  负载波动当结论。

红线（C，报告里逐条给出 observed vs target）：
- 草稿首显（开口→首草稿）P50 ≤ 1000 ms、P95 ≤ 1500 ms；
- 每次运行的第一条字幕事件必须是草稿，不允许开口后直接定稿让观众干等；
- 自然停顿后定稿（句末→定稿，停顿 ≥ 500 ms 的定稿）P50 ≤ 1500 ms；
- 不允许孤立标题 / 少量词直接作为定稿（英 <2 词、日韩 <3 字判垃圾）；
- 原文高命中（ASR 过线）而译文语义锚点不达标 → 翻译质量红。

用法：
  python tools/quality_probe.py
  python tools/quality_probe.py --only en --runs 1
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
MATERIALS_DIR = ROOT / "manual-test-materials"
MODELS_DEFAULT = Path(os.environ.get("APPDATA", "")) / "com.livetranslator.desktop" / "models"
SAMPLE_RATE = 16_000
FRAME_SAMPLES = 1_600  # 100 ms，和壳侧 PCM 帧节奏一致
ASR_MATCH_FLOOR = {"en": 0.55, "ja": 0.25, "ko": 0.50}

# 红线目标（毫秒）。P95 在 ≤3 次运行时取最大值。
RED_LINE_FIRST_DRAFT_P50_MS = 1000
RED_LINE_FIRST_DRAFT_P95_MS = 1500
RED_LINE_PAUSE_TO_FINAL_P50_MS = 1500
RED_LINE_PAUSE_TRAILING_MS = 500  # 定稿前静音 ≥ 此值才算「自然停顿后定稿」
# 孤立标题 / 少量词定稿：英 <2 词、日韩中 <3 字（去标点空白）。
JUNK_FINAL_MIN_EN_WORDS = 2
JUNK_FINAL_MIN_CJK_CHARS = 3
SHORT_FINAL_EN_WORDS = 3  # 再宽一档的「偏短定稿」，只计数供审阅
SHORT_FINAL_CJK_CHARS = 6


@dataclass(frozen=True)
class Material:
    key: str  # 素材标识（探针输出/日志用）
    language: str  # 源语言：ASR 指标、文字体系判定都按它分派
    asr_floor: float
    filename: str
    expected_orig: str
    # 每一组是同义可接受的中文锚点；只作语义巡检，不强迫逐字相同。
    translation_anchor_groups: tuple[tuple[str, ...], ...]
    # 压力变体（噪声/变速）只验稳健性与延迟，不判翻译质量门
    anchor_gate: bool = True


MATERIALS = {
    "en": Material(
        "en",
        "en",
        ASR_MATCH_FLOOR["en"],
        "01-en-hpr-podcast.mp3",
        """Hello this is Huka coming to you on Hacker Public Radio
        I want to talk about a new search engine for Creative Commons content
        Hacker Public Radio is licensed under Creative Commons
        It is something called Openverse
        they have indexed 700 million creative works
        Sources include the Smithsonian NASA Flickr
        this Openverse is the latest incarnation of CC Search taken over by WordPress""",
        (("知识共享", "共享"), ("搜索",), ("照片", "图片")),
    ),
    "en2": Material(
        "en2",
        "en",
        0.40,
        "02-en-rubenerd-podcast.mp3",
        """We're overseas again for the first time in three years
        Recorded at the busy Kudanshita Tokyo Subway station
        wandering around near the Imperial Palace""",
        (("东京",), ("地铁", "车站"), ("海外", "三年")),
    ),
    "ko": Material(
        "ko",
        "ko",
        0.35,
        "03-ko-fsi-dialogue.mp3",
        """이것은 무엇입니까 책입니다 신문입니다
        이름이 뭐예요 한국 사람 미국 사람""",
        (("什么",), ("书",), ("韩国", "美国")),
    ),
}


# ---------- PCM / 文本工具 ----------

def resample(samples: np.ndarray, source_rate: int) -> np.ndarray:
    """与 tools/selftest.py 一致的线性重采样，保持探针输入与壳缝一致。"""
    if source_rate == SAMPLE_RATE or len(samples) == 0:
        return samples.astype(np.float32)
    ratio = SAMPLE_RATE / source_rate
    n_out = int(len(samples) * ratio)
    positions = np.arange(n_out) / ratio
    low = np.clip(positions.astype(np.int64), 0, len(samples) - 1)
    high = np.clip(low + 1, 0, len(samples) - 1)
    fraction = (positions - low).astype(np.float32)
    return (samples[low] * (1 - fraction) + samples[high] * fraction).astype(np.float32)


def load_pcm(path: Path) -> np.ndarray:
    import soundfile as sf

    samples, source_rate = sf.read(str(path), dtype="float32", always_2d=False)
    if samples.ndim > 1:
        samples = samples.mean(axis=1)
    return resample(np.asarray(samples, dtype=np.float32), int(source_rate))


def normalize_tokens(text: str, language: str) -> list[str]:
    if language == "en":
        return re.findall(r"[a-z0-9]+(?:['’][a-z0-9]+)?", text.lower())
    # 对日/韩采用字符级指标，去掉空格、标点和引号；不能拿英语分词规则误伤它们。
    text = re.sub(r"[\s\u3000\u3001\u3002\uff0c\uff0e\uff01\uff1f,.!?、。！？「」『』（）()\[\]<>…]", "", text)
    return list(text)


def levenshtein(a: list[str], b: list[str]) -> int:
    if len(a) < len(b):
        a, b = b, a
    row = list(range(len(b) + 1))
    for i, left in enumerate(a, start=1):
        next_row = [i]
        for j, right in enumerate(b, start=1):
            next_row.append(min(next_row[-1] + 1, row[j] + 1, row[j - 1] + (left != right)))
        row = next_row
    return row[-1]


def accuracy(reference: str, actual: str, language: str) -> dict[str, Any]:
    wanted = normalize_tokens(reference, language)
    seen = normalize_tokens(actual, language)
    distance = levenshtein(wanted, seen)
    denominator = max(1, len(wanted))
    return {
        "unit": "word" if language == "en" else "character",
        "reference_units": len(wanted),
        "observed_units": len(seen),
        "edit_distance": distance,
        "accuracy": round(max(0.0, 1 - distance / denominator), 4),
    }


def source_script_leaks(text: str, language: str) -> list[str]:
    patterns = {
        "en": r"[\u3040-\u30ff\uac00-\ud7af]",
        "ja": r"[\uac00-\ud7af]",
        "ko": r"[\u3040-\u30ff]",
    }
    return sorted(set(re.findall(patterns[language], text)))


def en_words(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9]+(?:['’][A-Za-z0-9]+)?", text))


def cjk_chars(text: str) -> int:
    return len(re.findall(r"[\u3040-\u30ff\uac00-\ud7af\u4e00-\u9fff]", text))


def final_is_junk(orig: str, language: str) -> bool:
    if language == "en":
        return en_words(orig) < JUNK_FINAL_MIN_EN_WORDS
    return cjk_chars(orig) < JUNK_FINAL_MIN_CJK_CHARS


def final_is_short(orig: str, language: str) -> bool:
    if language == "en":
        return en_words(orig) < SHORT_FINAL_EN_WORDS
    return cjk_chars(orig) < SHORT_FINAL_CJK_CHARS


def percentile(values: list[float], p: float) -> float | None:
    """样本 ≤3 时 P95 直接取最大值（保守），P50 取中位数。"""
    if not values:
        return None
    ordered = sorted(values)
    if p >= 95 and len(ordered) <= 3:
        return ordered[-1]
    idx = (len(ordered) - 1) * p / 100
    lo = int(idx)
    hi = min(lo + 1, len(ordered) - 1)
    frac = idx - lo
    return ordered[lo] * (1 - frac) + ordered[hi] * frac


# ---------- 开口定位（与引擎同一只 Silero VAD） ----------

def load_engine_module():
    repo = ROOT.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    from listen import engine as module

    return module


class SpeechTimeline:
    """离线跑一遍素材的 VAD，给「开口」与「句末」提供素材侧时间轴。"""

    def __init__(self, pcm: np.ndarray, vad, window: int):
        self.voiced_times_ms: list[float] = []
        if vad is None:
            return
        probs = vad.probs(pcm)
        for i, prob in enumerate(probs):
            if float(prob) > 0.5:
                self.voiced_times_ms.append(i * window / SAMPLE_RATE * 1000)

    @property
    def onset_ms(self) -> float | None:
        return self.voiced_times_ms[0] if self.voiced_times_ms else None

    def last_voiced_before(self, at_ms: float) -> float | None:
        prior = [t for t in self.voiced_times_ms if t <= at_ms]
        return prior[-1] if prior else None


# ---------- 引擎子进程与事件记录 ----------

class EventRecorder:
    def __init__(self):
        self.active: dict[str, Any] | None = None

    async def read(self, ws) -> None:
        async for raw in ws:
            try:
                event = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue
            if self.active is None:
                continue
            self.active["events"].append(
                {
                    "at_ms": round((time.monotonic() - self.active["started_at"]) * 1000, 1),
                    **event,
                }
            )


async def start_engine(models_dir: Path):
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        str(ROOT / "engine" / "real_listen.py"),
        "--port",
        "0",
        "--models-dir",
        str(models_dir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stderr_lines: list[str] = []

    async def drain_stderr() -> None:
        assert proc.stderr is not None
        while line := await proc.stderr.readline():
            stderr_lines.append(line.decode(errors="replace").rstrip())

    stderr_task = asyncio.create_task(drain_stderr())
    assert proc.stdout is not None
    deadline = time.monotonic() + 120
    port: int | None = None
    loaded = False
    while time.monotonic() < deadline:
        try:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=max(0.1, deadline - time.monotonic()))
        except asyncio.TimeoutError:
            break
        if not line:
            break
        text_line = line.decode(errors="replace").strip()
        ready = re.match(r"READY\s+(\d+)", text_line)
        if ready:
            port = int(ready.group(1))
        if text_line == "LOADED":
            loaded = True
        if port is not None and loaded:
            break
    if port is None or not loaded:
        proc.kill()
        await proc.wait()
        stderr_task.cancel()
        raise RuntimeError("真听译 120 秒未 READY/LOADED（模型加载慢？）：" + "\n".join(stderr_lines[-10:]))

    async def drain_stdout() -> None:
        assert proc.stdout is not None
        while line := await proc.stdout.readline():
            pass

    asyncio.create_task(drain_stdout())
    return proc, port, stderr_task, stderr_lines


async def pump_realtime(ws, pcm: np.ndarray) -> tuple[float, float]:
    """以真实 100 ms 节奏灌入；返回起点和最后一帧实际发出的时刻。"""
    started_at = time.monotonic()
    for offset in range(0, len(pcm), FRAME_SAMPLES):
        frame = pcm[offset : offset + FRAME_SAMPLES]
        await ws.send(frame.astype("<f4", copy=False).tobytes())
        target = started_at + min(len(pcm), offset + FRAME_SAMPLES) / SAMPLE_RATE
        await asyncio.sleep(max(0.0, target - time.monotonic()))
    return started_at, time.monotonic()


def numbers_in(text: str, normalizer=None) -> set[str]:
    """文本里的数字集合（阿拉伯数字；中日文数词先经引擎同款规范化转数字）。"""
    if normalizer is not None:
        text = normalizer(text)
    return set(re.findall(r"\d+(?:\.\d+)?", text))


def coalesce_revised_finals(events: list[dict]) -> list[dict]:
    """同原文连续定稿视为一条改写：时刻取首条（门禁用），译文取末条（质量用）。"""
    out: list[dict] = []
    for event in events:
        if (
            event.get("type") == "final"
            and out
            and out[-1].get("type") == "final"
            and out[-1].get("orig") == event.get("orig")
        ):
            merged = dict(event)
            merged["at_ms"] = out[-1].get("at_ms")
            out[-1] = merged
        else:
            out.append(event)
    return out


def summarize_run(events: list[dict], material: Material, timeline: SpeechTimeline, duration_s: float, normalizer=None) -> dict[str, Any]:
    metrics_events = coalesce_revised_finals(events)
    drafts = [event for event in metrics_events if event.get("type") == "draft"]
    finals = [event for event in metrics_events if event.get("type") == "final"]
    notices = [event for event in events if event.get("type") == "notice"]
    observed_orig = " ".join(str(event.get("orig", "")) for event in finals)
    observed_trans = "".join(str(event.get("trans", "")) for event in finals)

    first_visible = min((float(event["at_ms"]) for event in drafts + finals), default=None)
    first_draft = min((float(event["at_ms"]) for event in drafts), default=None)
    first_final = min((float(event["at_ms"]) for event in finals), default=None)
    last_final = max((float(event["at_ms"]) for event in finals), default=None)
    onset = timeline.onset_ms

    def from_onset(value: float | None) -> float | None:
        if value is None or onset is None:
            return None
        return round(value - onset, 1)

    # 按事件顺序单遍回放：定稿前最近的一条字幕事件是草稿 → preceded_by_draft，
    # 且比较两版译文是否改写（草稿→定稿修订）。
    per_final = []
    last_subtitle_kind = None
    last_draft_trans: str | None = None
    for event in (e for e in metrics_events if e.get("type") in ("draft", "final")):
        if event["type"] == "draft":
            last_subtitle_kind = "draft"
            last_draft_trans = event.get("trans", "")
            continue
        at = float(event["at_ms"])
        last_voiced = timeline.last_voiced_before(at)
        trailing = None if last_voiced is None else round(at - last_voiced, 1)
        preceded_by_draft = last_subtitle_kind == "draft"
        orig_text = str(event.get("orig", ""))
        trans_text = str(event.get("trans", ""))
        # 数字一致性（memoQ/Trados 式 QA）：原文的数字必须出现在译文里。
        # 压力变体放宽到两位数以上：白噪下 ASR 常把英文单词数字写成 "2 or three"，
        # 译文的「两三」匹配不上，属可接受降级；1536 这类关键数字仍严查。
        missing = numbers_in(orig_text, normalizer) - numbers_in(trans_text, normalizer)
        if not material.anchor_gate:
            missing = {n for n in missing if len(n) >= 2}
        missing_numbers = sorted(missing)
        per_final.append(
            {
                "at_ms": at,
                "orig": orig_text,
                "trans": trans_text,
                "trailing_silence_ms": trailing,
                "is_pause_final": bool(trailing is not None and trailing >= RED_LINE_PAUSE_TRAILING_MS),
                "preceded_by_draft": preceded_by_draft,
                "draft_trans_revised": None if not preceded_by_draft else bool(last_draft_trans != event.get("trans", "")),
                "missing_numbers": missing_numbers,
            }
        )
        last_subtitle_kind = "final"
        last_draft_trans = None

    anchor_hits = [any(option in observed_trans for option in group) for group in material.translation_anchor_groups]
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", observed_trans))
    asr_score = accuracy(material.expected_orig, observed_orig, material.language)
    pause_finals = [item for item in per_final if item["is_pause_final"]]
    return {
        "timing": {
            "media_duration_ms": round(duration_s * 1000, 1),
            "speech_onset_ms": onset,
            "first_visible_ms": first_visible,
            "first_draft_ms": first_draft,
            "first_final_ms": first_final,
            "first_visible_from_onset_ms": from_onset(first_visible),
            "first_draft_from_onset_ms": from_onset(first_draft),
            "first_final_from_onset_ms": from_onset(first_final),
            "last_final_minus_media_end_ms": None if last_final is None else round(last_final - duration_s * 1000, 1),
            "first_event_kind": next((e.get("type") for e in events if e.get("type") in ("draft", "final")), None),
        },
        "counts": {
            "draft": len(drafts),
            "final": len(finals),
            "finals_without_draft": sum(1 for item in per_final if not item["preceded_by_draft"]),
            "finals_with_revised_draft_trans": sum(
                1 for item in per_final if item["draft_trans_revised"] is True
            ),
            "junk_finals": sum(1 for item in per_final if final_is_junk(item["orig"], material.language)),
            "short_finals": sum(
                1 for item in per_final if final_is_short(item["orig"], material.language) and not final_is_junk(item["orig"], material.language)
            ),
            "pause_finals": len(pause_finals),
            "number_mismatches": sum(1 for item in per_final if item["missing_numbers"]),
        },
        "pause_to_final_ms": [item["trailing_silence_ms"] for item in pause_finals],
        "per_final": per_final,
        "basic_chain": {
            "received_subtitle_event": bool(drafts or finals),
            "received_final": bool(finals),
            "final_translation_has_chinese": chinese_chars > 0,
            "source_script_leaks_in_translation": source_script_leaks(observed_trans, material.language),
            "asr_accuracy": asr_score,
            "asr_match_floor": material.asr_floor,
            "asr_match_pass": asr_score["accuracy"] >= material.asr_floor,
            "pass": bool(drafts or finals) and bool(finals) and chinese_chars > 0 and asr_score["accuracy"] >= material.asr_floor,
        },
        "translation_quality": {
            "translation_anchor_hits": anchor_hits,
            "translation_anchor_hit_count": sum(anchor_hits),
            "translation_anchor_required": -(-len(anchor_hits) * 2 // 3),  # ceil(2/3)
            "pass": sum(anchor_hits) >= -(-len(anchor_hits) * 2 // 3),
        },
        "final_orig": observed_orig,
        "final_trans": observed_trans,
        "notice_kinds": [event.get("kind") for event in notices],
    }


async def run_material(ws, recorder: EventRecorder, material: Material, timeline: SpeechTimeline, tail_seconds: float, settle_seconds: float, normalizer=None) -> dict[str, Any]:
    path = MATERIALS_DIR / material.filename
    pcm = await asyncio.get_running_loop().run_in_executor(None, load_pcm, path)
    duration_s = len(pcm) / SAMPLE_RATE
    await ws.send(json.dumps({"type": "start", "source": f"quality-probe-{material.key}.exe"}))
    # 保留一个很短的控制帧时间，避免第一个 PCM 在 start 尚未处理时被忽略。
    await asyncio.sleep(0.08)
    current: dict[str, Any] = {"events": [], "started_at": time.monotonic()}
    recorder.active = current
    media_started_at, media_sent_at = await pump_realtime(ws, pcm)

    silence = np.zeros(FRAME_SAMPLES, dtype="<f4").tobytes()
    tail_frames = round(tail_seconds * SAMPLE_RATE / FRAME_SAMPLES)
    for _ in range(tail_frames):
        await ws.send(silence)
        await asyncio.sleep(FRAME_SAMPLES / SAMPLE_RATE)
    await asyncio.sleep(settle_seconds)
    recorder.active = None
    await ws.send(json.dumps({"type": "stop"}))
    await asyncio.sleep(0.15)

    summary = summarize_run(current["events"], material, timeline, duration_s, normalizer)
    summary["timing"]["media_send_elapsed_ms"] = round((media_sent_at - media_started_at) * 1000, 1)
    summary["events"] = current["events"]
    return summary


async def stop_engine(proc, stderr_task) -> None:
    if proc.returncode is None:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
    stderr_task.cancel()
    try:
        await stderr_task
    except asyncio.CancelledError:
        pass


def aggregate_stage_timing(material: Material, pcm: np.ndarray, sample_origs: list[str], engine_module, models_dir: Path) -> dict[str, Any]:
    """离线分别计时 VAD / 识别 / 草稿翻译 / 定稿翻译，用于区分延时根因。"""
    result: dict[str, Any] = {}
    vad = engine_module.Vad(models_dir)

    t0 = time.perf_counter()
    window = int(engine_module.VAD_WINDOW)
    for i in range(0, len(pcm) - window + 1, window):
        vad.probs(pcm[i : i + window])
    result["vad_full_pass_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    result["vad_per_100ms_ms"] = round(result["vad_full_pass_ms"] / (len(pcm) / SAMPLE_RATE * 10), 2)

    recognizer = engine_module.Recognizer(models_dir)
    onset = 0
    asr_costs = {}
    for seconds in (1, 2, 4, 6):
        chunk = pcm[onset : onset + seconds * SAMPLE_RATE]
        if len(chunk) < SAMPLE_RATE // 2:
            continue
        t0 = time.perf_counter()
        recognizer.decode(chunk)
        asr_costs[f"{seconds}s"] = round((time.perf_counter() - t0) * 1000, 1)
    result["asr_decode_ms_by_buffer"] = asr_costs

    translator = engine_module.Translator(models_dir)
    greedy_costs, beam_costs = [], []
    for text in sample_origs[:6]:
        if not text.strip():
            continue
        t0 = time.perf_counter()
        translator.to_chinese(text)
        greedy_costs.append((time.perf_counter() - t0) * 1000)
        t0 = time.perf_counter()
        translator.to_chinese(text, final=True)
        beam_costs.append((time.perf_counter() - t0) * 1000)
    result["draft_translate_ms_median"] = round(statistics.median(greedy_costs), 1) if greedy_costs else None
    result["final_translate_ms_median"] = round(statistics.median(beam_costs), 1) if beam_costs else None
    result["final_translate_ms_max"] = round(max(beam_costs), 1) if beam_costs else None
    return result


async def run_isolated_material(material: Material, timeline: SpeechTimeline, args, run_index: int, normalizer=None) -> dict[str, Any]:
    """单次运行独占一个引擎，测量不受前一条解码尾队列污染。"""
    import websockets

    proc, port, stderr_task, stderr_lines = await start_engine(args.models_dir)
    print(f"[{material.key}#{run_index + 1}] 真听译 READY @ {port}（模型已 LOADED）", flush=True)
    try:
        await asyncio.sleep(args.warmup_seconds)
        recorder = EventRecorder()
        async with websockets.connect(
            f"ws://127.0.0.1:{port}", max_size=2**22, ping_interval=30, ping_timeout=60
        ) as ws:
            reader = asyncio.create_task(recorder.read(ws))
            try:
                print(f"[{material.key}#{run_index + 1}] 真实 PCM 回放：{material.filename}", flush=True)
                report = await run_material(ws, recorder, material, timeline, args.tail_seconds, args.settle_seconds, normalizer)
            finally:
                reader.cancel()
                try:
                    await reader
                except asyncio.CancelledError:
                    pass
        return report
    finally:
        await stop_engine(proc, stderr_task)


def language_gates(runs: list[dict[str, Any]], material: Material) -> dict[str, Any]:
    first_drafts = [r["timing"]["first_draft_from_onset_ms"] for r in runs if r["timing"]["first_draft_from_onset_ms"] is not None]
    first_visible = [r["timing"]["first_visible_from_onset_ms"] for r in runs if r["timing"]["first_visible_from_onset_ms"] is not None]
    pause_values = [v for r in runs for v in r["pause_to_final_ms"] if v is not None]
    totals = {
        "final": sum(r["counts"]["final"] for r in runs),
        "finals_without_draft": sum(r["counts"]["finals_without_draft"] for r in runs),
        "junk_finals": sum(r["counts"]["junk_finals"] for r in runs),
        "short_finals": sum(r["counts"]["short_finals"] for r in runs),
        "number_mismatches": sum(r["counts"].get("number_mismatches", 0) for r in runs),
        "draft": sum(r["counts"]["draft"] for r in runs),
    }
    first_event_all_draft = all(r["timing"]["first_event_kind"] == "draft" for r in runs)
    anchor_counts = [r["translation_quality"]["translation_anchor_hit_count"] for r in runs]
    asr_medians = [r["basic_chain"]["asr_accuracy"]["accuracy"] for r in runs]

    aggregate = {
        "first_draft_from_onset_ms": {"p50": percentile(first_drafts, 50), "p95": percentile(first_drafts, 95), "runs": len(first_drafts)},
        "first_visible_from_onset_ms": {"p50": percentile(first_visible, 50), "p95": percentile(first_visible, 95), "runs": len(first_visible)},
        "pause_to_final_ms": {"p50": percentile(pause_values, 50), "p95": percentile(pause_values, 95), "samples": len(pause_values)},
        "finals_without_draft_ratio": round(totals["finals_without_draft"] / totals["final"], 3) if totals["final"] else None,
        "asr_accuracy_median": round(statistics.median(asr_medians), 4) if asr_medians else None,
        "translation_anchor_hit_median": statistics.median(anchor_counts) if anchor_counts else None,
    }

    def line(target: Any, observed: Any, passed: bool) -> dict[str, Any]:
        return {"target": target, "observed": observed, "pass": passed}

    if material.anchor_gate:
        red_lines = {
            "first_draft_p50_le_1000ms": line(f"≤{RED_LINE_FIRST_DRAFT_P50_MS}ms", aggregate["first_draft_from_onset_ms"]["p50"], aggregate["first_draft_from_onset_ms"]["p50"] is not None and aggregate["first_draft_from_onset_ms"]["p50"] <= RED_LINE_FIRST_DRAFT_P50_MS),
            "first_draft_p95_le_1500ms": line(f"≤{RED_LINE_FIRST_DRAFT_P95_MS}ms", aggregate["first_draft_from_onset_ms"]["p95"], aggregate["first_draft_from_onset_ms"]["p95"] is not None and aggregate["first_draft_from_onset_ms"]["p95"] <= RED_LINE_FIRST_DRAFT_P95_MS),
            "first_subtitle_event_is_draft": line("每次运行首条事件为草稿", first_event_all_draft, first_event_all_draft),
            "pause_to_final_p50_le_1500ms": line(f"≤{RED_LINE_PAUSE_TO_FINAL_P50_MS}ms", aggregate["pause_to_final_ms"]["p50"], aggregate["pause_to_final_ms"]["p50"] is not None and aggregate["pause_to_final_ms"]["p50"] <= RED_LINE_PAUSE_TO_FINAL_P50_MS),
            "no_junk_finals": line("孤立少量词定稿 = 0", totals["junk_finals"], totals["junk_finals"] == 0),
            "no_number_mismatches": line("原文数字未进译文 = 0", totals["number_mismatches"], totals["number_mismatches"] == 0),
        }
    else:
        # 压力变体（噪声/变速）只验「降级但不崩」：事件照流、无垃圾条、数字不丢。
        # 延迟照报不判门——白噪下 VAD 常判有声、停顿消失属预期降级。
        red_lines = {
            "drafts_still_flow": line("每次运行都有草稿", all(r["counts"]["draft"] > 0 for r in runs), all(r["counts"]["draft"] > 0 for r in runs)),
            "finals_still_flow": line("每次运行都有定稿", all(r["counts"]["final"] > 0 for r in runs), all(r["counts"]["final"] > 0 for r in runs)),
            "no_junk_finals": line("孤立少量词定稿 = 0", totals["junk_finals"], totals["junk_finals"] == 0),
            "no_number_mismatches": line("原文数字未进译文 = 0", totals["number_mismatches"], totals["number_mismatches"] == 0),
        }
    basic_chain_pass = all(r["basic_chain"]["pass"] for r in runs)
    translation_quality_pass = (not material.anchor_gate) or all(
        count >= r["translation_quality"]["translation_anchor_required"] for r, count in zip(runs, anchor_counts)
    )
    red_lines_pass = all(item["pass"] for item in red_lines.values())
    return {
        "totals": totals,
        "aggregate": aggregate,
        "basic_chain_pass": basic_chain_pass,
        "translation_quality_pass": translation_quality_pass,
        "red_lines": red_lines,
        "red_lines_pass": red_lines_pass,
        "overall_pass": basic_chain_pass and translation_quality_pass and red_lines_pass,
    }


def _burn_cpu():
    while True:
        pass


def start_cpu_load(cores: int):
    """观众机器上 CPU 被抢是常态：起 cores 个满转子进程模拟竞争负载，结束即杀。"""
    import multiprocessing as mp

    if cores <= 0:
        return []
    procs = []
    for _ in range(cores):
        proc = mp.Process(target=_burn_cpu, daemon=True)
        proc.start()
        procs.append(proc)
    return procs


def stop_cpu_load(procs) -> None:
    for proc in procs:
        proc.terminate()
    for proc in procs:
        proc.join(timeout=3)


async def async_main(args) -> int:
    keys = [key.strip() for key in args.only.split(",") if key.strip()]
    unknown = [key for key in keys if key not in MATERIALS]
    if unknown:
        raise ValueError(f"未知语种：{', '.join(unknown)}；可选 en,ja,ko")
    missing = [str(MATERIALS_DIR / MATERIALS[key].filename) for key in keys if not (MATERIALS_DIR / MATERIALS[key].filename).is_file()]
    if missing:
        raise FileNotFoundError("缺少测试素材：\n" + "\n".join(missing))
    if not args.models_dir.is_dir():
        raise FileNotFoundError(f"模型目录不存在：{args.models_dir}")

    load_procs = start_cpu_load(args.load_cores)
    try:
        return await _run_probe(args)
    finally:
        stop_cpu_load(load_procs)


async def _run_probe(args) -> int:
    keys = [key.strip() for key in args.only.split(",") if key.strip()]
    unknown = [key for key in keys if key not in MATERIALS]
    if unknown:
        raise ValueError(f"未知语种：{', '.join(unknown)}；可选 en,ja,ko")
    missing = [str(MATERIALS_DIR / MATERIALS[key].filename) for key in keys if not (MATERIALS_DIR / MATERIALS[key].filename).is_file()]
    if missing:
        raise FileNotFoundError("缺少测试素材：\n" + "\n".join(missing))

    # 引擎模块永远要加载：开口定位（VAD 时间轴）是所有延时指标的基准；
    # --skip-stage-timing 只跳过离线的 ASR/翻译分级计时。
    engine_module = load_engine_module()
    llm_cfg = getattr(engine_module, "load_llm_config", lambda: None)()
    if llm_cfg:
        wait = float(llm_cfg.get("timeout_s") or 8) + 2
        if args.settle_seconds < wait:
            args.settle_seconds = wait

    output: dict[str, Any] = {
        "generated_at_epoch_ms": round(time.time() * 1000),
        "method": "真实 MP3 以 100ms PCM 实时送入本机真听译；每语种独立引擎多次运行，延时从离线 VAD 定位的开口起算。",
        "method_notes": [
            "样本 ≤3 次时 P95 取最大值（保守）。",
            "「基础链通过」= 有事件、有定稿、译文含中文、ASR 命中过线；不代表译文质量。",
            "「翻译质量通过」= 语义锚点命中 ≥ 三分之二组。",
            "「句末→定稿」按定稿到达时刻减素材侧最后有声窗计算；引擎滞后时该值只会更大，不会偏乐观。",
        ],
        "coverage": ["MP3 解码", "PCM 缝", "VAD", "SenseVoice", "翻译", "草稿门", "切条"],
        "not_covered": ["媒体播放器进程采音", "系统混音", "Tauri 事件投递", "字幕窗像素绘制"],
        "red_line_targets": {
            "first_draft_p50_ms": RED_LINE_FIRST_DRAFT_P50_MS,
            "first_draft_p95_ms": RED_LINE_FIRST_DRAFT_P95_MS,
            "pause_to_final_p50_ms": RED_LINE_PAUSE_TO_FINAL_P50_MS,
            "junk_finals": 0,
        },
        "runs_per_language": args.runs,
        "cpu_load_cores": args.load_cores,
        "models_dir": str(args.models_dir),
        "warmup_seconds": args.warmup_seconds,
        "languages": {},
    }

    overall = {"basic_chain": True, "translation_quality": True, "red_lines": True}
    for key in keys:
        material = MATERIALS[key]
        pcm = load_pcm(MATERIALS_DIR / material.filename)
        vad = engine_module.Vad(args.models_dir)
        timeline = SpeechTimeline(pcm, vad, int(engine_module.VAD_WINDOW))
        runs = []
        for run_index in range(args.runs):
            report = await run_isolated_material(material, timeline, args, run_index, normalizer=getattr(engine_module, "normalize_cjk_numbers", None))
            runs.append(report)
            timing = report["timing"]
            print(
                f"[{key}#{run_index + 1}] draft={report['counts']['draft']} final={report['counts']['final']} "
                f"首草稿(开口起)={timing['first_draft_from_onset_ms']}ms 首定稿(开口起)={timing['first_final_from_onset_ms']}ms "
                f"首事件={timing['first_event_kind']} ASR={report['basic_chain']['asr_accuracy']['accuracy']:.1%} "
                f"锚点={report['translation_quality']['translation_anchor_hit_count']}/{len(report['translation_quality']['translation_anchor_hits'])}",
                flush=True,
            )

        language_report: dict[str, Any] = {
            "language": key,
            "audio": str(MATERIALS_DIR / material.filename),
            "runs": runs,
        }
        if not args.skip_stage_timing:
            language_report["stage_timing"] = await asyncio.get_running_loop().run_in_executor(
                None,
                aggregate_stage_timing,
                material,
                pcm,
                [item["orig"] for run in runs for item in run["per_final"]],
                engine_module,
                args.models_dir,
            )
        language_report["gates"] = language_gates(runs, material)
        output["languages"][key] = language_report

        gates = language_report["gates"]
        overall["basic_chain"] &= gates["basic_chain_pass"]
        overall["translation_quality"] &= gates["translation_quality_pass"]
        overall["red_lines"] &= gates["red_lines_pass"]
        agg = gates["aggregate"]
        fd = agg["first_draft_from_onset_ms"]
        pf = agg["pause_to_final_ms"]
        print(
            f"[{key}] 汇总：首草稿P50={fd['p50']}ms P95={fd['p95']}ms | "
            f"句末→定稿P50={pf['p50']}ms P95={pf['p95']}ms ({pf['samples']}条) | "
            f"无草稿定稿={gates['totals']['finals_without_draft']}/{gates['totals']['final']} "
            f"垃圾定稿={gates['totals']['junk_finals']} | "
            f"基础链={'过' if gates['basic_chain_pass'] else '红'} "
            f"翻译质量={'过' if gates['translation_quality_pass'] else '红'} "
            f"红线={'过' if gates['red_lines_pass'] else '红'}",
            flush=True,
        )

    output["overall"] = {**overall, "all_pass": overall["basic_chain"] and overall["translation_quality"] and overall["red_lines"]}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告：{args.report}", flush=True)
    verdict = "基础链过 + 翻译质量过 + 红线过" if output["overall"]["all_pass"] else (
        f"基础链={'过' if overall['basic_chain'] else '红'}；翻译质量={'过' if overall['translation_quality'] else '红'}；红线={'过' if overall['red_lines'] else '红'}"
    )
    print("=== 总门禁：" + verdict + " ===", flush=True)
    return 0 if output["overall"]["all_pass"] else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", default="en,en2,ko", help="逗号分隔的素材标识，见 MATERIALS")
    parser.add_argument("--models-dir", type=Path, default=MODELS_DEFAULT)
    parser.add_argument("--runs", type=int, default=3, help="每语种独立运行次数（P50/P95 用）")
    parser.add_argument("--warmup-seconds", type=float, default=1.0, help="LOADED 之后的额外静置")
    parser.add_argument("--tail-seconds", type=float, default=3.0, help="原音频后灌入的静音，用于逼出句末定稿")
    parser.add_argument("--settle-seconds", type=float, default=1.0)
    parser.add_argument("--report", type=Path, default=MATERIALS_DIR / "quality-probe-report.json")
    parser.add_argument("--skip-stage-timing", action="store_true", help="跳过离线分级计时（省时）")
    parser.add_argument("--load-cores", type=int, default=0, help="模拟观众机器的 CPU 竞争：起 N 个满载子进程")
    args = parser.parse_args()
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
