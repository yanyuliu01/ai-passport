package com.yanyuliu.pockethub;

import android.content.Context;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;

/** Android SQLite adapter. Construction is explicit; no startup hook creates or imports data. */
final class LegacyArchiveAndroidDb implements LegacyArchiveStore.Database {
    private final SQLiteDatabase db;
    LegacyArchiveAndroidDb(Context context) {
        db=context.getApplicationContext().openOrCreateDatabase("legacy-history-archive.db",Context.MODE_PRIVATE,null);
        if(!db.enableWriteAheadLogging()){db.close();throw new IllegalStateException("历史归档无法启用 WAL");}
        db.execSQL("PRAGMA synchronous=FULL");
    }
    public void exec(String sql,Object... args){if(args.length==0)db.execSQL(sql);else db.execSQL(sql,args);}
    public LegacyArchiveStore.Rows query(String sql,Object... args){
        String[] values=new String[args.length];for(int i=0;i<args.length;i++)values[i]=args[i]==null?null:String.valueOf(args[i]);
        Cursor cursor=db.rawQuery(sql,values);
        return new LegacyArchiveStore.Rows(){
            public boolean next(){return cursor.moveToNext();}
            public String string(int column){return cursor.getString(column);}
            public long number(int column){return cursor.getLong(column);}
            public byte[] bytes(int column){return cursor.getBlob(column);}
            public void close(){cursor.close();}
        };
    }
    public void begin(){db.beginTransactionNonExclusive();}
    public void success(){db.setTransactionSuccessful();}
    public void end(){db.endTransaction();}
    public void close(){db.close();}
}
