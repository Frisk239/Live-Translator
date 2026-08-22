"""切条状态机与语言稳定性的测试。

纯逻辑用例不碰模型；最后一条集成回归用真实英语 PCM 复现自动语言判断。
pytest 跑：cd desktop && python -m pytest engine/tests -q
"""
import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "engine"))
import importlib.util

spec = importlib.util.spec_from_file_location("real_listen", Path(__file__).resolve().parents[2] / "engine" / "real_listen.py")
rl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rl)


def new_seg(now=0.0):
    s = rl.Segmenter()
    s.feed_speech(__import__("numpy").zeros(16000), now)
    return s


def test_sentence_end_cuts_when_mature():
    s = new_seg()
    # 词数够、时长够 → 句末切
    assert s.should_cut("he has got like three thousand HP.", "他有三千血。", 2.0) is True


def test_sentence_end_waits_for_breath():
    s = new_seg()
    s.seg_start = 0.0
    s.last_voice_at = 1.9
    # 句末标点成熟，但只停了 100ms：还在句中小憩，不切
    assert s.should_cut("he has got like three thousand HP.", "他有三千血。", 2.0) is False
    # 换气超过 250ms：句末切条成立
    assert s.should_cut("he has got like three thousand HP.", "他有三千血。", 2.3) is True


def test_short_prefix_does_not_cut_on_punct():
    s = new_seg()
    # 增量前缀 “So.”：不成熟，先不切
    assert s.should_cut("So.", "所以。", 0.3) is False


def test_pause_cuts():
    s = new_seg(now=0.0)
    # 成条文字（两词以上）+ 静音超过口气阈值 → 切
    assert s.should_cut("hello there", "你好", 0.0 + rl.PAUSE_CUT_MS / 1000 + 0.05) is True


def test_immature_text_waits_longer_before_pause_cut():
    s = new_seg(now=0.0)
    # 孤立少量词不成条：600ms 停顿先不切，等更久才切（不出标题式定稿）
    assert s.should_cut("Go.", "走。", 0.0 + rl.PAUSE_CUT_MS / 1000 + 0.05) is False
    assert s.should_cut("Go.", "走。", 0.0 + rl.IMMATURE_PAUSE_CUT_MS / 1000 + 0.05) is True


def test_no_cut_while_speaking():
    s = new_seg()
    s.last_voice_at = 1.0
    assert s.should_cut("so we are go", "所以我们", 1.1) is False


def test_hard_cap_trans_length():
    s = new_seg()
    s.last_voice_at = 0.0
    # 硬顶约一行半电影字幕，连说也切
    assert s.should_cut("blah", "一" * (rl.TRANS_MAX_CHARS + 1), 0.1) is True
    # 不到硬顶、又没有从句闭合：不切
    assert s.should_cut("blah blah", "一" * 16, 0.1) is False


def test_hard_cap_duration():
    s = new_seg()
    s.last_voice_at = rl.SEG_MAX_SECONDS  # 一直有声
    assert s.should_cut("blah blah", "嗯嗯", rl.SEG_MAX_SECONDS + 0.1) is True


def test_comma_cuts_only_when_long_enough():
    s = new_seg()
    s.seg_start = 0.0
    s.last_voice_at = 2.0
    short = "so,"  # 译文太短，逗号不切
    assert s.should_cut(short, "所以，", 2.0) is False
    assert s.should_cut(short, "所以我们要从这边绕过去，", 2.0) is True


def test_clause_closed_does_not_cut_on_bare_and():
    assert rl.clause_closed("so we went and") is False
    assert rl.clause_closed("所以我们走了，") is True
    assert rl.clause_closed("行ったけど") is True


def test_take_resets():
    import numpy as np

    s = new_seg()
    buf, text, _ = s.take()
    assert buf.size > 0 and s.buf.size == 0 and s.seg_start is None


def test_language_switch_holds_a_short_unconfirmed_candidate():
    gate = rl.LanguageStability()
    gate.stable_language = "en"
    short_voice = int(0.7 * rl.SAMPLE_RATE)

    # 英语会话里短短的「変な？」先不出给观众；静音重复 decode 也不能确认它。
    assert gate.observe("変な？", short_voice) is False
    assert gate.waiting_without_new_voice(short_voice) is True
    assert gate.should_drop(rl.LANG_SWITCH_DROP_SILENCE_MS - 1) is False
    assert gate.should_drop(rl.LANG_SWITCH_DROP_SILENCE_MS) is True


def test_language_classifier_keeps_real_multilingual_scripts_distinct():
    assert rl.LanguageStability.language_of("hello") == "en"
    assert rl.LanguageStability.language_of("你好") == "zh"
    assert rl.LanguageStability.language_of("こんにちは") == "ja"
    assert rl.LanguageStability.language_of("変な？") == "ja"  # 日文汉字 + 假名仍是日文
    assert rl.LanguageStability.language_of("안녕하세요") == "ko"


def test_language_switch_allows_a_confirmed_real_japanese_phrase():
    gate = rl.LanguageStability()
    gate.stable_language = "en"

    # 真实切到日语时，后续新增语音再次识别为日文即可确认，不会锁在英语。
    first = int(0.65 * rl.SAMPLE_RATE)
    second = int(0.85 * rl.SAMPLE_RATE)
    assert gate.observe("こんにちは", first) is False
    assert gate.observe("こんにちは世界", second) is True
    assert gate.stable_language == "ja"

    # 新会话没有英语预设；观众一开口就是日语时不需要先攒两次才显示。
    fresh = rl.LanguageStability()
    assert fresh.observe("はい", int(0.25 * rl.SAMPLE_RATE)) is True
    fresh.commit_final("はい")
    assert fresh.stable_language == "ja"


def test_engine_shows_each_displayable_draft_and_finals_with_beam_only():
    """草稿够最小成条量就亮、文本变了就刷新；句末定稿只走 beam，不重跑贪心。"""
    import numpy as np

    class FakeRecognizer:
        def __init__(self):
            self.outputs = iter([
                "I want to go",
                "I want to go to",
                "I want to go to the store.",
            ])

        def decode(self, _samples):
            return next(self.outputs)

    class FakeTranslator:
        def __init__(self):
            self.calls = []

        def to_chinese(self, text, final=False):
            self.calls.append((text, final))
            return "final" if final else "draft"

    engine = rl.Engine.__new__(rl.Engine)
    engine.recognizer = FakeRecognizer()
    engine.translator = FakeTranslator()
    engine.language_stability = rl.LanguageStability()
    engine.draft_policy = rl.DraftPolicy()
    engine.empty_hits = 0
    engine._last_draft_at = -1e9
    engine._last_draft_end = -1e9
    engine._decoded_samples = -1
    seg = rl.Segmenter()
    events = []

    seg.feed_speech(np.zeros(12_000, dtype=np.float32), 0.0)
    engine._maybe_draft_final(seg, 0.0, lambda o, t: events.append(("draft", o, t)), lambda o, t: events.append(("final", o, t)))
    seg.feed_speech(np.zeros(4_000, dtype=np.float32), 0.8)
    engine._maybe_draft_final(seg, 0.8, lambda o, t: events.append(("draft", o, t)), lambda o, t: events.append(("final", o, t)))
    seg.feed_speech(np.zeros(4_000, dtype=np.float32), 1.9)
    # 句末已出现但没换气：不切，继续当草稿长
    engine._maybe_draft_final(seg, 1.95, lambda o, t: events.append(("draft", o, t)), lambda o, t: events.append(("final", o, t)))
    # 换气 250ms 后句末切条成立 → 定稿
    engine._maybe_draft_final(seg, 2.3, lambda o, t: events.append(("draft", o, t)), lambda o, t: events.append(("final", o, t)))

    assert events == [
        ("draft", "I want to go", ""),
        ("draft", "I want to go to", ""),
        # 句子说完但还没换气：完整句先作为草稿亮出，不切成无上下文碎片
        ("draft", "I want to go to the store.", ""),
        ("final", "I want to go to the store.", "final"),
    ]
    # 定稿那轮复用识别与草稿译文，只补 beam
    assert engine.translator.calls == [
        ("I want to go", False),
        ("I want to go to", False),
        ("I want to go to the store.", False),
        ("I want to go to the store.", True),
    ]


def test_engine_reuses_last_recognition_when_buffer_has_not_grown():
    """静音期缓冲没长：定稿复用上一次识别与草稿译文，只补 beam。"""
    import numpy as np

    class FakeRecognizer:
        def __init__(self):
            self.decode_count = 0

        def decode(self, _samples):
            self.decode_count += 1
            return "I want to go now"

    class FakeTranslator:
        def __init__(self):
            self.calls = []

        def to_chinese(self, text, final=False):
            self.calls.append((text, final))
            return "final" if final else "draft"

    engine = rl.Engine.__new__(rl.Engine)
    engine.recognizer = FakeRecognizer()
    engine.translator = FakeTranslator()
    engine.language_stability = rl.LanguageStability()
    engine.draft_policy = rl.DraftPolicy()
    engine.empty_hits = 0
    engine._last_draft_at = -1e9
    engine._last_draft_end = -1e9
    engine._decoded_samples = -1
    seg = rl.Segmenter()
    events = []

    seg.feed_speech(np.zeros(16_000, dtype=np.float32), 0.0)
    engine._maybe_draft_final(seg, 0.0, lambda o, t: events.append(("draft", o, t)), lambda o, t: events.append(("final", o, t)))
    # 之后只进静音：口气停顿到点，走定稿路径
    engine._maybe_draft_final(seg, 0.0 + rl.PAUSE_CUT_MS / 1000 + 0.1, lambda o, t: events.append(("draft", o, t)), lambda o, t: events.append(("final", o, t)))

    assert [kind for kind, _o, _t in events] == ["draft", "final"]
    assert engine.recognizer.decode_count == 1
    assert engine.translator.calls == [
        ("I want to go now", False),
        ("I want to go now", True),
    ]


def test_immature_text_is_not_translated_and_retries_after_half_second():
    import numpy as np

    class FakeRecognizer:
        def decode(self, _samples):
            return "Concord"

    class FakeTranslator:
        def __init__(self):
            self.calls = []

        def to_chinese(self, text, final=False):
            self.calls.append(text)
            return "康科德"

    engine = rl.Engine.__new__(rl.Engine)
    engine.recognizer = FakeRecognizer()
    engine.translator = FakeTranslator()
    engine.language_stability = rl.LanguageStability()
    engine.draft_policy = rl.DraftPolicy()
    engine.empty_hits = 0
    engine._last_draft_at = -1e9
    engine._last_draft_end = -1e9
    engine._decoded_samples = -1
    seg = rl.Segmenter()
    events = []
    seg.feed_speech(np.zeros(int(0.6 * rl.SAMPLE_RATE), dtype=np.float32), 0.0)
    engine._maybe_draft_final(seg, 0.0, lambda o, t: events.append(o), lambda o, t: events.append(o))
    assert events == []
    assert engine.translator.calls == []
    assert engine._last_draft_at == 0.0 - rl.DRAFT_INTERVAL_MS / 1000 + rl.DRAFT_EMPTY_RETRY_MS / 1000


def test_stream_en_draft_rejects_glitch_and_short_function_words():
    assert rl.stream_en_draft_ok("Concord returned") is True
    assert rl.stream_en_draft_ok("the the") is False
    assert rl.stream_en_draft_ok("Concord") is False
    assert rl.stream_en_draft_ok("なんか？") is False
    assert rl.stream_en_draft_ok("제 대가로") is False


def test_min_content_ignores_ascii_punct_on_cjk_glitch():
    # 阶段2 暴露：ASCII 句号曾让「맞だ.」这种 2 字异语混过 3 字门
    assert rl.min_content_met("맞だ.") is False
    assert rl.min_content_met("寝よう？") is True
    assert rl.min_content_met("ごんは") is True


def test_draft_policy_waits_for_minimum_content_then_updates_on_change():
    policy = rl.DraftPolicy()
    assert policy.observe("So", "所以") is None  # 一个词不成条
    assert policy.observe("Go on", "继续") == ("Go on", "继续")
    assert policy.observe("Go on", "继续") is None  # 同一文本不重发
    assert policy.observe("Go on now", "现在继续") == ("Go on now", "现在继续")


def test_draft_policy_accepts_short_japanese_once_displayable():
    policy = rl.DraftPolicy()
    assert policy.observe("ごん", "小权") is None  # 两字不成条
    assert policy.observe("ごんは", "小权是") == ("ごんは", "小权是")


def test_number_word_normalization_is_conservative():
    # 成组数词 → 阿拉伯数字：数字不再被枢轴翻译算错（1536→1530 的教训）
    assert rl.normalize_cjk_numbers("천 오백 삼십 육 년에는") == "1536 년에는"
    assert rl.normalize_cjk_numbers("이 천 원") == "2000 원"
    # 快语速下 ASR 的 ITN 混排（ASCII 数字 + 数词）也要能解析
    assert rl.normalize_cjk_numbers("1오백3십6 년에는") == "1536 년에는"
    assert rl.normalize_cjk_numbers("2천3백4십5 원") == "2345 원"
    assert rl.normalize_cjk_numbers("십만 명이") == "100000 명이"
    assert rl.normalize_cjk_numbers("千五百三十六年") == "1536年"
    assert rl.normalize_cjk_numbers("一億二千万") == "120000000"
    # 孤立个位数词语义太泛（사=4/公司、일=1/工作），不动
    assert rl.normalize_cjk_numbers("사 오 년도") == "사 오 년도"
    assert rl.normalize_cjk_numbers("오늘 날씨가 좋다") == "오늘 날씨가 좋다"
    assert rl.normalize_cjk_numbers("三つの約束") == "三つの約束"


def test_translator_uses_fast_draft_and_small_beam_for_final():
    translator = rl.Translator.__new__(rl.Translator)
    translator.pairs = {"opus-en-zh": object()}
    calls = []
    translator._greedy = lambda pair, text: calls.append(("greedy", pair, text)) or "fast"
    translator._beam = lambda pair, text, width: calls.append(("beam", pair, text, width)) or "refined"

    assert translator.to_chinese("hello") == "fast"
    assert translator.to_chinese("hello", final=True) == "refined"
    assert calls == [
        ("greedy", translator.pairs["opus-en-zh"], "hello"),
        ("beam", translator.pairs["opus-en-zh"], "hello", rl.FINAL_BEAM_SIZE),
    ]


def test_llm_revises_final_in_background_and_skips_stale_bar():
    import time
    import numpy as np

    class FakeRecognizer:
        def decode(self, _samples):
            return "I want to go now"

    class FakeTranslator:
        def __init__(self):
            self._llm_cfg = {"on": True}
            self.calls = []

        def to_chinese(self, text, final=False):
            self.calls.append((text, final))
            return "final" if final else "draft"

        def prefer_beam_finals(self):
            return True

        def _try_llm_final(self, text, **_kwargs):
            time.sleep(0.08)
            return "LLM"

    engine = rl.Engine.__new__(rl.Engine)
    engine.recognizer = FakeRecognizer()
    engine.translator = FakeTranslator()
    engine.language_stability = rl.LanguageStability()
    engine.draft_policy = rl.DraftPolicy()
    engine.empty_hits = 0
    engine._last_draft_at = -1e9
    engine._last_draft_end = -1e9
    engine._decoded_samples = -1
    engine._bar_seq = 0
    engine._bar_lock = __import__("threading").Lock()
    engine._llm_prefetch_inflight = False
    engine._llm_revise_inflight = False
    engine._llm_prefetch_launched = None
    engine._llm_epoch = 0
    engine._llm_prefetch = None
    engine._llm_revise_pending = None
    engine._pending_final_orig = ""
    engine._pending_final_applied = False
    engine._bar_draft_text = ""
    engine._llm_draft_src = ""
    engine._llm_draft_trans = ""
    engine._llm_draft_at = 0.0
    engine._llm_context = []
    seg = rl.Segmenter()
    events = []
    seg.feed_speech(np.zeros(16_000, dtype=np.float32), 0.0)
    engine._maybe_draft_final(seg, 0.0, lambda o, t: events.append(("draft", t)), lambda o, t: events.append(("final", t)))
    engine._maybe_draft_final(
        seg,
        0.0 + rl.PAUSE_CUT_MS / 1000 + 0.1,
        lambda o, t: events.append(("draft", t)),
        lambda o, t: events.append(("final", t)),
    )
    assert events[0] == ("draft", "")
    assert events[1][0] == "final"
    time.sleep(0.25)
    assert events[-1] == ("final", "LLM")

    events.clear()
    engine._schedule_llm_revise(engine._bump_bar() - 1, "stale", lambda o, t: events.append(t))
    engine._bump_bar()
    time.sleep(0.2)
    assert events == []


def test_mask_tail_hides_last_two_cjk_or_words():
    assert rl.mask_tail("所以我们要试试这场战斗") == "所以我们要试试这场"
    assert rl.mask_tail("和谐返回") == "和谐"
    assert rl.mask_tail("好") == ""
    assert rl.mask_tail("hello world foo bar") == "hello world"
    assert rl.mask_tail("hello world") == ""


def test_stable_trans_appends_or_freezes():
    assert rl.stable_trans("", "所以我们要试试这场战斗") == "所以我们要试试这场"
    assert rl.stable_trans("所以我们要试试这场", "所以我们要试试这场战斗了啊") == "所以我们要试试这场战斗"
    assert rl.stable_trans("和谐", "协和号回到了它的位置") == "和谐"
    assert rl.stable_trans("和谐", "和谐返回了") == "和谐返"


def test_draft_worth_llm_waits_for_a_real_clause():
    assert rl.draft_worth_llm("So we.") is False
    assert rl.draft_worth_llm("So we are going") is True
    assert rl.draft_worth_llm("Okay let us regroup") is True
    assert rl.draft_worth_llm("ごんは小さいときに") is True


def _llm_engine(recognizer, translator):
    engine = rl.Engine.__new__(rl.Engine)
    engine.recognizer = recognizer
    engine.translator = translator
    engine.language_stability = rl.LanguageStability()
    engine.draft_policy = rl.DraftPolicy()
    engine.empty_hits = 0
    engine.llm_direct = False
    engine._last_draft_at = -1e9
    engine._last_draft_end = -1e9
    engine._decoded_samples = -1
    engine._bar_seq = 0
    engine._need_new_bar = True
    engine._bar_lock = __import__("threading").Lock()
    engine._llm_prefetch_inflight = False
    engine._llm_revise_inflight = False
    engine._llm_prefetch_launched = None
    engine._llm_epoch = 0
    engine._llm_prefetch = None
    engine._llm_revise_pending = None
    engine._pending_final_orig = ""
    engine._pending_final_applied = False
    engine._bar_draft_text = ""
    engine._llm_draft_src = ""
    engine._llm_draft_trans = ""
    engine._llm_draft_at = 0.0
    engine._llm_context = []
    engine._hold_from = 0.0
    engine._hold_resets = 0
    engine._hold_touch_text = ""
    engine._bar_revise_launched = None
    engine._lookahead_src = ""
    engine._lookahead_llm = ""
    engine._lookahead_gen = 0
    engine._lookahead_inflight = False
    return engine


def test_rewrite_prefetch_skips_short_draft():
    import numpy as np

    class Rec:
        def decode(self, _samples):
            return "So we."

    class Tr:
        def __init__(self):
            self._llm_cfg = {"on": True}
            self.llm_calls = 0

        def to_chinese(self, text, final=False):
            return "我们也是"

        def prefer_beam_finals(self):
            return True

        def _try_llm_final(self, text, **_kwargs):
            self.llm_calls += 1
            return "LLM"

    engine = _llm_engine(Rec(), Tr())
    seg = rl.Segmenter()
    seg.feed_speech(np.zeros(16_000, dtype=np.float32), 0.0)
    engine._maybe_draft_final(seg, 0.0, lambda o, t: None, lambda o, t: None)
    assert engine.translator.llm_calls == 0
    assert engine._llm_prefetch_launched is None


def test_final_hold_keeps_next_bar_off_screen():
    import numpy as np

    class Rec:
        def __init__(self):
            self.n = 0

        def decode(self, _samples):
            self.n += 1
            if self.n == 1:
                return "Okay let us regroup and try again."
            return "He has got like three thousand hit points."

    class Tr:
        def __init__(self):
            self._llm_cfg = None

        def to_chinese(self, text, final=False):
            return "CT2"

        def prefer_beam_finals(self):
            return True

    engine = _llm_engine(Rec(), Tr())
    seg = rl.Segmenter()
    events = []
    on_d = lambda o, t: events.append(("draft", o))
    on_f = lambda o, t: events.append(("final", o))
    seg.feed_speech(np.zeros(16_000, dtype=np.float32), 0.0)
    engine._maybe_draft_final(seg, 0.0, on_d, on_f)
    cut = 0.0 + rl.PAUSE_CUT_MS / 1000 + 0.1
    engine._maybe_draft_final(seg, cut, on_d, on_f)
    assert any(k == "final" and "regroup" in o for k, o in events)
    seq_at_final = engine._bar_seq
    seg.feed_speech(np.zeros(16_000, dtype=np.float32), cut + 0.2)
    before = list(events)
    engine._maybe_draft_final(seg, cut + 0.5, on_d, on_f)
    assert events == before
    assert engine._bar_seq == seq_at_final
    engine._maybe_draft_final(seg, cut + rl.LLM_BAR_HOLD_S + 0.5, on_d, on_f)
    assert any("hit points" in o for _, o in events)


def test_hold_extends_while_rewrite_in_flight():
    class Tr:
        _llm_cfg = {"on": True}

    engine = _llm_engine(object(), Tr())
    engine._hold_from = 10.0
    engine._pending_final_applied = False
    engine._llm_revise_inflight = True
    assert engine._still_holding(10.5) is True
    assert engine._still_holding(11.2) is True
    assert engine._still_holding(12.1) is False
    engine._pending_final_applied = True
    assert engine._still_holding(11.2) is False
    assert engine._still_holding(10.5) is True


def test_lookahead_prefetches_next_bar_during_hold():
    import numpy as np
    import time

    class Rec:
        def __init__(self):
            self.n = 0

        def decode(self, _samples):
            self.n += 1
            if self.n == 1:
                return "Okay let us regroup and try again."
            return "He has got like three thousand hit points."

    class Tr:
        def __init__(self):
            self._llm_cfg = {"on": True}
            self.seen = []

        def to_chinese(self, text, final=False):
            return "CT2"

        def prefer_beam_finals(self):
            return True

        def _try_llm_final(self, text, **_kwargs):
            self.seen.append(text)
            return "LOOKAHEAD"

    engine = _llm_engine(Rec(), Tr())
    seg = rl.Segmenter()
    events = []
    on_d = lambda o, t: events.append(("draft", o, t))
    on_f = lambda o, t: events.append(("final", o, t))
    seg.feed_speech(np.zeros(16_000, dtype=np.float32), 0.0)
    engine._maybe_draft_final(seg, 0.0, on_d, on_f)
    cut = 0.0 + rl.PAUSE_CUT_MS / 1000 + 0.1
    engine._maybe_draft_final(seg, cut, on_d, on_f)
    seg.feed_speech(np.zeros(16_000, dtype=np.float32), cut + 0.2)
    engine._maybe_draft_final(seg, cut + 0.5, on_d, on_f)
    time.sleep(0.05)
    assert any("hit points" in s for s in engine.translator.seen)
    assert engine._lookahead_llm == "LOOKAHEAD"
    engine._maybe_draft_final(seg, cut + rl.LLM_BAR_HOLD_S + 0.5, on_d, on_f)
    assert any(k == "final" and t == "LOOKAHEAD" for k, o, t in events if k == "final")


def test_llm_direct_skips_ct2_on_draft_and_final():
    import numpy as np

    class FakeRecognizer:
        def decode(self, _samples):
            return "I want to go now"

    class FakeTranslator:
        def __init__(self):
            self._llm_cfg = {"on": True}
            self.calls = []

        def to_chinese(self, text, final=False):
            self.calls.append((text, final))
            return "CT2"

        def prefer_beam_finals(self):
            return True

        def _try_llm_final(self, text, **_kwargs):
            return ""

    engine = rl.Engine.__new__(rl.Engine)
    engine.recognizer = FakeRecognizer()
    engine.translator = FakeTranslator()
    engine.language_stability = rl.LanguageStability()
    engine.draft_policy = rl.DraftPolicy()
    engine.empty_hits = 0
    engine.llm_direct = True
    engine._last_draft_at = -1e9
    engine._last_draft_end = -1e9
    engine._decoded_samples = -1
    engine._bar_seq = 0
    engine._need_new_bar = True
    engine._bar_lock = __import__("threading").Lock()
    engine._llm_prefetch_inflight = False
    engine._llm_revise_inflight = False
    engine._llm_prefetch_launched = None
    engine._llm_epoch = 0
    engine._llm_prefetch = None
    engine._llm_revise_pending = None
    engine._pending_final_orig = ""
    engine._pending_final_applied = False
    engine._bar_draft_text = ""
    engine._llm_draft_src = ""
    engine._llm_draft_trans = ""
    engine._llm_draft_at = 0.0
    engine._llm_context = []
    seg = rl.Segmenter()
    events = []
    seg.feed_speech(np.zeros(16_000, dtype=np.float32), 0.0)
    engine._maybe_draft_final(seg, 0.0, lambda o, t: events.append(("draft", t)), lambda o, t: events.append(("final", t)))
    engine._maybe_draft_final(
        seg,
        0.0 + rl.PAUSE_CUT_MS / 1000 + 0.1,
        lambda o, t: events.append(("draft", t)),
        lambda o, t: events.append(("final", t)),
    )
    assert engine.translator.calls == []
    assert events
    assert all(trans != "CT2" for _kind, trans in events)


def test_llm_prefetch_usable_accepts_prefix_growth():
    assert rl.llm_prefetch_usable("これは私", "これは私が小さい時に") is True
    assert rl.llm_prefetch_usable("hello", "hello") is True
    assert rl.llm_prefetch_usable("Concorde", "Concord returned") is False
    assert rl.llm_prefetch_usable("一", "一のうちに") is False


def test_llm_config_accepts_camel_or_snake():
    camel = rl._normalize_llm_config({
        "enabled": True,
        "baseUrl": "https://api.example.com/v1",
        "model": "demo",
        "apiKey": "sk-test",
    })
    assert camel["base_url"] == "https://api.example.com/v1"
    assert camel["api_key"] == "sk-test"
    snake = rl._normalize_llm_config({
        "base_url": "https://api.example.com/v1",
        "model": "demo",
        "api_key": "sk-test",
    })
    assert snake["base_url"] == "https://api.example.com/v1"


def test_llm_thinking_fields_follow_probed_param():
    assert rl._llm_thinking_fields(
        {"thinking_param": "reasoning_effort", "thinking": "low"}, True
    ) == {"reasoning_effort": "low"}
    assert rl._llm_thinking_fields({"thinking_param": "thinking", "thinking": "off"}, True) == {
        "thinking": "off"
    }
    assert rl._llm_thinking_fields({"thinking_param": "", "thinking": ""}, True) == {}
    assert rl._llm_thinking_fields({"thinking_param": "thinking", "thinking": ""}, True) == {}


def test_incremental_suffix_planner():
    assert rl.Translator.incremental_suffix("", "hello") is None
    assert rl.Translator.incremental_suffix("Concord returned", "Concorde returned") is None
    assert rl.Translator.incremental_suffix("Concord returned", "Concord returned to its place") == " to its place"
    assert rl.Translator.incremental_suffix("ごんは", "ごんは中山") == "中山"
    assert rl.Translator.suffix_worth_translating(".", "en") == ""
    assert rl.Translator.suffix_worth_translating(" to its place", "en") == "to its place"
    assert rl.Translator.suffix_worth_translating("中山", "ja") == "中山"


def test_translator_draft_reuses_prefix_and_only_translates_suffix():
    translator = rl.Translator.__new__(rl.Translator)
    translator.pairs = {"opus-en-zh": object()}
    translator.ct2 = {}
    translator.clear_incremental()
    calls = []
    translator._greedy = lambda pair, text: calls.append(text) or f"T({text})"
    translator._beam = lambda pair, text, width: calls.append(f"beam:{text}") or f"F({text})"

    assert translator.to_chinese("Concord returned") == "T(Concord returned)"
    assert translator.to_chinese("Concord returned to its place") == "T(Concord returned)T(to its place)"
    assert calls == ["Concord returned", "to its place"]
    assert translator.to_chinese("Concord returned to its place", final=True) == "F(Concord returned to its place)"
    calls.clear()
    assert translator.to_chinese("Concorde returned to its place") == "T(Concorde returned to its place)"
    assert calls == ["Concorde returned to its place"]


def test_incremental_kanji_suffix_keeps_japanese_pair():
    translator = rl.Translator.__new__(rl.Translator)
    translator.pairs = {"opus-ja-en": object(), "opus-en-zh": object()}
    translator.ct2 = {}
    translator.clear_incremental()
    pairs_seen = []

    def fake_pair(pair, text, final=False):
        pairs_seen.append((pair, text))
        return f"{pair}:{text}"

    translator._translate_pair = fake_pair
    translator.to_chinese("ごんは")
    pairs_seen.clear()
    translator.to_chinese("ごんは中山")
    assert pairs_seen[0] == ("opus-ja-en", "中山")


def test_english_fixture_never_emits_japanese_orig(monkeypatch):
    """回归：短英语片段不能因 auto LID 变成假名。

    这里直接按真听译的 f32/16k 输入节奏驱动 Engine，绕开扬声器、环回和
    WS；缺本机模型时跳过，避免把下载状态误判为识别回归。
    """
    import numpy as np

    pytest.importorskip("soundfile")
    root = Path(__file__).resolve().parents[2]
    models = Path(
        os.environ.get(
            "LT_ENGINE_MODELS",
            Path.home() / "AppData" / "Roaming" / "com.livetranslator.desktop" / "models",
        )
    )
    needed = [
        models / "sense-voice" / "model.int8.onnx",
        models / "sense-voice" / "tokens.txt",
        models / "vad" / "silero_vad.onnx",
    ]
    if not all(path.is_file() for path in needed):
        pytest.skip("本机没有真听译模型")

    # 这条回归只验证识别和语言门；不加载 OPUS，避免把翻译耗时混入回归信号。
    class StubTranslator:
        def __init__(self, _models):
            pass

        def to_chinese(self, _text, final=False):
            return "译"

        def prefer_beam_finals(self):
            return False

    monkeypatch.setattr(rl, "Translator", StubTranslator)
    import soundfile as sf

    audio, sample_rate = sf.read(root / "tests" / "fixtures" / "selftest_en.ogg", dtype="float32", always_2d=True)
    audio = audio.mean(axis=1)
    # 与壳侧格式一致：单声道、16k、f32le。线性插值和 tools/selftest.py 相同。
    output_len = int(len(audio) * rl.SAMPLE_RATE / sample_rate)
    points = __import__("numpy").arange(output_len) * sample_rate / rl.SAMPLE_RATE
    i0 = __import__("numpy").clip(points.astype("int64"), 0, len(audio) - 1)
    i1 = __import__("numpy").clip(i0 + 1, 0, len(audio) - 1)
    audio = (audio[i0] * (1 - (points - i0)) + audio[i1] * (points - i0)).astype(np.float32)
    seconds = float(os.environ.get("LT_ENGINE_REGRESSION_SECONDS", "18"))
    audio = audio[: int(seconds * rl.SAMPLE_RATE)]  # 默认最小化到会出现误判的开头 18 秒

    engine = rl.Engine(models)
    segmenter = rl.Segmenter()
    events = []
    now = 0.0
    chunk = 1600  # 100ms PCM；墙钟推进 90ms，匹配真实回放器

    for offset in range(0, len(audio), chunk):
        engine.process(
            audio[offset : offset + chunk],
            segmenter,
            now,
            lambda orig, trans: events.append(("draft", orig, trans)),
            lambda orig, trans: events.append(("final", orig, trans)),
        )
        now += 0.09
    for _ in range(35):  # 让口气切条与未确认候选的收尾逻辑都走到
        engine.process(
            np.zeros(chunk, dtype=np.float32),
            segmenter,
            now,
            lambda orig, trans: events.append(("draft", orig, trans)),
            lambda orig, trans: events.append(("final", orig, trans)),
        )
        now += 0.09

    originals = " ".join(orig for _kind, orig, _trans in events)
    escaped_originals = originals.encode("unicode_escape").decode()
    assert re.search(r"[A-Za-z]{3,}", originals), f"英语素材没有识别出英文：{originals!r}"
    assert not re.search(r"[\u3040-\u30fa\u30ff\uac00-\ud7af\u4e00-\u9fff]", originals), (
        f"英语素材误出了非英语原文：{escaped_originals}"
    )


@pytest.mark.parametrize(
    ("fixture", "expected_script", "wrong_script", "expected_language"),
    [
        ("selftest_ja.ogg", r"[\u3040-\u30ff]", r"[\uac00-\ud7af]", "ja"),
        ("selftest_ko.ogg", r"[\uac00-\ud7af]", r"[\u3040-\u30ff]", "ko"),
    ],
)
def test_real_engine_keeps_japanese_and_korean_fixtures_in_their_source_language(
    monkeypatch, fixture, expected_script, wrong_script, expected_language
):
    """真 PCM → 真识别 → 语言门：日语、韩语不能互相串进原文。"""
    import numpy as np

    pytest.importorskip("soundfile")
    root = Path(__file__).resolve().parents[2]
    models = Path(
        os.environ.get(
            "LT_ENGINE_MODELS",
            Path.home() / "AppData" / "Roaming" / "com.livetranslator.desktop" / "models",
        )
    )
    needed = [
        models / "sense-voice" / "model.int8.onnx",
        models / "sense-voice" / "tokens.txt",
        models / "vad" / "silero_vad.onnx",
    ]
    if not all(path.is_file() for path in needed):
        pytest.skip("本机没有真听译模型")

    class StubTranslator:
        def __init__(self, _models):
            pass

        def to_chinese(self, _text, final=False):
            return "译"

        def prefer_beam_finals(self):
            return False

    monkeypatch.setattr(rl, "Translator", StubTranslator)
    import soundfile as sf

    audio, sample_rate = sf.read(root / "tests" / "fixtures" / fixture, dtype="float32", always_2d=True)
    audio = audio.mean(axis=1)
    output_len = int(len(audio) * rl.SAMPLE_RATE / sample_rate)
    points = np.arange(output_len) * sample_rate / rl.SAMPLE_RATE
    i0 = np.clip(points.astype("int64"), 0, len(audio) - 1)
    i1 = np.clip(i0 + 1, 0, len(audio) - 1)
    audio = (audio[i0] * (1 - (points - i0)) + audio[i1] * (points - i0)).astype(np.float32)
    seconds = float(os.environ.get("LT_ENGINE_OTHER_LANG_SECONDS", "18"))
    audio = audio[: int(seconds * rl.SAMPLE_RATE)]

    engine = rl.Engine(models)
    segmenter = rl.Segmenter()
    events = []
    now = 0.0
    chunk = 1600
    for offset in range(0, len(audio), chunk):
        engine.process(
            audio[offset : offset + chunk],
            segmenter,
            now,
            lambda orig, trans: events.append(("draft", orig, trans)),
            lambda orig, trans: events.append(("final", orig, trans)),
        )
        now += 0.09
    for _ in range(35):
        engine.process(
            np.zeros(chunk, dtype=np.float32),
            segmenter,
            now,
            lambda orig, trans: events.append(("draft", orig, trans)),
            lambda orig, trans: events.append(("final", orig, trans)),
        )
        now += 0.09

    originals = " ".join(orig for _kind, orig, _trans in events)
    escaped_originals = originals.encode("unicode_escape").decode()
    assert re.search(expected_script, originals), f"{fixture} 没有识别出预期源语言：{escaped_originals}"
    assert not re.search(wrong_script, originals), f"{fixture} 串进另一种源语言：{escaped_originals}"
    assert engine.language_stability.stable_language == expected_language


def test_early_revise_fires_at_sentence_end_and_final_is_born_llm():
    import time
    import numpy as np

    SENT = "So we are going to try this boss fight."

    class Rec:
        def __init__(self):
            self.texts = ["So we are going", SENT, SENT]

        def decode(self, _samples):
            return self.texts.pop(0) if self.texts else SENT

    class Tr:
        def __init__(self):
            self._llm_cfg = {"on": True}
            self.llm_calls = []

        def to_chinese(self, text, final=False):
            return "我们打算"

        def prefer_beam_finals(self):
            return True

        def _try_llm_final(self, text, **_kwargs):
            self.llm_calls.append(text)
            time.sleep(0.05)
            return "这场首领战我们再试一次"

    engine = _llm_engine(Rec(), Tr())
    seg = rl.Segmenter()
    events = []
    on_d = lambda o, t: events.append(("draft", o, t))
    on_f = lambda o, t: events.append(("final", o, t))

    seg.feed_speech(np.zeros(16_000, dtype=np.float32), 0.0)
    engine._maybe_draft_final(seg, 0.0, on_d, on_f)  # 半句：只预取
    assert engine.translator.llm_calls == ["So we are going"]
    time.sleep(0.2)  # 等预取落地进存货

    seg.feed_speech(np.zeros(16_000, dtype=np.float32), 0.4)
    engine._maybe_draft_final(seg, 0.5, on_d, on_f)  # 句末已现：提前整句改写
    assert engine.translator.llm_calls[-1] == SENT
    time.sleep(0.2)  # 改写落地入存货

    seg.feed_speech(np.zeros(16_000, dtype=np.float32), 1.2)
    engine._maybe_draft_final(seg, 1.5, on_d, on_f)  # 停顿切条：定稿
    finals = [e for e in events if e[0] == "final"]
    assert finals, "没有出定稿"
    assert finals[0][2] == "这场首领战我们再试一次"  # 出生即整句 LLM，不经 CT2 版
    assert engine._pending_final_applied is True


def test_revise_landing_extends_hold_for_reading():
    import time

    class Tr:
        _llm_cfg = {"on": True}

    engine = _llm_engine(object(), Tr())
    seq = engine._bump_bar()
    engine._pending_final_orig = "He got like three thousand hit points."
    engine._pending_final_applied = True
    engine._hold_from = time.monotonic() - 2.0  # 定稿保底期已过
    assert engine._still_holding(time.monotonic()) is False

    shown = []
    engine._apply_llm_if_current(
        seq, engine._pending_final_orig, "他已经有了大约三千点生命值", lambda o, t: shown.append(t)
    )
    assert shown == ["他已经有了大约三千点生命值"]
    # 换说法的纠正版落地：从这一刻重新保底
    assert engine._still_holding(time.monotonic() + 0.5) is True
    assert engine._still_holding(time.monotonic() + rl.LLM_BAR_HOLD_S + 0.3) is False

    # 流式生长（前缀延长）不算换版，不再重置保底
    hold_before = engine._hold_from
    engine._apply_llm_if_current(
        seq, engine._pending_final_orig, "他已经有了大约三千点生命值了", lambda o, t: shown.append(t)
    )
    assert engine._hold_from == hold_before
