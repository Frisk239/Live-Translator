//! 托管源与记住我的登录态（ADR 0027 / 0031）。

use serde::{Deserialize, Serialize};
use windows::core::{PCWSTR, PWSTR};
use windows::Win32::Security::Credentials::{
    CredDeleteW, CredFree, CredReadW, CredWriteW, CREDENTIALW, CRED_PERSIST_LOCAL_MACHINE,
    CRED_TYPE_GENERIC,
};

pub const DEFAULT_ORIGIN: &str = "http://127.0.0.1:8787";
const CRED_TARGET: &str = "LiveTranslator/hosted";

#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SavedSession {
    pub email: String,
    pub token: String,
}

pub fn origin() -> String {
    std::env::var("LIVE_TRANSLATOR_HOSTED_ORIGIN")
        .ok()
        .map(|s| s.trim().trim_end_matches('/').to_string())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| DEFAULT_ORIGIN.to_string())
}

pub fn listen_ws_url() -> Result<String, String> {
    listen_ws_url_for(&origin())
}

pub fn listen_ws_url_for(origin: &str) -> Result<String, String> {
    let origin = origin.trim().trim_end_matches('/');
    if let Some(rest) = origin.strip_prefix("https://") {
        Ok(format!("wss://{rest}/listen"))
    } else if let Some(rest) = origin.strip_prefix("http://") {
        Ok(format!("ws://{rest}/listen"))
    } else {
        Err("托管地址无效".into())
    }
}

fn wide(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(std::iter::once(0)).collect()
}

pub fn save_remembered(session: &SavedSession) -> Result<(), String> {
    let raw = serde_json::to_string(session).map_err(|e| e.to_string())?;
    let mut blob = raw.into_bytes();
    let mut target = wide(CRED_TARGET);
    unsafe {
        let mut cred = std::mem::zeroed::<CREDENTIALW>();
        cred.Type = CRED_TYPE_GENERIC;
        cred.TargetName = PWSTR(target.as_mut_ptr());
        cred.CredentialBlobSize = blob.len() as u32;
        cred.CredentialBlob = blob.as_mut_ptr();
        cred.Persist = CRED_PERSIST_LOCAL_MACHINE;
        CredWriteW(&cred, 0).map_err(|e| format!("没能记住登录：{e}"))?;
    }
    Ok(())
}

pub fn load_remembered() -> Option<SavedSession> {
    let target = wide(CRED_TARGET);
    unsafe {
        let mut cred: *mut CREDENTIALW = std::ptr::null_mut();
        if CredReadW(PCWSTR(target.as_ptr()), CRED_TYPE_GENERIC, None, &mut cred).is_err() {
            return None;
        }
        let parsed = {
            let c = &*cred;
            let bytes = std::slice::from_raw_parts(c.CredentialBlob, c.CredentialBlobSize as usize);
            serde_json::from_slice(bytes).ok()
        };
        CredFree(cred.cast());
        parsed
    }
}

pub fn clear_remembered() {
    let target = wide(CRED_TARGET);
    unsafe {
        let _ = CredDeleteW(PCWSTR(target.as_ptr()), CRED_TYPE_GENERIC, None);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_origin_is_loopback_ws() {
        assert_eq!(
            listen_ws_url_for(DEFAULT_ORIGIN).unwrap(),
            "ws://127.0.0.1:8787/listen"
        );
    }

    #[test]
    fn https_origin_becomes_wss() {
        assert_eq!(
            listen_ws_url_for("https://listen.example/").unwrap(),
            "wss://listen.example/listen"
        );
    }

    #[test]
    fn origin_without_scheme_is_rejected() {
        assert!(listen_ws_url_for("listen.example").is_err());
    }
}
