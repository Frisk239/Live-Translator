//! 音源清单：枚举系统音频会话（IAudioSessionManager2），查峰值电平，
//! 只列**当前真在出声**的进程，按电平排前；系统混音置底（ADR 0001）。
//! 每次拉取都重新枚举（面板刷新按钮就是重拉）；没人出声时面板给空态。
//! 抓 PCM 做听译是下一刀；这里只「看」不「听」。

use serde::Serialize;
use std::collections::HashMap;
use windows::core::{Interface, Result};
use windows::Win32::Media::Audio::Endpoints::IAudioMeterInformation;
use windows::Win32::Media::Audio::{
    eConsole, eRender, IAudioSessionControl2, IAudioSessionEnumerator, IAudioSessionManager2,
    IMMDeviceEnumerator,
};
use windows::Win32::System::Com::{CoCreateInstance, CLSCTX_ALL};

/// MMDeviceEnumerator 的 CLSID（BCDE0395-E52F-467C-8E3D-C4579291692E；
/// windows crate 未导出这个常量，自己写）
const CLSID_MM_DEVICE_ENUMERATOR: windows::core::GUID =
    windows::core::GUID::from_u128(0xBCDE_0395_E52F_467C_8E3D_C457_9291_692E);

#[derive(Serialize, Clone, Debug)]
#[serde(rename_all = "camelCase")]
pub struct AudioSource {
    pub id: String,
    pub process_name: String,
    pub friendly_name: String,
    pub audible: bool,
    pub system: bool,
}

/// 电平低于这个值不算在出声（peak 0.0–1.0，约 -60dB）
const PEAK_THRESHOLD: f32 = 0.001;

pub struct AudibleProc {
    pub exe: String,
    pub peak: f32,
    pub pid: u32,
}

#[allow(dead_code)]
pub fn list() -> Vec<AudioSource> {
    list_with_remembered(None)
}

/// 出声进程排前；上次选的音源若进程还在、只是没在出声，保留成一行灰的
/// （能选、能开听，等它出声）；系统混音置底。
pub fn list_with_remembered(remembered: Option<&str>) -> Vec<AudioSource> {
    let remembered = remembered.filter(|s| !s.is_empty() && *s != "system");
    let mut audible = audible_processes().unwrap_or_default();
    audible.sort_by(|a, b| b.peak.total_cmp(&a.peak).then(a.exe.cmp(&b.exe)));

    let mut sources: Vec<AudioSource> = audible
        .into_iter()
        .map(|p| AudioSource {
            friendly_name: friendly_from(&p.exe),
            id: p.exe.clone(),
            process_name: p.exe,
            audible: true,
            system: false,
        })
        .collect();

    if let Some(r) = remembered {
        let still_listed = sources.iter().any(|s| s.id == r);
        // 进程表里还找得到 = 还活着，只是没在出声；找不到 = 真退出了（面板会停住等再选）
        if !still_listed && process_exists(r) {
            sources.push(AudioSource {
                friendly_name: format!("{} · 没在出声", friendly_from(r)),
                id: r.to_string(),
                process_name: r.to_string(),
                audible: false,
                system: false,
            });
        }
    }

    sources.push(AudioSource {
        id: "system".into(),
        process_name: String::new(),
        friendly_name: "系统混音 · 喇叭里正在响的全部声音".into(),
        audible: true,
        system: true,
    });
    sources
}

/// 进程表里有没有这个 exe（不看出不出声）
pub fn process_exists(exe: &str) -> bool {
    exe_names_by_pid().values().any(|name| name == exe)
}

/// 这个 exe 当前正在出声的任一 pid（优先，用于进程环回立刻抓到声）
pub fn audible_pid(exe: &str) -> Option<u32> {
    audible_processes()
        .ok()?
        .into_iter()
        .find(|p| p.exe == exe)
        .map(|p| p.pid)
}

/// 进程表里有没有这个 pid（采音绑定的是具体 pid，进程退出监测要查 pid 而不是 exe 名）
pub fn pid_exists(pid: u32) -> bool {
    exe_names_by_pid().contains_key(&pid)
}

/// 这个 exe 的任一 pid（没在出声时兜底，环回等它出声）
pub fn any_pid(exe: &str) -> Option<u32> {
    exe_names_by_pid()
        .into_iter()
        .find(|(_, name)| name == exe)
        .map(|(pid, _)| pid)
}

pub fn label(id: &str) -> String {
    if id == "system" {
        return "系统混音".into();
    }
    friendly_from(id)
}

fn friendly_from(process: &str) -> String {
    let base = process.trim_end_matches(".exe");
    let mut cs = base.chars();
    match cs.next() {
        Some(f) => f.to_uppercase().collect::<String>() + cs.as_str(),
        None => String::new(),
    }
}

fn own_exe_name() -> String {
    std::env::current_exe()
        .ok()
        .and_then(|p| p.file_name().map(|n| n.to_string_lossy().to_lowercase()))
        .unwrap_or_default()
}

/// 默认播放端点上的音频会话 → (进程名, 峰值电平)
fn audible_processes() -> Result<Vec<AudibleProc>> {
    unsafe {
        let co = windows::Win32::System::Com::CoInitializeEx(
            None,
            windows::Win32::System::Com::COINIT_MULTITHREADED,
        );
        let out = walk_sessions();
        if co.is_ok() {
            windows::Win32::System::Com::CoUninitialize();
        }
        out
    }
}

fn walk_sessions() -> Result<Vec<AudibleProc>> {
    unsafe {
        let enumerator: IMMDeviceEnumerator =
            CoCreateInstance(&CLSID_MM_DEVICE_ENUMERATOR, None, CLSCTX_ALL)?;
        let device = enumerator.GetDefaultAudioEndpoint(eRender, eConsole)?;
        let mgr: IAudioSessionManager2 = device.Activate(CLSCTX_ALL, None)?;
        let sessions: IAudioSessionEnumerator = mgr.GetSessionEnumerator()?;

        let count = sessions.GetCount()?;
        let own = own_exe_name();
        let mut names = exe_names_by_pid();
        let mut out: Vec<AudibleProc> = Vec::new();
        for i in 0..count {
            let ctl = sessions.GetSession(i)?;
            let ctl2: IAudioSessionControl2 = ctl.cast()?;
            let meter: IAudioMeterInformation = ctl.cast()?;
            // pid 为 0 的是「系统声音」会话，归系统混音，不单列
            let Ok(pid) = ctl2.GetProcessId() else {
                continue;
            };
            let Ok(peak) = meter.GetPeakValue() else {
                continue;
            };
            if pid == 0 || peak <= PEAK_THRESHOLD {
                continue;
            }
            let exe = names.remove(&pid).unwrap_or_default();
            if exe.is_empty() || exe == own {
                continue;
            }
            match out.iter_mut().find(|p| p.exe == exe) {
                Some(hit) => hit.peak = hit.peak.max(peak),
                None => out.push(AudibleProc { exe, peak, pid }),
            }
        }
        Ok(out)
    }
}

fn exe_names_by_pid() -> HashMap<u32, String> {
    use windows::Win32::System::Diagnostics::ToolHelp::{
        CreateToolhelp32Snapshot, Process32FirstW, Process32NextW, PROCESSENTRY32W,
        TH32CS_SNAPPROCESS,
    };
    let mut map: HashMap<u32, String> = HashMap::new();
    unsafe {
        let Ok(snap) = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0) else {
            return map;
        };
        let mut entry = PROCESSENTRY32W::default();
        entry.dwSize = std::mem::size_of::<PROCESSENTRY32W>() as u32;
        if Process32FirstW(snap, &mut entry).is_ok() {
            loop {
                let end = entry
                    .szExeFile
                    .iter()
                    .position(|&c| c == 0)
                    .unwrap_or(entry.szExeFile.len());
                map.entry(entry.th32ProcessID).or_insert_with(|| {
                    String::from_utf16_lossy(&entry.szExeFile[..end]).to_lowercase()
                });
                if Process32NextW(snap, &mut entry).is_err() {
                    break;
                }
            }
        }
        let _ = windows::Win32::Foundation::CloseHandle(snap);
    }
    map
}
