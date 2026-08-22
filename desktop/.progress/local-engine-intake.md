# intake · local-engine

日期：2026-08-18  
裁决：**需返工**（续作同一 slug，不开新主题）

## 手验

1. 选正在出声的进程开听 — **未过**。采音全链已通（Application Loopback 激活 / Initialize / Start / PCM 线程）。卡在壳 `ensure_connected`：`connect_async` 挂起，3s 超时后开听失败。引擎端口手连正常。
2. `FAKE_SCRIPT=en` — **未做**（被 1 阻塞）。

## 本窗已修（不要回滚）

- PROPVARIANT + 手动 `CoTaskMemFree` double free（开听必崩）
- reqwest `rustls-tls`；删 VAD 后应用内能补下
- 国内镜像前置（hf-mirror / ghfast / gh-proxy）
- 进程监测改绑 pid；`do_start` 失败停采音

## 返工 Must

1. 壳能连上已 READY 的听译 WS，把 `start` 发出去，进程音源出真字幕
2. 手验 1 + 手验 2 都过
3. 崩溃路径也清引擎子进程
4. 诊断探针用完删掉

## 不要动

缝三类回事件、假听译协议、已绿的采音激活、翻译栈偏离（下一主题再补 ADR）。
