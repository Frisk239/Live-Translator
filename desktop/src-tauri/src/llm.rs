//! 定稿大模型配置：单独落在 app_data，不进设置广播，避免密钥进字幕窗。

use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", default)]
pub struct LlmConfig {
    pub enabled: bool,
    #[serde(alias = "base_url")]
    pub base_url: String,
    pub model: String,
    #[serde(alias = "api_key")]
    pub api_key: String,
    pub thinking: String,
    #[serde(alias = "thinking_param")]
    pub thinking_param: String,
    #[serde(alias = "timeout_s")]
    pub timeout_s: f64,
    #[serde(alias = "max_tokens")]
    pub max_tokens: u32,
}

impl Default for LlmConfig {
    fn default() -> Self {
        Self {
            enabled: false,
            base_url: "https://opencode.ai/zen/go/v1".into(),
            model: "deepseek-v4-flash".into(),
            api_key: String::new(),
            thinking: String::new(),
            thinking_param: String::new(),
            timeout_s: 20.0,
            max_tokens: 256,
        }
    }
}

pub fn config_path(models_dir: &Path) -> PathBuf {
    models_dir
        .parent()
        .map(|p| p.join("llm.local.json"))
        .unwrap_or_else(|| models_dir.join("llm.local.json"))
}

pub fn load(models_dir: &Path) -> LlmConfig {
    let path = config_path(models_dir);
    std::fs::read_to_string(&path)
        .ok()
        // Windows 记事本等工具会写 UTF-8 BOM；serde_json 不认，先剥掉再解析。
        .map(|s| s.strip_prefix('\u{feff}').unwrap_or(&s).to_string())
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default()
}

pub fn save(models_dir: &Path, cfg: &LlmConfig) -> Result<(), String> {
    let path = config_path(models_dir);
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir).map_err(|e| format!("写配置目录失败：{e}"))?;
    }
    let text = serde_json::to_string_pretty(cfg).map_err(|e| format!("序列化失败：{e}"))?;
    std::fs::write(&path, text).map_err(|e| format!("写配置失败：{e}"))
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LlmProbe {
    pub ok: bool,
    pub ms: u32,
    pub preview: String,
}

pub async fn probe(cfg: &LlmConfig) -> LlmProbe {
    let started = std::time::Instant::now();
    if cfg.base_url.trim().is_empty() || cfg.model.trim().is_empty() || cfg.api_key.trim().is_empty()
    {
        return LlmProbe {
            ok: false,
            ms: 0,
            preview: "先填接口地址、模型和密钥。".into(),
        };
    }
    let url = format!("{}/chat/completions", cfg.base_url.trim_end_matches('/'));
    // 试连只求一个 pong：单档超时压到 10s，别让观众为冷连接干等一整档 20s。
    let probe_timeout_s = cfg.timeout_s.clamp(3.0, 10.0);
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(probe_timeout_s as u64))
        .build();
    let Ok(client) = client else {
        return LlmProbe {
            ok: false,
            ms: 0,
            preview: "建连失败。".into(),
        };
    };
    let extras = [
        serde_json::json!({"thinking": "off"}),
        serde_json::json!({"reasoning_effort": "no_think"}),
        serde_json::json!({}),
    ];
    let mut last = LlmProbe {
        ok: false,
        ms: 0,
        preview: "连不上。".into(),
    };
    for extra in extras {
        let mut body = serde_json::json!({
            "model": cfg.model,
            "messages": [{"role": "user", "content": "ping, reply with the single word pong"}],
            "max_tokens": cfg.max_tokens.max(32),
            "temperature": 0.2,
        });
        if let Some(obj) = extra.as_object() {
            for (k, v) in obj {
                body[k] = v.clone();
            }
        }
        let send = client
            .post(&url)
            .header("Content-Type", "application/json")
            .header("Authorization", format!("Bearer {}", cfg.api_key))
            .header("User-Agent", "livetranslator/0.1")
            .body(body.to_string())
            .send()
            .await;
        let ms = started.elapsed().as_millis() as u32;
        last = match send {
            Ok(resp) => {
                let status = resp.status();
                let text = resp.text().await.unwrap_or_default();
                if !status.is_success() {
                    LlmProbe {
                        ok: false,
                        ms,
                        preview: format!("接口 {}：{}", status.as_u16(), text.chars().take(80).collect::<String>()),
                    }
                } else {
                    let preview = serde_json::from_str::<serde_json::Value>(&text)
                        .ok()
                        .and_then(|v| {
                            v["choices"][0]["message"]["content"]
                                .as_str()
                                .map(|s| s.trim().to_string())
                        })
                        .unwrap_or_default();
                    if preview.is_empty() {
                        LlmProbe {
                            ok: false,
                            ms,
                            preview: "连上了，但没有返回内容。".into(),
                        }
                    } else {
                        return LlmProbe {
                            ok: true,
                            ms,
                            preview: preview.chars().take(80).collect(),
                        };
                    }
                }
            }
            Err(e) => LlmProbe {
                ok: false,
                ms,
                preview: format!("连不上：{e}"),
            },
        };
    }
    last
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LlmModelList {
    pub ok: bool,
    pub preview: String,
    pub models: Vec<String>,
}

pub fn parse_model_ids(value: &serde_json::Value) -> Vec<String> {
    let rows = value
        .get("data")
        .or_else(|| value.get("models"))
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();
    let mut out = Vec::new();
    for row in rows {
        let id = if let Some(s) = row.as_str() {
            s.to_string()
        } else {
            row.get("id")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string()
        };
        let id = id.trim().to_string();
        if id.is_empty() || !is_chat_model(&id) {
            continue;
        }
        if !out.iter().any(|x| x == &id) {
            out.push(id);
        }
        if out.len() >= 120 {
            break;
        }
    }
    out
}

fn is_chat_model(id: &str) -> bool {
    let l = id.to_ascii_lowercase();
    !(l.contains("embed")
        || l.contains("whisper")
        || l.contains("tts")
        || l.contains("dall-e")
        || l.contains("dalle")
        || l.contains("moderation"))
}

pub async fn list_models(cfg: &LlmConfig) -> LlmModelList {
    if cfg.base_url.trim().is_empty() || cfg.api_key.trim().is_empty() {
        return LlmModelList {
            ok: false,
            preview: "先填接口地址和密钥。".into(),
            models: vec![],
        };
    }
    let url = format!("{}/models", cfg.base_url.trim_end_matches('/'));
    let client = match reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(8))
        .build()
    {
        Ok(c) => c,
        Err(_) => {
            return LlmModelList {
                ok: false,
                preview: "建连失败。".into(),
                models: vec![],
            };
        }
    };
    let send = client
        .get(&url)
        .header("Authorization", format!("Bearer {}", cfg.api_key))
        .header("User-Agent", "livetranslator/0.1")
        .send()
        .await;
    match send {
        Ok(resp) => {
            let status = resp.status();
            let text = resp.text().await.unwrap_or_default();
            if !status.is_success() {
                return LlmModelList {
                    ok: false,
                    preview: format!(
                        "接口 {}：{}",
                        status.as_u16(),
                        text.chars().take(80).collect::<String>()
                    ),
                    models: vec![],
                };
            }
            let ids = serde_json::from_str::<serde_json::Value>(&text)
                .ok()
                .map(|v| parse_model_ids(&v))
                .unwrap_or_default();
            if ids.is_empty() {
                LlmModelList {
                    ok: false,
                    preview: "这个接口不给模型列表，请手写 Model ID。".into(),
                    models: vec![],
                }
            } else {
                LlmModelList {
                    ok: true,
                    preview: format!("找到 {} 个模型。", ids.len()),
                    models: ids,
                }
            }
        }
        Err(e) => LlmModelList {
            ok: false,
            preview: format!("拉不到模型：{e}"),
            models: vec![],
        },
    }
}

#[derive(Serialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct LlmThinkOption {
    pub id: String,
    pub label: String,
    pub param: String,
    pub value: String,
    pub ok: bool,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LlmThinkProbe {
    pub ok: bool,
    pub preview: String,
    pub options: Vec<LlmThinkOption>,
    /// 自动挑好的最低思考档（参数名 + 值）；没挑出来就靠引擎的通用默认
    pub recommended: Option<LlmThinkOption>,
}

fn thinking_param_names(params: &[String]) -> Vec<String> {
    params
        .iter()
        .filter(|p| {
            let l = p.to_ascii_lowercase();
            l.contains("think") || l.contains("reason")
        })
        .cloned()
        .collect()
}

fn split_enum_tokens(s: &str) -> Vec<String> {
    s.split(|c: char| c == ',' || c == '|' || c == '/')
        .map(|t| {
            t.trim()
                .trim_matches(|c: char| {
                    c == '\'' || c == '"' || c == '`' || c == ':' || c == '[' || c == ']'
                })
                .to_string()
        })
        .filter(|t| {
            !t.is_empty()
                && t.len() < 32
                && t.chars()
                    .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
        })
        .collect()
}

fn looks_like_enum_token(s: &str) -> bool {
    let l = s.to_ascii_lowercase();
    !s.is_empty()
        && s.len() < 24
        && !l.contains("error")
        && !l.contains("invalid")
        && s.chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
        && !["code", "type", "param", "message", "index", "null"].contains(&l.as_str())
}

pub fn extract_enum_values(text: &str) -> Vec<String> {
    let lower = text.to_ascii_lowercase();
    let mut out = Vec::new();
    for marker in ["one of", "valid values", "allowed values"] {
        if let Some(i) = lower.find(marker) {
            let rest = text[i + marker.len()..].trim_start_matches([':', ' ', '-']);
            let rest = rest
                .split(|c: char| c == '.' || c == ';' || c == '\n' || c == '}')
                .next()
                .unwrap_or(rest);
            out.extend(split_enum_tokens(rest));
            if !out.is_empty() {
                break;
            }
        }
    }
    if out.is_empty() {
        if let Some(start) = text.find('[') {
            if let Some(end) = text[start + 1..].find(']') {
                let inner = &text[start + 1..start + 1 + end];
                if inner.contains('\'') || inner.contains('"') || inner.contains(',') {
                    out.extend(split_enum_tokens(inner));
                }
            }
        }
    }
    let mut seen = Vec::new();
    for v in out {
        if v == "__lt_probe__" || !looks_like_enum_token(&v) {
            continue;
        }
        if !seen.iter().any(|x| x == &v) {
            seen.push(v);
        }
    }
    seen
}

fn model_supported_params(root: &serde_json::Value, model: &str) -> Vec<String> {
    let rows = root
        .get("data")
        .or_else(|| root.get("models"))
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();
    for row in rows {
        let id = row.get("id").and_then(|v| v.as_str()).unwrap_or("");
        if id != model {
            continue;
        }
        let mut params = Vec::new();
        if let Some(arr) = row.get("supported_parameters").and_then(|v| v.as_array()) {
            for p in arr {
                if let Some(s) = p.as_str() {
                    params.push(s.to_string());
                }
            }
        }
        if let Some(obj) = row.get("default_parameters").and_then(|v| v.as_object()) {
            for k in obj.keys() {
                if !params.iter().any(|x| x == k) {
                    params.push(k.clone());
                }
            }
        }
        return params;
    }
    vec![]
}

fn think_option(param: &str, value: &str) -> LlmThinkOption {
    if param.is_empty() {
        return LlmThinkOption {
            id: "plain".into(),
            label: "不发送思考字段".into(),
            param: String::new(),
            value: String::new(),
            ok: true,
        };
    }
    LlmThinkOption {
        id: format!("{param}:{value}"),
        label: format!("{param} · {value}"),
        param: param.to_string(),
        value: value.to_string(),
        ok: true,
    }
}

/// 从探测结果里挑「思考最少」的一档：先找完全关闭的值，退而求其次最低档；
/// 都没有就返回 None，交给引擎按最通用的参数试最低档（400 兜底去掉）。
pub fn pick_lowest_think(options: &[LlmThinkOption]) -> Option<LlmThinkOption> {
    const OFF: [&str; 8] = [
        "none", "no", "no_think", "off", "minimal", "disable", "disabled", "false",
    ];
    const LOW: [&str; 2] = ["low", "lite"];
    let mut low: Option<LlmThinkOption> = None;
    for o in options {
        if o.param.is_empty() {
            continue; // 「不发送」那一项不算档位
        }
        let v = o.value.to_ascii_lowercase();
        if OFF.contains(&v.as_str()) {
            return Some(o.clone());
        }
        if LOW.contains(&v.as_str()) && low.is_none() {
            low = Some(o.clone());
        }
    }
    low
}

async fn chat_error_or_ok(
    client: &reqwest::Client,
    url: &str,
    cfg: &LlmConfig,
    extra: serde_json::Value,
) -> Result<String, String> {
    let mut body = serde_json::json!({
        "model": cfg.model,
        "messages": [{"role": "user", "content": "ping, reply with the single word pong"}],
        "max_tokens": 32,
        "temperature": 0.0,
    });
    if let Some(obj) = extra.as_object() {
        for (k, v) in obj {
            body[k] = v.clone();
        }
    }
    let resp = client
        .post(url)
        .header("Content-Type", "application/json")
        .header("Authorization", format!("Bearer {}", cfg.api_key))
        .header("User-Agent", "livetranslator/0.1")
        .body(body.to_string())
        .send()
        .await
        .map_err(|e| e.to_string())?;
    let status = resp.status();
    let text = resp.text().await.unwrap_or_default();
    if status.is_success() {
        Ok(String::new())
    } else {
        Err(text)
    }
}

pub async fn probe_thinking(cfg: &LlmConfig) -> LlmThinkProbe {
    if cfg.base_url.trim().is_empty() || cfg.model.trim().is_empty() || cfg.api_key.trim().is_empty()
    {
        return LlmThinkProbe {
            ok: false,
            preview: "先填接口地址、模型和密钥。".into(),
            options: vec![],
            recommended: None,
        };
    }
    let base = cfg.base_url.trim_end_matches('/');
    let client = match reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(8))
        .build()
    {
        Ok(c) => c,
        Err(_) => {
            return LlmThinkProbe {
                ok: false,
                preview: "建连失败。".into(),
                options: vec![],
                recommended: None,
            };
        }
    };
    let mut param_names: Vec<String> = Vec::new();
    let models_url = format!("{base}/models");
    if let Ok(resp) = client
        .get(&models_url)
        .header("Authorization", format!("Bearer {}", cfg.api_key))
        .header("User-Agent", "livetranslator/0.1")
        .send()
        .await
    {
        if resp.status().is_success() {
            if let Ok(text) = resp.text().await {
                if let Ok(v) = serde_json::from_str::<serde_json::Value>(&text) {
                    param_names = thinking_param_names(&model_supported_params(&v, &cfg.model));
                }
            }
        }
    }
    if param_names.is_empty() {
        param_names = vec![
            "thinking".into(),
            "reasoning_effort".into(),
            "enable_thinking".into(),
        ];
    }

    let chat_url = format!("{base}/chat/completions");
    let mut options = vec![think_option("", "")];
    for param in param_names {
        let extra = serde_json::json!({ param.clone(): "__lt_probe__" });
        match chat_error_or_ok(&client, &chat_url, cfg, extra).await {
            Ok(_) => {}
            Err(text) => {
                let mut vals = extract_enum_values(&text);
                if vals.is_empty()
                    && (text.to_ascii_lowercase().contains("boolean")
                        || param == "enable_thinking")
                {
                    vals = vec!["true".into(), "false".into()];
                }
                for val in vals {
                    let opt = think_option(&param, &val);
                    if !options.iter().any(|o| o.id == opt.id) {
                        options.push(opt);
                    }
                }
            }
        }
    }
    let discovered = options.len().saturating_sub(1);
    let recommended = pick_lowest_think(&options);
    if discovered == 0 {
        LlmThinkProbe {
            ok: true,
            preview: "接口没报思考档位，先按通用参数带最低档。".into(),
            options,
            recommended: None,
        }
    } else {
        LlmThinkProbe {
            ok: true,
            preview: format!("从接口发现 {discovered} 档思考，已自动选最低。"),
            options,
            recommended,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn think_opt(param: &str, value: &str) -> LlmThinkOption {
        LlmThinkOption {
            id: format!("{param}:{value}"),
            label: String::new(),
            param: param.into(),
            value: value.into(),
            ok: true,
        }
    }

    #[test]
    fn pick_lowest_think_prefers_off_then_low() {
        let mixed = vec![
            think_opt("", ""), // 「不发送」不算档位
            think_opt("reasoning_effort", "high"),
            think_opt("reasoning_effort", "low"),
            think_opt("enable_thinking", "false"), // 布尔关 = 完全关闭，最优先
        ];
        let picked = pick_lowest_think(&mixed).unwrap();
        assert_eq!((picked.param.as_str(), picked.value.as_str()), ("enable_thinking", "false"));

        let low_only = vec![think_opt("", ""), think_opt("reasoning_effort", "medium"), think_opt("reasoning_effort", "low")];
        assert_eq!(pick_lowest_think(&low_only).unwrap().value, "low");

        assert!(pick_lowest_think(&[think_opt("", "")]).is_none());
        assert!(pick_lowest_think(&[]).is_none());
    }

    fn temp_models_dir(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir()
            .join("lt-llm-tests")
            .join(format!("{}-{}", tag, std::process::id()));
        let models = dir.join("models");
        std::fs::create_dir_all(&models).unwrap();
        models
    }

    #[test]
    fn save_then_load_roundtrips_all_fields() {
        let models = temp_models_dir("roundtrip");
        let cfg = LlmConfig {
            enabled: true,
            base_url: "https://example.com/v1".into(),
            model: "m1".into(),
            api_key: "sk-test".into(),
            thinking: "off".into(),
            thinking_param: String::new(),
            timeout_s: 12.0,
            max_tokens: 128,
        };
        save(&models, &cfg).unwrap();
        let loaded = load(&models);
        assert!(loaded.enabled);
        assert_eq!(loaded.base_url, "https://example.com/v1");
        assert_eq!(loaded.model, "m1");
        assert_eq!(loaded.api_key, "sk-test");
        assert_eq!(loaded.thinking, "off");
        assert_eq!(loaded.timeout_s, 12.0);
        assert_eq!(loaded.max_tokens, 128);
    }

    #[test]
    fn load_tolerates_utf8_bom_and_crlf_from_notepad() {
        let models = temp_models_dir("bom");
        let path = config_path(&models);
        let body = "{\r\n  \"enabled\": true,\r\n  \"baseUrl\": \"https://example.com/v1\",\r\n  \"model\": \"m1\",\r\n  \"apiKey\": \"sk-bom\"\r\n}\r\n";
        std::fs::write(&path, format!("\u{feff}{}", body)).unwrap();
        let loaded = load(&models);
        assert!(loaded.enabled);
        assert_eq!(loaded.base_url, "https://example.com/v1");
        assert_eq!(loaded.api_key, "sk-bom");
    }

    #[test]
    fn load_accepts_snake_case_aliases() {
        let models = temp_models_dir("snake");
        let path = config_path(&models);
        std::fs::write(
            &path,
            r#"{"enabled": true, "base_url": "https://example.com/v1", "model": "m1", "api_key": "sk-snake"}"#,
        )
        .unwrap();
        let loaded = load(&models);
        assert!(loaded.enabled);
        assert_eq!(loaded.base_url, "https://example.com/v1");
        assert_eq!(loaded.api_key, "sk-snake");
    }

    #[test]
    fn load_missing_file_falls_back_to_defaults() {
        let models = temp_models_dir("missing");
        let loaded = load(&models);
        assert!(!loaded.enabled);
        assert_eq!(loaded.model, "deepseek-v4-flash");
        assert_eq!(loaded.base_url, "https://opencode.ai/zen/go/v1");
        assert!(loaded.api_key.is_empty());
    }

    #[test]
    fn parse_model_ids_skips_embeddings_and_dedupes() {
        let v = serde_json::json!({
            "data": [
                {"id": "deepseek-v4-flash"},
                {"id": "text-embedding-3-small"},
                {"id": "deepseek-v4-flash"},
                "hy3",
                {"id": "whisper-1"}
            ]
        });
        assert_eq!(parse_model_ids(&v), vec!["deepseek-v4-flash", "hy3"]);
    }

    #[test]
    fn extract_enum_values_from_provider_errors() {
        let a = extract_enum_values(
            "Invalid reasoning_effort: expected one of ['none', 'low', 'medium', 'high']",
        );
        assert_eq!(a, vec!["none", "low", "medium", "high"]);
        let b = extract_enum_values("thinking must be one of: off, low, high.");
        assert_eq!(b, vec!["off", "low", "high"]);
        let c = extract_enum_values(
            r#"reasoning_effort: Invalid option: expected one of "max"|"xhigh"|"high"|"medium"|"low"|"minimal"|"none""#,
        );
        assert_eq!(c, vec!["max", "xhigh", "high", "medium", "low", "minimal", "none"]);
        let d = extract_enum_values(r#"{"error":{"type":"invalid_request_error","message":"Upstream [invalid_request_error]"}}"#);
        assert!(d.is_empty());
    }
}
