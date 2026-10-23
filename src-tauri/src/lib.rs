use serde::{Deserialize, Serialize};
use std::{collections::{HashMap, HashSet}, fs, io::{BufRead, BufReader}, path::{Path, PathBuf}, process::{Child, Command, Stdio}, sync::Mutex, time::Duration};
use tauri::{
    ipc::Response,
    menu::{Menu, MenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    Emitter, Manager,
};
use tauri_plugin_dialog::{DialogExt, MessageDialogKind};

#[derive(Clone, Serialize, Deserialize)]
struct Session { port: u16, token: String, protocol: u8 }
struct Runtime { process: Mutex<Option<Child>>, session: Session, library: PathBuf }
struct OpenBooks { allowed: HashMap<String, PathBuf>, pending: Vec<String> }

const LIBRARY: &str = "library.sqlite3";

fn book_paths<I, S>(values: I, cwd: &Path) -> Vec<PathBuf> where I: IntoIterator<Item = S>, S: Into<PathBuf> {
    let mut seen = HashSet::new();
    values.into_iter().filter_map(|value| {
        let path: PathBuf = value.into();
        let extension = path.extension().and_then(|item| item.to_str()).unwrap_or("").to_lowercase();
        if extension != "epub" && extension != "fb2" { return None; }
        let path = if path.is_absolute() { path } else { cwd.join(path) };
        let canonical = fs::canonicalize(path).ok()?;
        if !canonical.is_file() { return None; }
        if seen.insert(canonical.clone()) { Some(canonical) } else { None }
    }).collect()
}

fn queue_books(state: &mut OpenBooks, paths: &[PathBuf]) -> Vec<String> {
    paths.iter().map(|path| {
        let name = path.to_string_lossy().into_owned();
        state.allowed.insert(name.clone(), path.clone());
        if !state.pending.contains(&name) { state.pending.push(name.clone()); }
        name
    }).collect()
}

fn admit_books<I, S>(app: &tauri::AppHandle, values: I, cwd: &Path) where I: IntoIterator<Item = S>, S: Into<PathBuf> {
    let paths = book_paths(values, cwd);
    if paths.is_empty() { return; }
    if let Some(state) = app.try_state::<Mutex<OpenBooks>>() {
        let names = match state.lock() { Ok(mut books) => queue_books(&mut books, &paths), Err(_) => return };
        let _ = app.emit("open-books", names);
    }
}

fn reveal(app: &tauri::AppHandle) {
    if app.try_state::<Runtime>().is_none() { return; }
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
    }
}

fn install_tray(app: &tauri::App) -> tauri::Result<()> {
    let open = MenuItem::with_id(
        app,
        "tray-open",
        "\u{041e}\u{0442}\u{043a}\u{0440}\u{044b}\u{0442}\u{044c} Dogma",
        true,
        None::<&str>,
    )?;
    let quit = MenuItem::with_id(
        app,
        "tray-quit",
        "\u{0412}\u{044b}\u{0439}\u{0442}\u{0438}",
        true,
        None::<&str>,
    )?;
    let menu = Menu::with_items(app, &[&open, &quit])?;
    let mut builder = TrayIconBuilder::new()
        .menu(&menu)
        .show_menu_on_left_click(false)
        .tooltip("Dogma")
        .on_menu_event(|app, event| match event.id().as_ref() {
            "tray-open" => reveal(app),
            "tray-quit" => app.exit(0),
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if matches!(
                event,
                TrayIconEvent::Click {
                    button: MouseButton::Left,
                    button_state: MouseButtonState::Up,
                    ..
                } | TrayIconEvent::DoubleClick {
                    button: MouseButton::Left,
                    ..
                }
            ) {
                reveal(tray.app_handle());
            }
        });
    if let Some(icon) = app.default_window_icon() {
        builder = builder.icon(icon.clone());
    }
    builder.build(app)?;
    Ok(())
}

fn fail_startup(app: &tauri::App, error: &dyn std::error::Error) {
    if let Some(window) = app.get_webview_window("main") { let _ = window.hide(); }
    app.dialog()
        .message(format!("Не удалось запустить локальную среду.\n\n{error}"))
        .title("Dogma")
        .kind(MessageDialogKind::Error)
        .show(|_| std::process::exit(1));
}

impl Runtime {
    fn shutdown(&self) {
        let mut child = match self.process.lock() {
            Ok(mut slot) => slot.take(),
            Err(_) => None,
        };
        if let Some(process) = child.as_mut() {
            process.stdin.take();
            for _ in 0..150 {
                if matches!(process.try_wait(), Ok(Some(_))) { return; }
                std::thread::sleep(Duration::from_millis(100));
            }
            let _ = process.kill();
            let _ = process.wait();
        }
    }
}

impl Drop for Runtime {
    fn drop(&mut self) { self.shutdown(); }
}

fn migrate_library(old: &Path, new: &Path) -> PathBuf {
    if old == new || fs::symlink_metadata(new.join(LIBRARY)).is_ok() || !old.join(LIBRARY).is_file() { return new.to_path_buf(); }
    if fs::symlink_metadata(new).map_or(false, |metadata| metadata.is_dir()) { let _ = fs::remove_dir(new); }
    let vacant = matches!(fs::symlink_metadata(new), Err(error) if error.kind() == std::io::ErrorKind::NotFound);
    if vacant && new.parent().map_or(false, |parent| fs::create_dir_all(parent).is_ok()) && fs::rename(old, new).is_ok() { new.to_path_buf() } else { old.to_path_buf() }
}

fn library_dir(app: &tauri::App) -> Result<PathBuf, Box<dyn std::error::Error>> {
    let roaming = app.path().app_data_dir();
    if !cfg!(windows) { return Ok(roaming?); }
    let library = app.path().app_local_data_dir()?.join("library");
    Ok(match roaming { Ok(roaming) => migrate_library(&roaming, &library), Err(_) => library })
}

fn launch(app: &tauri::App) -> Result<Runtime, Box<dyn std::error::Error>> {
    let data = library_dir(app)?;
    fs::create_dir_all(&data)?;
    let resources = if cfg!(debug_assertions) {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../resources").canonicalize()?
    } else {
        app.path().resource_dir()?
    };
    let name = if cfg!(windows) { "rimsky-runtime.exe" } else { "rimsky-runtime" };
    let executable = resources.join("runtime").join("core").join(name);
    let mut command = if executable.is_file() {
        Command::new(executable)
    } else if cfg!(debug_assertions) {
        let python = std::env::var("RIMSKY_PYTHON").unwrap_or_else(|_| if cfg!(windows) { "python".into() } else { "python3".into() });
        let mut process = Command::new(python);
        process.arg(PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../runtime/server.py"));
        process
    } else {
        return Err("The offline runtime is missing. This is not a complete application package.".into());
    };
    command.arg("--data-dir").arg(&data).arg("--parent-pipe");
    if cfg!(debug_assertions) { command.arg("--dev"); }
    command.env("RIMSKY_RESOURCE_DIR", &resources).env("PYTHONUTF8", "1").env("PYTHONIOENCODING", "utf-8");
    command.stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::inherit());
    #[cfg(windows)] {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
    let mut child = command.spawn()?;
    let stdout = child.stdout.take().ok_or("Runtime stdout is unavailable")?;
    let (tx, rx) = std::sync::mpsc::channel();
    std::thread::spawn(move || {
        let mut reader = BufReader::new(stdout);
        let mut line = String::new();
        let result = reader.read_line(&mut line).map(|_| line);
        let _ = tx.send(result);
        let mut sink = std::io::sink();
        let _ = std::io::copy(&mut reader, &mut sink);
    });
    let line = match rx.recv_timeout(Duration::from_secs(45)) {
        Ok(Ok(value)) if !value.trim().is_empty() => value,
        _ => { let _ = child.kill(); let _ = child.wait(); return Err("Local runtime did not start. Check its packaged dependencies.".into()); }
    };
    let reply: serde_json::Value = match serde_json::from_str(&line) {
        Ok(value) => value,
        Err(error) => { let _ = child.kill(); let _ = child.wait(); return Err(error.into()); }
    };
    if let Some(message) = reply.get("error").and_then(|value| value.as_str()) {
        let message: String = message.chars().take(2000).collect();
        let _ = child.kill(); let _ = child.wait();
        return Err(message.into());
    }
    let session: Session = match serde_json::from_value(reply) {
        Ok(value) => value,
        Err(error) => { let _ = child.kill(); let _ = child.wait(); return Err(error.into()); }
    };
    if session.protocol != 1 || session.port == 0 || session.token.len() < 32 { let _ = child.kill(); let _ = child.wait(); return Err("Invalid runtime handshake".into()); }
    Ok(Runtime { process: Mutex::new(Some(child)), session, library: data })
}

#[tauri::command]
fn runtime_session(state: tauri::State<Runtime>) -> Session { state.session.clone() }

fn export_target_exists(destination: &Path) -> std::io::Result<bool> {
    match fs::symlink_metadata(destination) {
        Ok(metadata) if metadata.file_type().is_file() => Ok(true),
        Ok(_) => Err(std::io::Error::new(std::io::ErrorKind::InvalidInput, "Export destination must be a regular file")),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(false),
        Err(error) => Err(error),
    }
}

fn copy_export(source: &Path, destination: &Path) -> std::io::Result<()> {
    export_target_exists(destination)?;
    let parent = destination.parent().ok_or_else(|| std::io::Error::new(std::io::ErrorKind::InvalidInput, "Missing export directory"))?;
    let nonce = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH)
        .map_err(std::io::Error::other)?.as_nanos();
    let temporary = parent.join(format!(".rimsky-export-{}-{nonce}.tmp", std::process::id()));
    let mut output = fs::OpenOptions::new().write(true).create_new(true).open(&temporary)?;
    let result = (|| {
        let mut input = fs::File::open(source)?;
        std::io::copy(&mut input, &mut output)?;
        output.sync_all()?;
        drop(output);
        replace_export(&temporary, destination)
    })();
    if result.is_err() { let _ = fs::remove_file(&temporary); }
    result
}

#[cfg(not(windows))]
fn replace_export(source: &Path, destination: &Path) -> std::io::Result<()> {
    export_target_exists(destination)?;
    fs::rename(source, destination)
}

#[cfg(windows)]
fn replace_export(source: &Path, destination: &Path) -> std::io::Result<()> {
    if !export_target_exists(destination)? { return fs::rename(source, destination); }
    let nonce = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH)
        .map_err(std::io::Error::other)?.as_nanos();
    let backup = destination.with_file_name(format!(".rimsky-replaced-{}-{nonce}.bak", std::process::id()));
    fs::rename(destination, &backup)?;
    match fs::rename(source, destination) {
        Ok(()) => fs::remove_file(&backup).map_err(|error| std::io::Error::new(error.kind(), format!("Export was saved, but the previous file remains at {}: {error}", backup.display()))),
        Err(error) => match fs::rename(&backup, destination) {
            Ok(()) => Err(error),
            Err(restore_error) => Err(std::io::Error::new(error.kind(), format!("Export failed: {error}. The previous file could not be restored and remains at {}: {restore_error}", backup.display()))),
        },
    }
}

fn export_file(library: &Path, filename: &str, destination: &str) -> Result<(), String> {
    let root = library.join("exports");
    let source = root.join(filename).canonicalize().map_err(|e|e.to_string())?;
    let canonical_root = root.canonicalize().map_err(|e|e.to_string())?;
    if !source.starts_with(canonical_root) { return Err("Export path is outside the library".into()); }
    let target = PathBuf::from(destination);
    if !target.is_absolute() { return Err("Choose an absolute destination".into()); }
    let target_exists = export_target_exists(&target).map_err(|e|e.to_string())?;
    let data_root = library.canonicalize().map_err(|e|e.to_string())?;
    let parent = target.parent().ok_or("Choose a destination directory")?.canonicalize().map_err(|e|e.to_string())?;
    let resolved_target = if target_exists {
        target.canonicalize().map_err(|e|e.to_string())?
    } else {
        parent.join(target.file_name().ok_or("Choose an export filename")?)
    };
    if resolved_target.starts_with(&data_root) { return Err("Choose a destination outside the application library".into()); }
    copy_export(&source, &resolved_target).map_err(|e|e.to_string())
}

#[tauri::command]
async fn save_export(app: tauri::AppHandle, filename: String, destination: String) -> Result<(), String> {
    if filename.len() > 100 || !filename.chars().all(|c| c.is_ascii_alphanumeric() || c == '.') || filename.contains("..") {
        return Err("Invalid export file".into());
    }
    let library = app.try_state::<Runtime>().map(|runtime| runtime.library.clone()).ok_or("Local runtime is not running")?;
    tauri::async_runtime::spawn_blocking(move || export_file(&library, &filename, &destination)).await.map_err(|error| error.to_string())?
}

#[tauri::command]
fn startup_books(state: tauri::State<Mutex<OpenBooks>>) -> Result<Vec<String>, String> {
    state.lock().map(|mut state| std::mem::take(&mut state.pending)).map_err(|e|e.to_string())
}

#[tauri::command]
async fn read_opened_book(state: tauri::State<'_, Mutex<OpenBooks>>, path: String) -> Result<Response, String> {
    let file = state.lock().ok().and_then(|books| books.allowed.get(&path).cloned()).ok_or("File was not opened with this application")?;
    if fs::canonicalize(&file).map_err(|e|e.to_string())? != file { return Err("File was not opened with this application".into()); }
    let metadata = fs::metadata(&file).map_err(|e|e.to_string())?;
    if metadata.len() > 50 * 1024 * 1024 { return Err("Book exceeds 50 MiB".into()); }
    fs::read(&file).map(Response::new).map_err(|e|e.to_string())
}

fn allowed_navigation(url: &tauri::Url, dev: Option<&tauri::Url>) -> bool {
    let host = url.host_str().unwrap_or("");
    match url.scheme() {
        "tauri" => host == "localhost",
        "http" | "https" if host == "tauri.localhost" => true,
        _ => cfg!(debug_assertions) && dev.map_or(false, |dev| dev.origin() == url.origin()),
    }
}

fn navigation_guard<R: tauri::Runtime>() -> tauri::plugin::TauriPlugin<R> {
    tauri::plugin::Builder::new("navigation-guard")
        .on_navigation(|webview, url| allowed_navigation(url, webview.config().build.dev_url.as_ref()))
        .build()
}

pub fn run() {
    let cwd = std::env::current_dir().expect("Unable to resolve the startup directory");
    let mut books = OpenBooks { allowed: HashMap::new(), pending: Vec::new() };
    queue_books(&mut books, &book_paths(std::env::args_os().skip(1), &cwd));
    tauri::Builder::default()
        .manage(Mutex::new(books))
        .plugin(tauri_plugin_single_instance::init(|app, argv, cwd| {
            admit_books(app, argv.into_iter().skip(1), Path::new(&cwd));
            reveal(app);
        }))
        .plugin(tauri_plugin_dialog::init())
        .plugin(navigation_guard())
        .on_window_event(|window, event| {
            if window.label() == "main" {
                if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .setup(|app| {
            match launch(app) {
                Ok(runtime) => { app.manage(runtime); }
                Err(error) => fail_startup(app, error.as_ref()),
            }
            install_tray(app)?;
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![runtime_session, save_export, startup_books, read_opened_book])
        .build(tauri::generate_context!())
        .expect("Unable to start Dogma")
        .run(|app, event| {
            #[cfg(target_os = "macos")]
            if let tauri::RunEvent::Opened { urls } = &event {
                admit_books(app, urls.iter().filter_map(|url| url.to_file_path().ok()), Path::new(""));
                reveal(app);
            }
            if let tauri::RunEvent::Exit = event {
                if let Some(runtime) = app.try_state::<Runtime>() { runtime.shutdown(); }
            }
        });
}

#[cfg(test)]
mod native_checks {
    use super::*;

    fn directory(name: &str) -> PathBuf {
        let nonce = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
        let path = std::env::temp_dir().join(format!("rimsky-{name}-{}-{nonce}", std::process::id()));
        fs::create_dir_all(&path).unwrap();
        path
    }

    #[test]
    fn opened_paths_use_sender_directory_and_keep_first_order() {
        let root = directory("opened");
        let sender = root.join("sender");
        fs::create_dir(&sender).unwrap();
        fs::write(sender.join("second.FB2"), b"second").unwrap();
        fs::write(sender.join("first.epub"), b"first").unwrap();
        fs::write(sender.join("ignored.txt"), b"ignored").unwrap();
        fs::create_dir(sender.join("directory.epub")).unwrap();
        let first = fs::canonicalize(sender.join("first.epub")).unwrap();
        let second = fs::canonicalize(sender.join("second.FB2")).unwrap();
        assert_eq!(book_paths(vec!["second.FB2".into(), first.clone(), "second.FB2".into(), "ignored.txt".into(), "directory.epub".into(), "absent.epub".into()], &sender), vec![second, first]);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn pending_opens_survive_until_drain_and_can_be_reopened() {
        let mut state = OpenBooks { allowed: HashMap::new(), pending: Vec::new() };
        queue_books(&mut state, &["a".into(), "b".into()]);
        queue_books(&mut state, &["b".into(), "c".into()]);
        assert_eq!(std::mem::take(&mut state.pending), vec!["a", "b", "c"]);
        assert!(state.pending.is_empty());
        assert_eq!(state.allowed.len(), 3);
        queue_books(&mut state, &["a".into()]);
        assert_eq!(std::mem::take(&mut state.pending), vec!["a"]);
        assert_eq!(state.allowed.len(), 3);
    }

    #[test]
    fn concurrent_admission_and_draining_preserve_every_path() {
        let shared = std::sync::Arc::new(Mutex::new(OpenBooks { allowed: HashMap::new(), pending: Vec::new() }));
        let writer = shared.clone();
        let thread = std::thread::spawn(move || {
            for index in 0..1000 { queue_books(&mut writer.lock().unwrap(), &[PathBuf::from(index.to_string())]); }
        });
        let mut received = Vec::new();
        while !thread.is_finished() {
            received.extend(std::mem::take(&mut shared.lock().unwrap().pending));
            std::thread::yield_now();
        }
        thread.join().unwrap();
        received.extend(std::mem::take(&mut shared.lock().unwrap().pending));
        assert_eq!(received, (0..1000).map(|index| index.to_string()).collect::<Vec<_>>());
        assert_eq!(shared.lock().unwrap().allowed.len(), 1000);
    }

    #[cfg(unix)]
    #[test]
    fn non_unicode_arguments_do_not_panic() {
        use std::os::unix::ffi::OsStringExt;
        let root = directory("non-unicode");
        assert!(book_paths(vec![std::ffi::OsString::from_vec(b"missing-\xff.epub".to_vec())], &root).is_empty());
        fs::remove_dir_all(root).unwrap();
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn non_unicode_book_paths_stay_readable_through_their_ipc_name() {
        use std::os::unix::ffi::OsStringExt;
        let root = directory("non-unicode-open");
        let name = std::ffi::OsString::from_vec(b"\xe7\xe8.fb2".to_vec());
        fs::write(root.join(&name), b"book").unwrap();
        let paths = book_paths(vec![name], &root);
        assert_eq!(paths.len(), 1);
        let mut state = OpenBooks { allowed: HashMap::new(), pending: Vec::new() };
        let names = queue_books(&mut state, &paths);
        assert_eq!(state.allowed.get(&names[0]), Some(&paths[0]));
        assert_eq!(fs::read(&paths[0]).unwrap(), b"book");
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn library_moves_from_roaming_as_one_folder_beside_the_webview_profile() {
        let root = directory("migrate");
        let (old, local) = (root.join("roaming"), root.join("local"));
        let new = local.join("library");
        fs::create_dir_all(old.join("audio")).unwrap();
        fs::write(old.join("audio").join("a.wav"), b"audio").unwrap();
        fs::write(old.join(LIBRARY), b"db").unwrap();
        fs::write(old.join("library.sqlite3-wal"), b"wal").unwrap();
        fs::create_dir_all(local.join("EBWebView")).unwrap();
        assert_eq!(migrate_library(&old, &new), new);
        assert_eq!(fs::read(new.join(LIBRARY)).unwrap(), b"db");
        assert_eq!(fs::read(new.join("library.sqlite3-wal")).unwrap(), b"wal");
        assert_eq!(fs::read(new.join("audio").join("a.wav")).unwrap(), b"audio");
        assert!(local.join("EBWebView").is_dir() && !new.join("EBWebView").exists());
        assert!(!old.exists());
        assert_eq!(migrate_library(&old, &new), new);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn library_migration_leaves_both_folders_untouched_when_the_target_has_content() {
        let root = directory("migrate-conflict");
        let (old, new) = (root.join("roaming"), root.join("local").join("library"));
        fs::create_dir_all(old.join("books")).unwrap();
        fs::write(old.join(LIBRARY), b"db").unwrap();
        fs::create_dir_all(new.join("work")).unwrap();
        fs::write(new.join("work").join("partial"), b"partial").unwrap();
        assert_eq!(migrate_library(&old, &new), old);
        assert_eq!(migrate_library(&old, &new), old);
        assert!(old.join(LIBRARY).is_file() && old.join("books").is_dir());
        assert_eq!(fs::read(new.join("work").join("partial")).unwrap(), b"partial");
        assert!(!new.join(LIBRARY).exists() && !new.join("books").exists());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn failed_library_migration_keeps_the_old_folder_and_retries_into_an_empty_target() {
        let root = directory("migrate-retry");
        let old = root.join("roaming");
        fs::create_dir_all(old.join("audio")).unwrap();
        fs::write(old.join("audio").join("a.wav"), b"audio").unwrap();
        fs::write(old.join(LIBRARY), b"db").unwrap();
        fs::write(root.join("blocked"), b"file").unwrap();
        assert_eq!(migrate_library(&old, &root.join("blocked").join("library")), old);
        assert_eq!(fs::read(old.join("audio").join("a.wav")).unwrap(), b"audio");
        assert_eq!(fs::read(old.join(LIBRARY)).unwrap(), b"db");
        let new = root.join("local").join("library");
        fs::create_dir_all(&new).unwrap();
        assert_eq!(migrate_library(&old, &new), new);
        assert_eq!(fs::read(new.join("audio").join("a.wav")).unwrap(), b"audio");
        assert_eq!(fs::read(new.join(LIBRARY)).unwrap(), b"db");
        assert!(!old.exists());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn library_stays_when_paths_match_or_a_local_library_exists() {
        let root = directory("migrate-noop");
        let (old, new) = (root.join("roaming"), root.join("local"));
        fs::create_dir_all(&old).unwrap();
        fs::write(old.join(LIBRARY), b"old").unwrap();
        assert_eq!(migrate_library(&old, &old), old);
        fs::create_dir_all(&new).unwrap();
        fs::write(new.join(LIBRARY), b"new").unwrap();
        assert_eq!(migrate_library(&old, &new), new);
        assert_eq!(fs::read(old.join(LIBRARY)).unwrap(), b"old");
        assert_eq!(migrate_library(&root.join("absent"), &root.join("fresh")), root.join("fresh"));
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn navigation_is_limited_to_application_urls() {
        let dev = "http://127.0.0.1:1420".parse::<tauri::Url>().unwrap();
        for url in ["tauri://localhost", "tauri://localhost/index.html", "http://tauri.localhost/", "https://tauri.localhost/#port=1"] {
            assert!(allowed_navigation(&url.parse::<tauri::Url>().unwrap(), Some(&dev)), "{url}");
        }
        for url in ["file:///tmp/book.epub", "https://example.com/", "http://127.0.0.1:1421/", "tauri://example.com/", "http://tauri.localhost.example.com/", "data:text/html,x"] {
            assert!(!allowed_navigation(&url.parse::<tauri::Url>().unwrap(), Some(&dev)), "{url}");
        }
        assert_eq!(allowed_navigation(&"http://127.0.0.1:1420/#port=1".parse::<tauri::Url>().unwrap(), Some(&dev)), cfg!(debug_assertions));
        assert!(!allowed_navigation(&"http://127.0.0.1:1420/".parse::<tauri::Url>().unwrap(), None));
    }

    #[test]
    fn exports_reject_directories_without_moving_them() {
        let root = directory("directory-target");
        let source = root.join("source.wav");
        fs::write(&source, b"new audio").unwrap();
        for name in ["empty", "populated"] {
            let destination = root.join(name);
            fs::create_dir(&destination).unwrap();
            if name == "populated" { fs::write(destination.join("original"), b"keep").unwrap(); }
            assert_eq!(copy_export(&source, &destination).unwrap_err().kind(), std::io::ErrorKind::InvalidInput);
            assert_eq!(replace_export(&source, &destination).unwrap_err().kind(), std::io::ErrorKind::InvalidInput);
            assert!(destination.is_dir());
            assert!(source.is_file());
            if name == "populated" { assert_eq!(fs::read(destination.join("original")).unwrap(), b"keep"); }
        }
        assert_eq!(fs::read_dir(&root).unwrap().count(), 3);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn exports_create_and_replace_regular_files_without_leftovers() {
        let root = directory("export");
        let source = root.join("source.wav");
        let destination = root.join("destination.wav");
        fs::write(&source, b"first audio").unwrap();
        copy_export(&source, &destination).unwrap();
        assert_eq!(fs::read(&destination).unwrap(), b"first audio");
        fs::write(&source, b"second audio").unwrap();
        copy_export(&source, &destination).unwrap();
        assert_eq!(fs::read(&destination).unwrap(), b"second audio");
        assert_eq!(fs::read_dir(&root).unwrap().count(), 2);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn failed_replacement_preserves_previous_destination() {
        let root = directory("rollback");
        let destination = root.join("destination.wav");
        fs::write(&destination, b"original").unwrap();
        assert!(replace_export(&root.join("missing.wav"), &destination).is_err());
        assert_eq!(fs::read(&destination).unwrap(), b"original");
        assert_eq!(fs::read_dir(&root).unwrap().count(), 1);
        assert!(copy_export(&root.join("missing.wav"), &destination).is_err());
        assert_eq!(fs::read(&destination).unwrap(), b"original");
        assert_eq!(fs::read_dir(&root).unwrap().count(), 1);
        fs::remove_dir_all(root).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn exports_reject_symlinks_including_dangling_targets() {
        let root = directory("symlink-target");
        let source = root.join("source.wav");
        let original = root.join("original.wav");
        fs::write(&source, b"new").unwrap();
        fs::write(&original, b"old").unwrap();
        let link = root.join("link.wav");
        std::os::unix::fs::symlink(&original, &link).unwrap();
        assert!(copy_export(&source, &link).is_err());
        assert_eq!(fs::read(&original).unwrap(), b"old");
        fs::remove_file(&original).unwrap();
        assert!(copy_export(&source, &link).is_err());
        assert!(fs::symlink_metadata(&link).unwrap().file_type().is_symlink());
        assert!(!original.exists());
        fs::remove_dir_all(root).unwrap();
    }
}
