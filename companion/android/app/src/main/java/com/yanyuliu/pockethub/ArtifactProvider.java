package com.yanyuliu.pockethub;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;
import java.io.File;
import java.io.FileNotFoundException;

/** Read-only grants for a single cached artifact; no paths or Runtime credentials are shared. */
public final class ArtifactProvider extends ContentProvider {
    /** App 自己的更新包放在这里，和任务附件分开，好整个清掉旧的。 */
    static final String UPDATES = "updates";
    static Uri uri(Context context, File file, Attachment item) {
        return uri(context, file, item.label(), item.mime);
    }
    static Uri uri(Context context, File file, String name, String mime) {
        Uri.Builder builder = new Uri.Builder().scheme("content").authority(context.getPackageName() + ".artifacts");
        if (UPDATES.equals(file.getParentFile().getName())) builder.appendPath(UPDATES);
        return builder.appendPath(file.getName()).appendQueryParameter("name", name)
                .appendQueryParameter("mime", mime).build();
    }
    private File file(Uri uri) throws FileNotFoundException {
        String name = uri.getLastPathSegment();
        java.util.List<String> segments = uri.getPathSegments();
        boolean update = segments.size() == 2 && UPDATES.equals(segments.get(0));
        if (name == null || (segments.size() != 1 && !update) || !name.matches("[0-9a-f]{64}\\.bin")) throw new FileNotFoundException("Invalid artifact");
        File file = new File(new File(getContext().getFilesDir(), update ? UPDATES : "artifacts"), name);
        if (!file.isFile()) throw new FileNotFoundException("Artifact not cached");
        return file;
    }
    @Override public boolean onCreate() { return true; }
    @Override public String getType(Uri uri) { String mime = uri.getQueryParameter("mime"); return mime == null || mime.isEmpty() ? "application/octet-stream" : mime; }
    @Override public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
        if (!mode.equals("r")) throw new FileNotFoundException("Read only");
        return ParcelFileDescriptor.open(file(uri), ParcelFileDescriptor.MODE_READ_ONLY);
    }
    @Override public Cursor query(Uri uri, String[] projection, String selection, String[] args, String order) {
        try {
            File file = file(uri);
            String[] columns = projection == null ? new String[] {OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE} : projection;
            MatrixCursor cursor = new MatrixCursor(columns); Object[] values = new Object[columns.length];
            for (int index = 0; index < columns.length; index++) values[index] = columns[index].equals(OpenableColumns.DISPLAY_NAME) ? uri.getQueryParameter("name") : columns[index].equals(OpenableColumns.SIZE) ? file.length() : null;
            cursor.addRow(values); return cursor;
        } catch (FileNotFoundException missing) { return null; }
    }
    @Override public Uri insert(Uri uri, ContentValues values) { throw new UnsupportedOperationException(); }
    @Override public int delete(Uri uri, String selection, String[] args) { throw new UnsupportedOperationException(); }
    @Override public int update(Uri uri, ContentValues values, String selection, String[] args) { throw new UnsupportedOperationException(); }
}
