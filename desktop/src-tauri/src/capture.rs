//! 壳侧采音（ADR 0008：壳抓音源 PCM，经 WS 二进制帧交给听译）。
//! 进程音源 = Application Loopback（AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK）；
//! 系统混音 = 默认播放设备环回（AUDCLNT_STREAMFLAGS_LOOPBACK）。
//! 两者统一重采样成 16kHz / mono / f32le，按 ~100ms 块发。

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::Sender;
use std::sync::Arc;

use windows::core::{implement, Interface, GUID, PCWSTR};
use windows::Win32::Foundation::{CloseHandle, WAIT_OBJECT_0};
use windows::Win32::Media::Audio::{
    ActivateAudioInterfaceAsync, IActivateAudioInterfaceAsyncOperation,
    IActivateAudioInterfaceCompletionHandler, IActivateAudioInterfaceCompletionHandler_Impl,
    IAudioCaptureClient, IAudioClient, IMMDeviceEnumerator, AUDCLNT_BUFFERFLAGS_SILENT,
    AUDCLNT_SHAREMODE_SHARED, AUDCLNT_STREAMFLAGS_EVENTCALLBACK, AUDCLNT_STREAMFLAGS_LOOPBACK,
    AUDIOCLIENT_ACTIVATION_PARAMS, AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK,
    PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE, VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK,
    WAVEFORMATEX,
};
use windows::Win32::System::Com::StructuredStorage::{
    PROPVARIANT, PROPVARIANT_0_0, PROPVARIANT_0_0_0,
};
use windows::Win32::System::Com::{
    CoCreateInstance, CoInitializeEx, CoTaskMemAlloc, CoTaskMemFree, CoUninitialize, BLOB,
    CLSCTX_ALL, COINIT_MULTITHREADED,
};
use windows::Win32::System::Threading::{CreateEventW, WaitForSingleObject};
use windows::Win32::System::Variant::VT_BLOB;

const SAMPLE_RATE: u32 = 16000;
/// ~100ms 一帧入缝
const FRAME_SAMPLES: usize = 1600;
const WAVE_FORMAT_IEEE_FLOAT: u16 = 3;

pub enum CaptureSource {
    /// 按进程环回抓这一棵进程树
    Process(u32),
    /// 默认播放设备的系统混音
    System,
}

pub struct CaptureHandle {
    stop: Arc<AtomicBool>,
    thread: Option<std::thread::JoinHandle<()>>,
}

impl CaptureHandle {
    pub fn stop(mut self) {
        self.stop.store(true, Ordering::Relaxed);
        if let Some(t) = self.thread.take() {
            let _ = t.join();
        }
    }
}

impl Drop for CaptureHandle {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
    }
}

/// 起一路采音。返回的线程把 f32 块推进 `out`；错误也走 `out` 发 `Err` 报告激活失败。
pub fn spawn_capture(
    source: CaptureSource,
    out: Sender<Vec<f32>>,
) -> Result<CaptureHandle, String> {
    let stop = Arc::new(AtomicBool::new(false));
    let stop2 = stop.clone();
    let (ready_tx, ready_rx) = std::sync::mpsc::channel::<Result<(), String>>();
    let thread = std::thread::Builder::new()
        .name("pcm-capture".into())
        .spawn(move || {
            let co = unsafe { CoInitializeEx(None, COINIT_MULTITHREADED) };
            let result = capture_loop(&source, &stop2, &out, &ready_tx);
            if co.is_ok() {
                unsafe { CoUninitialize() };
            }
            let _ = result;
        })
        .map_err(|e| e.to_string())?;
    // 等激活结果（Initialize 完成才算抓到）
    match ready_rx.recv_timeout(std::time::Duration::from_secs(10)) {
        Ok(Ok(())) => Ok(CaptureHandle {
            stop,
            thread: Some(thread),
        }),
        Ok(Err(e)) => {
            stop.store(true, Ordering::Relaxed);
            let _ = thread.join();
            Err(e)
        }
        Err(_) => {
            stop.store(true, Ordering::Relaxed);
            let _ = thread.join();
            Err("采音激活超时".into())
        }
    }
}

fn capture_loop(
    source: &CaptureSource,
    stop: &AtomicBool,
    out: &Sender<Vec<f32>>,
    ready_tx: &std::sync::mpsc::Sender<Result<(), String>>,
) -> Result<(), String> {
    unsafe {
        let (client, fmt_info) = match source {
            CaptureSource::Process(pid) => activate_process_loopback(*pid)?,
            CaptureSource::System => activate_system_loopback()?,
        };

        let event =
            CreateEventW(None, false, false, None).map_err(|e| format!("CreateEvent 失败：{e}"))?;
        let flags = AUDCLNT_STREAMFLAGS_LOOPBACK | AUDCLNT_STREAMFLAGS_EVENTCALLBACK;
        // 100ms 缓冲（REFERENCE_TIME，100ns 单位）
        client
            .Initialize(
                AUDCLNT_SHAREMODE_SHARED,
                flags,
                1_000_000,
                0,
                &fmt_info.format,
                None,
            )
            .map_err(|e| format!("Initialize 失败：{e}"))?;
        client
            .SetEventHandle(event)
            .map_err(|e| format!("SetEventHandle 失败：{e}"))?;
        let capture: IAudioCaptureClient = client
            .GetService()
            .map_err(|e| format!("GetService(IAudioCaptureClient) 失败：{e}"))?;
        client.Start().map_err(|e| format!("Start 失败：{e}"))?;

        let _ = ready_tx.send(Ok(()));

        let mut frame: Vec<f32> = Vec::with_capacity(FRAME_SAMPLES * 2);
        let mut native_buf: Vec<f32>;
        let mut silent_ticks: u32 = 0;
        while !stop.load(Ordering::Relaxed) {
            if WaitForSingleObject(event, 200) != WAIT_OBJECT_0 {
                // 进程环回在目标进程无声时不产生数据（无音频时钟）。
                // 补静音心跳，让听译的时间线连续：无声走「还没听到人声」，
                // 不再误报「抓不到」；「抓不到」只留给激活失败（壳直接发 no_audio）。
                silent_ticks += 1;
                if silent_ticks >= 1 {
                    frame.extend(std::iter::repeat(0.0f32).take(FRAME_SAMPLES));
                    if frame.len() >= FRAME_SAMPLES {
                        let chunk: Vec<f32> = frame.drain(..FRAME_SAMPLES).collect();
                        if out.send(chunk).is_err() {
                            let _ = client.Stop();
                            let _ = CloseHandle(event);
                            return Ok(());
                        }
                    }
                }
                continue;
            }
            silent_ticks = 0;
            let mut pkt: u32;
            loop {
                pkt = match capture.GetNextPacketSize() {
                    Ok(n) => n,
                    Err(_) => break,
                };
                if pkt == 0 {
                    break;
                }
                let mut data: *mut u8 = std::ptr::null_mut();
                let mut frames: u32 = 0;
                let mut flags: u32 = 0;
                if capture
                    .GetBuffer(&mut data, &mut frames, &mut flags, None, None)
                    .is_err()
                {
                    break;
                }
                if (flags as i32) & AUDCLNT_BUFFERFLAGS_SILENT.0 != 0 || data.is_null() {
                    native_buf = vec![0.0f32; frames as usize];
                } else {
                    // 采样宽度和声道按激活时确认的格式来（进程环回=f32；系统环回 mix format=f32）
                    let n = frames as usize * fmt_info.channels as usize;
                    let slice = std::slice::from_raw_parts(data as *const f32, n);
                    native_buf = slice.to_vec();
                }
                let _ = capture.ReleaseBuffer(frames);
                let mono = if fmt_info.channels > 1 {
                    to_mono(&native_buf, fmt_info.channels as usize)
                } else {
                    native_buf
                };
                let pcm16k = if fmt_info.needs_convert {
                    resample_to_16k(mono, fmt_info.sample_rate)
                } else {
                    mono
                };
                frame.extend_from_slice(&pcm16k);
                while frame.len() >= FRAME_SAMPLES {
                    let chunk: Vec<f32> = frame.drain(..FRAME_SAMPLES).collect();
                    if out.send(chunk).is_err() {
                        // 接收端（WS 写任务）没了：开听已停
                        let _ = client.Stop();
                        let _ = CloseHandle(event);
                        return Ok(());
                    }
                }
            }
        }
        let _ = client.Stop();
        let _ = CloseHandle(event);
        Ok(())
    }
}

fn target_format() -> WAVEFORMATEX {
    WAVEFORMATEX {
        wFormatTag: WAVE_FORMAT_IEEE_FLOAT,
        nChannels: 1,
        nSamplesPerSec: SAMPLE_RATE,
        wBitsPerSample: 32,
        nBlockAlign: 4,
        nAvgBytesPerSec: SAMPLE_RATE * 4,
        cbSize: 0,
    }
}

fn activate_process_loopback(pid: u32) -> Result<(IAudioClient, FmtInfo), String> {
    // 进程环回直接向系统要 16k/mono/f32，不用我们重采样
    let fmt = target_format();
    let info = FmtInfo {
        format: fmt,
        channels: 1,
        sample_rate: SAMPLE_RATE,
        needs_convert: false,
    };

    let params = AUDIOCLIENT_ACTIVATION_PARAMS {
        ActivationType: AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK,
        Anonymous: windows::Win32::Media::Audio::AUDIOCLIENT_ACTIVATION_PARAMS_0 {
            ProcessLoopbackParams:
                windows::Win32::Media::Audio::AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS {
                    TargetProcessId: pid,
                    ProcessLoopbackMode: PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE,
                },
        },
    };
    let size = std::mem::size_of::<AUDIOCLIENT_ACTIVATION_PARAMS>();
    let blob = unsafe { CoTaskMemAlloc(size) } as *mut u8;
    if blob.is_null() {
        return Err("CoTaskMemAlloc 失败".into());
    }
    unsafe {
        std::ptr::copy_nonoverlapping(&params as *const _ as *const u8, blob, size);
    }
    // 按 C 语义零初始化后裸指针逐字段写（union + ManuallyDrop 的安全写法在这条
    // 路径上出过堆损坏，直接对齐微软 ApplicationLoopback 样例的内存布局）
    let mut prop: PROPVARIANT = unsafe { std::mem::zeroed() };
    unsafe {
        let inner = &mut *(&mut prop as *mut PROPVARIANT as *mut PROPVARIANT_0_0);
        inner.vt = VT_BLOB;
        inner.wReserved1 = 0;
        inner.wReserved2 = 0;
        inner.wReserved3 = 0;
        inner.Anonymous.blob.cbSize = size as u32;
        inner.Anonymous.blob.pBlobData = blob;
    }

    let (tx, rx) = std::sync::mpsc::channel::<Result<IAudioClient, String>>();
    let handler: IActivateAudioInterfaceCompletionHandler = Handler { tx }.into();
    let iid_iaudioclient: GUID =
        windows::core::GUID::from_u128(0x1CB9AD4C_DBFA_4c32_B178_C2F568A703B2);
    let op: IActivateAudioInterfaceAsyncOperation = unsafe {
        ActivateAudioInterfaceAsync(
            PCWSTR(VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK.as_ptr()),
            &iid_iaudioclient,
            Some(&prop as *const PROPVARIANT),
            &handler,
        )
    }
    .map_err(|e| format!("进程环回激活失败：{e}"))?;
    let _ = op; // 持有引用直到回调完成

    let client = rx
        .recv_timeout(std::time::Duration::from_secs(5))
        .map_err(|_| "进程环回激活超时".to_string())?
        .map_err(|e| format!("进程环回激活失败：{e}"))?;

    // blob 的所有权已交给 PROPVARIANT（vt=VT_BLOB）：
    // windows crate 的 PROPVARIANT Drop 会 PropVariantClear 释放 pBlobData，
    // 这里再手动 CoTaskMemFree 就是 double free → HEAP_CORRUPTION（已踩过）
    Ok((client, info))
}

fn activate_system_loopback() -> Result<(IAudioClient, FmtInfo), String> {
    unsafe {
        let enumerator: IMMDeviceEnumerator =
            CoCreateInstance(&CLSID_MM_DEVICE_ENUMERATOR_LOCAL, None, CLSCTX_ALL)
                .map_err(|e| format!("设备枚举失败：{e}"))?;
        let device = enumerator
            .GetDefaultAudioEndpoint(
                windows::Win32::Media::Audio::eRender,
                windows::Win32::Media::Audio::eConsole,
            )
            .map_err(|e| format!("没有默认播放设备：{e}"))?;
        let client: IAudioClient = device
            .Activate(CLSCTX_ALL, None)
            .map_err(|e| format!("激活 IAudioClient 失败：{e}"))?;
        let mix_ptr = client
            .GetMixFormat()
            .map_err(|e| format!("GetMixFormat 失败：{e}"))?;
        let mix = *mix_ptr;
        let info = FmtInfo {
            format: *mix_ptr,
            channels: mix.nChannels,
            sample_rate: mix.nSamplesPerSec,
            needs_convert: mix.nSamplesPerSec != SAMPLE_RATE,
        };
        // mix format 基本是 float；保守处理非 float 的情况
        if mix.wFormatTag != WAVE_FORMAT_IEEE_FLOAT && mix.wFormatTag != 0xFFFE {
            let _ = CoTaskMemFree(Some(mix_ptr as *const core::ffi::c_void));
            let tag = mix.wFormatTag;
            return Err(format!("不支持的混音格式 tag={tag}"));
        }
        // 注意：0xFFFE (EXTENSIBLE) 的实际子格式也可能不是 float；这里赌常见情况，错了 GetBuffer 解出的会是噪声
        Ok((client, info))
    }
}

// sources.rs 里的 CLSID 是私有的；这里用同一常量
const CLSID_MM_DEVICE_ENUMERATOR_LOCAL: GUID =
    GUID::from_u128(0xBCDE_0395_E52F_467C_8E3D_C457_9291_692E);

pub struct FmtInfo {
    format: WAVEFORMATEX,
    channels: u16,
    sample_rate: u32,
    needs_convert: bool,
}

fn to_mono(interleaved: &[f32], channels: usize) -> Vec<f32> {
    interleaved
        .chunks(channels)
        .map(|c| c.iter().sum::<f32>() / channels as f32)
        .collect()
}

/// 线性插值重采样到 16k（参照 reference/LiveTranslate 的做法，只学接口不搬仓）
fn resample_to_16k(input: Vec<f32>, from: u32) -> Vec<f32> {
    if from == SAMPLE_RATE || input.is_empty() {
        return input;
    }
    let ratio = SAMPLE_RATE as f64 / from as f64;
    let n_out = (input.len() as f64 * ratio) as usize;
    let mut out = Vec::with_capacity(n_out);
    let last = input.len() - 1;
    for i in 0..n_out {
        let pos = i as f64 / ratio;
        let i0 = pos.floor() as usize;
        let i0 = i0.min(last);
        let i1 = (i0 + 1).min(last);
        let frac = (pos - i0 as f64) as f32;
        out.push(input[i0] * (1.0 - frac) + input[i1] * frac);
    }
    out
}

/// 进程环回激活是异步的：实现完成回调，把结果抛回等待线程
#[implement(IActivateAudioInterfaceCompletionHandler)]
struct Handler {
    tx: std::sync::mpsc::Sender<Result<IAudioClient, String>>,
}

impl IActivateAudioInterfaceCompletionHandler_Impl for Handler_Impl {
    fn ActivateCompleted(
        &self,
        operation: windows::core::Ref<'_, IActivateAudioInterfaceAsyncOperation>,
    ) -> windows::core::Result<()> {
        unsafe {
            let Some(op) = operation.as_ref() else {
                let _ = self.tx.send(Err("激活回调缺操作句柄".into()));
                return Ok(());
            };
            let op: &IActivateAudioInterfaceAsyncOperation = op;
            let mut hr: windows::core::HRESULT = windows::core::HRESULT(0);
            let mut unk: Option<windows::core::IUnknown> = None;
            if let Err(e) = op.GetActivateResult(&mut hr, &mut unk) {
                let _ = self.tx.send(Err(format!("GetActivateResult 失败：{e}")));
                return Ok(());
            }
            if hr.is_err() {
                let _ = self.tx.send(Err(format!("激活 HRESULT {hr:?}")));
                return Ok(());
            }
            match unk.and_then(|u| u.cast::<IAudioClient>().ok()) {
                Some(client) => {
                    let _ = self.tx.send(Ok(client));
                }
                None => {
                    let _ = self.tx.send(Err("激活结果不是 IAudioClient".into()));
                }
            }
        }
        Ok(())
    }
}
