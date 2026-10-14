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
    static Uri uri(Context context, File file, Attachment item) {
        return new Uri.Builder().scheme("content").authority(context.getPackageName() + ".artifacts")
                .appendPath(file.getName()).appendQueryParameter("name", item.label())
                .appendQueryParameter("mime", item.mime).build();
    }
    private File file(Uri uri) throws FileNotFoundException {
        String name = uri.getLastPathSegment();
        if (name == null || !name.matches("[0-9a-f]{64}\\.bin")) throw new FileNotFoundException("Invalid artifact");
        File file = new File(new File(getContext().getFilesDir(), "artifacts"), name);
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
