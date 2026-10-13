use serde_json::{json, Value};
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};
use tauri::{AppHandle, Emitter, State};

struct AppState {
    repo_root: PathBuf,
    child: Arc<Mutex<Option<u32>>>,
}

fn repo_root() -> PathBuf {
    // Dev: desktop/src-tauri → repo root is ../../
    let mut p = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    p.pop(); // desktop
    p.pop(); // repo
    p
}

fn find_binary(root: &PathBuf, stem: &str) -> Option<PathBuf> {
    let bin_dir = root.join("desktop/src-tauri/binaries");
    let candidates = [
        bin_dir.join(format!("{stem}-x86_64-pc-windows-msvc.exe")),
        bin_dir.join(format!("{stem}.exe")),
        bin_dir.join(format!("{stem}-x86_64-unknown-linux-gnu")),
        bin_dir.join(format!("{stem}-aarch64-apple-darwin")),
        bin_dir.join(format!("{stem}-x86_64-apple-darwin")),
        bin_dir.join(stem),
    ];
    candidates.into_iter().find(|p| p.exists())
}

fn engine_command(root: &PathBuf) -> Command {
    // Dev (`tauri dev` / debug): always use live Python so engine/ edits apply.
    // Release builds use the frozen sidecar when present.
    // Override anytime: HIGHLIGHT_USE_PYTHON=1 or HIGHLIGHT_USE_SIDECAR=1
    let force_python = std::env::var("HIGHLIGHT_USE_PYTHON").ok().as_deref() == Some("1")
        || (cfg!(debug_assertions)
            && std::env::var("HIGHLIGHT_USE_SIDECAR").ok().as_deref() != Some("1"));

    if !force_python {
        if let Some(sidecar) = find_binary(root, "highlight-engine") {
            let mut c = Command::new(sidecar);
            if let Some(ff) = find_binary(root, "ffmpeg") {
                c.env("HIGHLIGHT_FFMPEG", &ff);
            }
            if let Some(fp) = find_binary(root, "ffprobe") {
                c.env("HIGHLIGHT_FFPROBE", &fp);
            }
            c.env("HIGHLIGHT_RESOURCES", root);
            return c;
        }
    }
    let mut c = Command::new("python");
    c.arg("-m").arg("engine");
    c.current_dir(root);
    c.env("PYTHONPATH", root);
    c.env("HIGHLIGHT_RESOURCES", root);
    c
}

#[tauri::command]
fn list_games(state: State<AppState>) -> Result<Value, String> {
    let mut cmd = engine_command(&state.repo_root);
    cmd.arg("games");
    let out = cmd.output().map_err(|e| e.to_string())?;
    if !out.status.success() {
        return Err(String::from_utf8_lossy(&out.stderr).to_string());
    }
    serde_json::from_slice(&out.stdout).map_err(|e| e.to_string())
}

#[tauri::command]
fn probe_devices(state: State<AppState>) -> Result<Value, String> {
    let mut cmd = engine_command(&state.repo_root);
    cmd.arg("devices");
    let out = cmd.output().map_err(|e| e.to_string())?;
    if !out.status.success() {
        // Soft fallback so UI still loads
        return Ok(json!({
            "gpus": [],
            "cpu": {"id":"cpu","index":-1,"name":"CPU","vendor":"cpu","type":"cpu","encoders":["libx264"],"decode":["cpu"]},
            "ffmpeg_encoders": ["libx264", "h264_nvenc"],
            "hwaccels": ["cuda", "cpu"],
            "auto_select": "cpu",
            "recommend_encode": "h264_nvenc",
            "recommend_decode": "cuda"
        }));
    }
    serde_json::from_slice(&out.stdout).map_err(|e| e.to_string())
}

#[tauri::command]
fn probe_video(state: State<AppState>, path: String) -> Result<Value, String> {
    let mut cmd = engine_command(&state.repo_root);
    cmd.arg("probe").arg(&path);
    let out = cmd.output().map_err(|e| e.to_string())?;
    if !out.status.success() {
        let err = String::from_utf8_lossy(&out.stderr);
        let stdout = String::from_utf8_lossy(&out.stdout);
        return Err(if !err.trim().is_empty() {
            err.to_string()
        } else {
            stdout.to_string()
        });
    }
    serde_json::from_slice(&out.stdout).map_err(|e| e.to_string())
}

fn user_dir() -> PathBuf {
    if let Ok(appdata) = std::env::var("APPDATA") {
        return PathBuf::from(appdata).join("HighlightApp");
    }
    let home = std::env::var("HOME").unwrap_or_else(|_| ".".into());
    PathBuf::from(home).join(".highlight")
}

fn ui_config_path() -> PathBuf {
    user_dir().join("ui_settings.json")
}

fn unix_mtime(p: &Path) -> u64 {
    std::fs::metadata(p)
        .and_then(|m| m.modified())
        .ok()
        .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

fn path_str(p: &Path) -> String {
    p.to_string_lossy().to_string()
}

/// One `out/{title}/` package → JSON for the library.
fn read_output_dir(dir: &Path) -> Option<Value> {
    let name = dir.file_name()?.to_string_lossy().to_string();
    let mut files: Vec<PathBuf> = std::fs::read_dir(dir)
        .ok()?
        .flatten()
        .map(|e| e.path())
        .filter(|p| p.is_file())
        .collect();
    files.sort();
    let file_name = |p: &PathBuf| p.file_name().map(|s| s.to_string_lossy().to_string()).unwrap_or_default();
    let ends = |p: &PathBuf, suffix: &str| file_name(p).to_lowercase().ends_with(suffix);
    let pick = |exact: &str, pred: &dyn Fn(&PathBuf) -> bool| -> Option<PathBuf> {
        files
            .iter()
            .find(|p| file_name(p) == exact)
            .or_else(|| files.iter().find(|p| pred(p)))
            .cloned()
    };

    let meta_path = pick(&format!("{name}.meta.json"), &|p| ends(p, ".meta.json"));
    let mp4 = pick(&format!("{name}.mp4"), &|p| ends(p, ".mp4"));
    if meta_path.is_none() && mp4.is_none() {
        return None;
    }
    let thumb = pick(&format!("{name}.jpg"), &|p| {
        ends(p, ".jpg") && !ends(p, " yt.jpg") && !file_name(p).starts_with('_')
    });
    let thumb_yt = pick(&format!("{name} YT.jpg"), &|p| ends(p, " yt.jpg"));
    let txt = pick(&format!("{name}.txt"), &|p| ends(p, ".txt"));

    let meta: Value = meta_path
        .as_ref()
        .and_then(|p| std::fs::read(p).ok())
        .and_then(|b| serde_json::from_slice(&b).ok())
        .unwrap_or(Value::Null);
    let description = txt
        .as_ref()
        .and_then(|p| std::fs::read_to_string(p).ok())
        .unwrap_or_default();
    let title = meta
        .get("title")
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
        .unwrap_or_else(|| name.clone());
    let modified = mp4.as_ref().map(|p| unix_mtime(p)).unwrap_or_else(|| unix_mtime(dir));

    Some(json!({
        "dir": path_str(dir),
        "name": name,
        "title": title,
        "mp4": mp4.as_ref().map(|p| path_str(p)),
        "thumb": thumb.as_ref().map(|p| path_str(p)),
        "thumb_yt": thumb_yt.as_ref().map(|p| path_str(p)),
        "description": description,
        "meta": meta,
        "modified": modified,
    }))
}

#[tauri::command]
fn app_info(state: State<AppState>) -> Value {
    let root = &state.repo_root;
    json!({
        "repo_root": path_str(root),
        "output_dir": path_str(&root.join("out")),
        "work_dir": path_str(&root.join("work")),
        "brand_defaults": {
            "character": path_str(&root.join("augniik-character.png")),
            "cover": path_str(&root.join("augniik-cover.jpg")),
            "profile": path_str(&root.join("augniik-profile.jpg")),
        },
    })
}

#[tauri::command]
fn list_outputs(state: State<AppState>, output_dir: Option<String>) -> Result<Vec<Value>, String> {
    let dir = output_dir
        .filter(|s| !s.trim().is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| state.repo_root.join("out"));
    if !dir.exists() {
        return Ok(vec![]);
    }
    let mut items: Vec<Value> = std::fs::read_dir(&dir)
        .map_err(|e| e.to_string())?
        .flatten()
        .map(|e| e.path())
        .filter(|p| p.is_dir())
        .filter_map(|p| read_output_dir(&p))
        .collect();
    items.sort_by_key(|v| std::cmp::Reverse(v.get("modified").and_then(|m| m.as_u64()).unwrap_or(0)));
    Ok(items)
}

#[tauri::command]
fn read_output(dir: String) -> Result<Value, String> {
    read_output_dir(Path::new(&dir)).ok_or_else(|| format!("not an output package: {dir}"))
}

#[tauri::command]
fn get_user_config() -> Result<Value, String> {
    let p = ui_config_path();
    if !p.exists() {
        return Ok(Value::Null);
    }
    let bytes = std::fs::read(&p).map_err(|e| e.to_string())?;
    serde_json::from_slice(&bytes).map_err(|e| e.to_string())
}

#[tauri::command]
fn save_user_config(config: Value) -> Result<(), String> {
    let p = ui_config_path();
    if let Some(parent) = p.parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    let tmp = p.with_extension("json.tmp");
    std::fs::write(&tmp, serde_json::to_vec_pretty(&config).map_err(|e| e.to_string())?)
        .map_err(|e| e.to_string())?;
    std::fs::rename(&tmp, &p).map_err(|e| e.to_string())
}

#[tauri::command]
fn copy_brand_asset(src: String, kind: String) -> Result<String, String> {
    if !["character", "cover", "profile"].contains(&kind.as_str()) {
        return Err(format!("unknown brand asset: {kind}"));
    }
    let src = PathBuf::from(src);
    let ext = src
        .extension()
        .map(|e| e.to_string_lossy().to_lowercase())
        .filter(|e| ["png", "jpg", "jpeg", "webp"].contains(&e.as_str()))
        .ok_or("brand asset must be png / jpg / webp")?;
    let dir = user_dir().join("brand");
    std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let prefix = format!("{kind}_");
    if let Ok(entries) = std::fs::read_dir(&dir) {
        for e in entries.flatten() {
            if e.file_name().to_string_lossy().starts_with(&prefix) {
                let _ = std::fs::remove_file(e.path());
            }
        }
    }
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let dest = dir.join(format!("{kind}_{stamp}.{ext}"));
    std::fs::copy(&src, &dest).map_err(|e| e.to_string())?;
    Ok(path_str(&dest))
}

#[tauri::command]
fn run_job(app: AppHandle, state: State<AppState>, job: Value) -> Result<(), String> {
    let root = state.repo_root.clone();
    let mut job = job;
    let mode = match job.get("mode").and_then(|m| m.as_str()) {
        Some("scan") => "scan",
        Some("assemble") => "assemble",
        _ => "run",
    };
    // Keep API keys out of the job file on disk; the engine reads HIGHLIGHT_AI_KEY.
    let ai_key = job
        .pointer_mut("/ai/provider")
        .and_then(|p| p.as_object_mut())
        .and_then(|p| p.remove("api_key"))
        .and_then(|k| k.as_str().map(|s| s.to_string()));

    let job_path = root.join("work").join("_ui_job.json");
    if let Some(parent) = job_path.parent() {
        std::fs::create_dir_all(parent).ok();
    }
    std::fs::write(&job_path, serde_json::to_vec_pretty(&job).map_err(|e| e.to_string())?)
        .map_err(|e| e.to_string())?;

    let mut cmd = engine_command(&root);
    cmd.arg(mode).arg("--job").arg(&job_path);
    match ai_key {
        Some(k) if !k.is_empty() => {
            cmd.env("HIGHLIGHT_AI_KEY", k);
        }
        _ => {
            cmd.env_remove("HIGHLIGHT_AI_KEY");
        }
    }
    cmd.stdout(Stdio::piped());
    cmd.stderr(Stdio::piped());
    cmd.env("HIGHLIGHT_RESOURCES", &root);
    cmd.env("PYTHONPATH", &root);

    let mut child = cmd.spawn().map_err(|e| format!("spawn engine: {e}"))?;
    if let Ok(mut slot) = state.child.lock() {
        *slot = child.id().into();
    }

    let stdout = child.stdout.take().ok_or("no stdout")?;
    let stderr = child.stderr.take().ok_or("no stderr")?;
    let app_out = app.clone();
    let app_err = app.clone();
    let child_pid = child.id();
    let child_slot = state.child.clone();
    std::thread::spawn(move || {
        let reader = BufReader::new(stdout);
        for line in reader.lines().flatten() {
            let payload = serde_json::from_str::<Value>(&line).unwrap_or_else(|_| {
                json!({ "stage": "log", "message": line })
            });
            let _ = app_out.emit("engine-progress", payload);
        }
        let status = child.wait();
        let ok = status.map(|s| s.success()).unwrap_or(false);
        if let Ok(mut slot) = child_slot.lock() {
            if *slot == Some(child_pid) {
                *slot = None;
            }
        }
        let _ = app_out.emit(
            "engine-progress",
            json!({ "done": true, "ok": ok }),
        );
    });
    std::thread::spawn(move || {
        let reader = BufReader::new(stderr);
        for line in reader.lines().flatten() {
            if line.trim().is_empty() {
                continue;
            }
            let _ = app_err.emit(
                "engine-progress",
                json!({ "stage": "log", "message": format!("stderr: {line}") }),
            );
        }
    });

    Ok(())
}

#[tauri::command]
fn open_path(path: String) -> Result<(), String> {
    #[cfg(target_os = "windows")]
    {
        Command::new("explorer").arg(&path).spawn().map_err(|e| e.to_string())?;
    }
    #[cfg(target_os = "macos")]
    {
        Command::new("open").arg(&path).spawn().map_err(|e| e.to_string())?;
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        Command::new("xdg-open").arg(&path).spawn().map_err(|e| e.to_string())?;
    }
    Ok(())
}

#[tauri::command]
fn cancel_job(app: AppHandle, state: State<AppState>) -> Result<(), String> {
    let pid = {
        let mut slot = state.child.lock().map_err(|e| e.to_string())?;
        slot.take()
    };
    if let Some(pid) = pid {
        #[cfg(target_os = "windows")]
        {
            // /T kills python + child ffmpeg tree
            let _ = Command::new("taskkill")
                .args(["/PID", &pid.to_string(), "/T", "/F"])
                .status();
        }
        #[cfg(unix)]
        {
            let _ = Command::new("kill").args(["-TERM", &pid.to_string()]).status();
            // also try process group if engine spawned ffmpeg
            let _ = Command::new("pkill").args(["-P", &pid.to_string()]).status();
        }
        let _ = app.emit(
            "engine-progress",
            json!({
                "stage": "log",
                "message": "Stopping… killed engine + ffmpeg",
                "cancelled": true,
            }),
        );
        let _ = app.emit(
            "engine-progress",
            json!({ "done": true, "ok": false, "cancelled": true, "message": "Stopped by user" }),
        );
    }
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_shell::init())
        .manage(AppState {
            repo_root: repo_root(),
            child: Arc::new(Mutex::new(None)),
        })
        .invoke_handler(tauri::generate_handler![
            list_games,
            probe_devices,
            probe_video,
            run_job,
            open_path,
            cancel_job,
            app_info,
            list_outputs,
            read_output,
            get_user_config,
            save_user_config,
            copy_brand_asset
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
