//! 真听译模型的下载与就绪检查。
//! 国内优先（hf-mirror.com 直连），失败再 HuggingFace（reqwest 自动拾取 HTTPS_PROXY）。
//! 模型下到 app_data/models/，不进 git；已存在的文件跳过（断点续装按文件粒度）。

use std::path::Path;

pub struct ModelFile {
    /// 按序尝试的 URL（先国内镜像，再官方）
    pub urls: &'static [&'static str],
    /// 相对 models 目录的落盘路径
    pub rel: &'static str,
}

// 国内用户优先：模型直链全部把国内镜像排在前面，失败自动落官方源。
const HF_MIRROR: &str = "https://hf-mirror.com"; // HuggingFace 国内镜像，直连
const HF: &str = "https://huggingface.co"; // 官方，走 HTTPS_PROXY（若有）
const SENSE_VOICE_REPO: &str = "csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17";
// VAD 官方在 GitHub release：国内用 ghfast / gh-proxy 加速，再落官方
const VAD_URLS: &[&str] = &[
    "https://ghfast.top/https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx",
    "https://gh-proxy.com/https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx",
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx",
];

fn hf_file(repo: &str, file: &str, rel: String) -> ModelFile {
    ModelFile {
        urls: Box::leak(
            vec![
                format!("{HF_MIRROR}/{repo}/resolve/main/{file}").leak() as &str,
                format!("{HF}/{repo}/resolve/main/{file}").leak() as &str,
            ]
            .into_boxed_slice(),
        ),
        rel: rel.leak(),
    }
}

/// tokenizer 仍用 Xenova 的 json（与 CT2 词表逐 id 对齐）；权重走 CT2 int8。
fn pair_files(pair: &str, ct2_repo: &str) -> Vec<ModelFile> {
    vec![
        hf_file(
            &format!("Xenova/{pair}"),
            "tokenizer.json",
            format!("{pair}/tokenizer.json"),
        ),
        hf_file(ct2_repo, "config.json", format!("{pair}-ct2/config.json")),
        hf_file(ct2_repo, "model.bin", format!("{pair}-ct2/model.bin")),
        hf_file(
            ct2_repo,
            "shared_vocabulary.json",
            format!("{pair}-ct2/shared_vocabulary.json"),
        ),
    ]
}

/// 必需集：识别 + VAD + 英→中 CT2（英语直播开听的底线）。ja/ko 是增量，
/// 下载失败不阻塞开听（引擎按对降级：日语先出原文，译文等模型到位）。
pub fn required() -> Vec<ModelFile> {
    manifest()
        .into_iter()
        .filter(|f| !f.rel.starts_with("opus-ja-en") && !f.rel.starts_with("opus-ko-en"))
        .collect()
}

/// 全量清单：识别 + VAD + 三对 CT2（ja/ko 经 en 转 zh）
pub fn manifest() -> Vec<ModelFile> {
    let sv = |file: &str| hf_file(SENSE_VOICE_REPO, file, format!("sense-voice/{file}"));
    let mut files = vec![
        sv("model.int8.onnx"),
        sv("tokens.txt"),
        ModelFile {
            urls: VAD_URLS,
            rel: "vad/silero_vad.onnx",
        },
    ];
    files.extend(pair_files("opus-en-zh", "jiangzhuo9357/opus-mt-en-zh-ct2"));
    files.extend(pair_files("opus-ja-en", "jiangzhuo9357/opus-mt-ja-en-ct2"));
    files.extend(pair_files("opus-ko-en", "jiangzhuo9357/opus-mt-ko-en-ct2"));
    files
}

pub fn all_present(models_dir: &Path) -> bool {
    required().iter().all(|f| models_dir.join(f.rel).is_file())
}

/// 逐文件下载；progress 上报 0..1（按全部字节数，已存在文件计入基数）。
pub async fn download_all<F>(models_dir: &Path, mut progress: F) -> Result<(), String>
where
    F: FnMut(f64) + Send,
{
    let client = reqwest::Client::builder()
        .user_agent(concat!(
            "live-translator-desktop/",
            env!("CARGO_PKG_VERSION")
        ))
        // 模型文件几百 MB：不限总时长，但连接 30s / 单次读 60s 到点即断，
        // 否则镜像源 stalled 后下载会永远挂在 Downloading（reqwest 默认无超时）
        .connect_timeout(std::time::Duration::from_secs(30))
        .read_timeout(std::time::Duration::from_secs(60))
        .build()
        .map_err(|e| e.to_string())?;

    let files = manifest();
    let mut total: u64 = 0;
    let mut done: u64 = 0;
    let mut pending: Vec<&ModelFile> = Vec::new();
    for f in &files {
        let target = models_dir.join(f.rel);
        if target.is_file() {
            let len = target.metadata().map(|m| m.len()).unwrap_or(0);
            total += len;
            done += len;
        } else {
            pending.push(f);
        }
    }

    for f in &pending {
        // 每个文件先 HEAD 拿大小，拿不到就先估 0、下载中按实际字节计
        let mut size = 0u64;
        let mut chosen: Option<&'static str> = None;
        for url in f.urls {
            if let Ok(resp) = client.head(*url).send().await {
                if resp.status().is_success() {
                    size = resp.content_length().unwrap_or(0);
                    chosen = Some(url);
                    break;
                }
            }
        }
        let url = chosen
            .ok_or_else(|| format!("{}：所有源都不可达（可能被限流，稍后重开会续装）", f.rel))?;
        total += size;
        download_one(
            &client,
            url,
            &models_dir.join(f.rel),
            &mut done,
            total,
            &mut progress,
        )
        .await?;
    }
    // 尺寸健全性：json/onnx 不可能小于 100 字节——限流期的错误体按坏文件处理
    for f in manifest() {
        let target = models_dir.join(f.rel);
        if let Ok(meta) = std::fs::metadata(&target) {
            if meta.len() < 100 {
                let _ = std::fs::remove_file(&target);
            }
        }
    }
    if !all_present(models_dir) {
        return Err("部分文件校验失败，下次打开会续装".into());
    }
    progress(1.0);
    Ok(())
}

async fn download_one<F>(
    client: &reqwest::Client,
    url: &'static str,
    target: &std::path::PathBuf,
    done: &mut u64,
    total: u64,
    progress: &mut F,
) -> Result<(), String>
where
    F: FnMut(f64) + Send,
{
    let mut resp = client
        .get(url)
        .send()
        .await
        .map_err(|e| format!("{url} 请求失败：{e}"))?;
    if !resp.status().is_success() {
        return Err(format!("{url} 返回 {}", resp.status()));
    }
    if let Some(parent) = target.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let tmp = target.with_extension("part");
    let mut file = std::fs::File::create(&tmp).map_err(|e| format!("写盘失败：{e}"))?;
    use std::io::Write;
    while let Some(chunk) = resp.chunk().await.map_err(|e| format!("{url} 中断：{e}"))? {
        file.write_all(&chunk)
            .map_err(|e| format!("写盘失败：{e}"))?;
        *done += chunk.len() as u64;
        let pct = if total > 0 {
            *done as f64 / total as f64
        } else {
            0.0
        };
        progress(pct.min(0.99));
    }
    drop(file);
    std::fs::rename(&tmp, target).map_err(|e| format!("落盘失败：{e}"))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    #[test]
    fn required_is_sensevoice_vad_and_en_ct2() {
        let rels: Vec<&str> = super::required().into_iter().map(|f| f.rel).collect();
        assert!(rels.contains(&"sense-voice/model.int8.onnx"));
        assert!(rels.contains(&"vad/silero_vad.onnx"));
        assert!(rels.contains(&"opus-en-zh/tokenizer.json"));
        assert!(rels.contains(&"opus-en-zh-ct2/model.bin"));
        assert!(!rels.iter().any(|r| r.contains("onnx/encoder")));
        assert!(!rels.iter().any(|r| r.starts_with("opus-ja")));
        assert!(!rels.iter().any(|r| r.starts_with("opus-ko")));
    }

    #[test]
    fn manifest_includes_ja_ko_ct2_not_onnx_weights() {
        let rels: Vec<&str> = super::manifest().into_iter().map(|f| f.rel).collect();
        assert!(rels.contains(&"opus-ja-en-ct2/model.bin"));
        assert!(rels.contains(&"opus-ko-en-ct2/model.bin"));
        assert!(!rels.iter().any(|r| r.contains("/onnx/")));
    }
}
