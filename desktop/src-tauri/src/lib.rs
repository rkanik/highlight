use serde_json::{json, Value};
use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};
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

#[tauri::command]
fn run_job(app: AppHandle, state: State<AppState>, job: Value) -> Result<(), String> {
    let root = state.repo_root.clone();
    let job_path = root.join("work").join("_ui_job.json");
    if let Some(parent) = job_path.parent() {
        std::fs::create_dir_all(parent).ok();
    }
    std::fs::write(&job_path, serde_json::to_vec_pretty(&job).map_err(|e| e.to_string())?)
        .map_err(|e| e.to_string())?;

    let mut cmd = engine_command(&root);
    cmd.arg("run").arg("--job").arg(&job_path);
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
            cancel_job
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
