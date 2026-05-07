from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def read(*parts: str) -> str:
    return PROJECT_ROOT.joinpath(*parts).read_text(encoding="utf-8")


def test_notification_store_supports_persistent_grouped_toasts():
    store = read("webui", "components", "notifications", "notification-store.js")
    api = read("api", "notification_create.py")

    assert "isPersistentToast(toast)" in store
    assert "return this.getToastDisplayTime(toast) <= 0;" in store
    assert store.count("if (this.isPersistentToast(toast)) return;") >= 2
    assert "this.restartToastTimer(toast.toastId);" in store
    assert "this.removeFromToastStack(existingToast.toastId);" in store
    assert "if display_time < 0:" in api
    assert "if display_time <= 0:" not in api


def test_backup_zip_downloads_emit_grouped_preparing_and_downloading_toasts():
    store = read("webui", "components", "settings", "backup", "backup-store.js")

    assert 'window.toastFrontendInfo?.("Preparing download...", "Download", 0, group, undefined, true);' in store
    assert 'window.toastFrontendInfo?.("Downloading...", "Download", 3, group, undefined, true);' in store
    assert 'window.toastFrontendError?.(message || "Download failed", "Download Error", 8, group, undefined, true);' in store
    assert 'this.createDownloadToastGroup("backup-create")' in store
    assert 'this.createDownloadToastGroup("backup-download")' in store

    create_start = store.index("async createBackup()")
    create_prepare = store.index("this.showDownloadPreparingToast(downloadToastGroup);", create_start)
    create_fetch = store.index("const response = await fetchApi('/backup_create'", create_start)
    assert create_prepare < create_fetch

    download_start = store.index("async downloadBackup")
    download_prepare = store.index("this.showDownloadPreparingToast(downloadToastGroup);", download_start)
    download_fetch = store.index("const response = await fetchApi('/backup_download'", download_start)
    assert download_prepare < download_fetch


def test_file_browser_zip_downloads_emit_grouped_preparing_and_downloading_toasts():
    store = read("webui", "components", "modals", "file-browser", "file-browser-store.js")

    assert 'window.toastFrontendInfo?.("Preparing download...", "Download", 0, group, undefined, true);' in store
    assert 'window.toastFrontendInfo?.("Downloading...", "Download", 3, group, undefined, true);' in store
    assert 'this.createDownloadToastGroup("file-browser-bulk-download")' in store
    assert 'this.createDownloadToastGroup("file-browser-directory-download")' in store
    assert "if (file.is_dir) {" in store
    assert "return this.downloadDirectory(file);" in store
    assert "link.download = file.name;" in store

    bulk_start = store.index("async bulkDownloadFiles()")
    bulk_prepare = store.index("this.showDownloadPreparingToast(downloadToastGroup);", bulk_start)
    bulk_fetch = store.index('const resp = await fetchApi("/download_work_dir_files"', bulk_start)
    assert bulk_prepare < bulk_fetch

    directory_start = store.index("async downloadDirectory(file)")
    directory_prepare = store.index("this.showDownloadPreparingToast(downloadToastGroup);", directory_start)
    directory_fetch = store.index("const resp = await fetchApi(`/download_work_dir_file", directory_start)
    assert directory_prepare < directory_fetch
