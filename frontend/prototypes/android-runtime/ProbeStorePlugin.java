package org.travelflow.p0;

import android.content.ContentValues;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.database.sqlite.SQLiteOpenHelper;
import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

/** P0独立数据库，只接收虚构的固定123分记录，不是正式财务接口。 */
@CapacitorPlugin(name = "ProbeStore")
public class ProbeStorePlugin extends Plugin {
    private SQLiteOpenHelper helper;

    @Override
    public void load() {
        helper = new SQLiteOpenHelper(getContext(), "p0-native.sqlite", null, 1) {
            @Override
            public void onCreate(SQLiteDatabase db) {
                db.execSQL("CREATE TABLE records (id TEXT PRIMARY KEY, cents INTEGER NOT NULL CHECK(cents=123))");
            }
            @Override
            public void onUpgrade(SQLiteDatabase db, int oldVersion, int newVersion) {
                throw new IllegalStateException("No prototype migration is defined");
            }
        };
    }

    private JSObject snapshot(SQLiteDatabase db) {
        JSArray entries = new JSArray();
        long total = 0;
        try (Cursor rows = db.rawQuery("SELECT id,cents FROM records ORDER BY id", null)) {
            while (rows.moveToNext()) {
                JSObject entry = new JSObject();
                entry.put("id", rows.getString(0));
                entry.put("amount_cents", rows.getLong(1));
                entries.put(entry);
                total += rows.getLong(1);
            }
        }
        JSObject result = new JSObject();
        result.put("entries", entries);
        result.put("total_cents", total);
        return result;
    }

    @PluginMethod
    public void read(PluginCall call) {
        try {
            call.resolve(snapshot(helper.getReadableDatabase()));
        } catch (Exception error) {
            call.reject("Prototype database could not be read", "STORE_READ_FAILED");
        }
    }

    @PluginMethod
    public void append(PluginCall call) {
        String id = call.getString("id", "");
        if (!id.matches("[a-zA-Z0-9_-]{1,80}")) {
            call.reject("Invalid prototype operation ID", "INVALID_ID");
            return;
        }
        try {
            SQLiteDatabase db = helper.getWritableDatabase();
            JSObject result;
            db.beginTransaction();
            try {
                ContentValues values = new ContentValues();
                values.put("id", id);
                values.put("cents", 123);
                db.insertWithOnConflict("records", null, values, SQLiteDatabase.CONFLICT_IGNORE);
                result = snapshot(db);
                db.setTransactionSuccessful();
            } finally {
                db.endTransaction();
            }
            // 事务提交后才返回，不把仅排入队列当保存成功。
            call.resolve(result);
        } catch (Exception error) {
            call.reject("Prototype write was not confirmed", "STORE_WRITE_FAILED");
        }
    }

    @Override
    protected void handleOnDestroy() {
        if (helper != null) helper.close();
        super.handleOnDestroy();
    }
}
