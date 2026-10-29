package id.citramahkota.monitoringstok;

import android.app.DownloadManager;
import android.content.Context;
import android.net.Uri;
import android.os.Environment;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

import java.util.Locale;
import java.util.regex.Pattern;

/** Unduh APK rilis GitHub lewat DownloadManager tanpa izin pemasangan paket. */
@CapacitorPlugin(name = "UpdateDownload")
public class UpdateDownload extends Plugin {
    private static final Pattern SAFE_NAME = Pattern.compile(
        "^MonitoringStok-\\d{1,6}\\.\\d{1,2}\\.\\d{1,2}\\.apk$",
        Pattern.CASE_INSENSITIVE
    );

    private static String safeName(String raw) {
        String name = raw == null ? "" : raw.trim();
        if (!SAFE_NAME.matcher(name).matches()) return null;
        if (name.contains("/") || name.contains("\\") || name.contains("..")) return null;
        return name;
    }

    @PluginMethod
    public void start(PluginCall call) {
        String name = safeName(call.getString("name", ""));
        if (name == null) {
            call.reject("Nama berkas APK tidak valid");
            return;
        }
        Uri uri;
        try {
            uri = Uri.parse(call.getString("url", ""));
        } catch (Exception error) {
            call.reject("Link pembaruan tidak dapat dibaca");
            return;
        }
        String host = uri.getHost() == null ? "" : uri.getHost().toLowerCase(Locale.ROOT);
        String path = uri.getPath() == null ? "" : uri.getPath().toLowerCase(Locale.ROOT);
        if (!"https".equalsIgnoreCase(uri.getScheme()) || !"github.com".equals(host)
                || !path.contains("/releases/download/")) {
            call.reject("APK hanya boleh diunduh dari GitHub Releases yang diatur Admin");
            return;
        }
        DownloadManager manager = (DownloadManager) getContext().getSystemService(Context.DOWNLOAD_SERVICE);
        if (manager == null) {
            call.reject("Pengelola unduhan Android tidak tersedia");
            return;
        }
        try {
            DownloadManager.Request request = new DownloadManager.Request(uri);
            request.setTitle(name);
            request.setDescription("Pembaruan Monitoring Stok");
            request.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
            request.setMimeType("application/vnd.android.package-archive");
            request.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, name);
            long id = manager.enqueue(request);
            JSObject result = new JSObject();
            result.put("id", id);
            call.resolve(result);
        } catch (Exception error) {
            call.reject("Tidak dapat memulai unduhan: " + error.getMessage());
        }
    }
}
