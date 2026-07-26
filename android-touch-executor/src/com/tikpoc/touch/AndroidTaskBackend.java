package com.tikpoc.touch;

import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.database.sqlite.SQLiteOpenHelper;
import java.util.ArrayList;
import java.util.List;

public final class AndroidTaskBackend implements DeviceTaskStore.Backend {
    private static final String DB_NAME = "tikpoc-mobile-queue.db";
    private final QueueDatabase database;

    public AndroidTaskBackend(Context context) {
        database = new QueueDatabase(context.getApplicationContext());
    }

    public static String[] schema() {
        return new String[] {
                "CREATE TABLE tasks (task_id TEXT PRIMARY KEY, lease_id TEXT NOT NULL, "
                        + "session_epoch INTEGER NOT NULL, lease_expires_at_ms INTEGER NOT NULL, "
                        + "phase TEXT NOT NULL, payload TEXT NOT NULL)",
                "CREATE TABLE results (idempotency_key TEXT PRIMARY KEY, task_id TEXT NOT NULL, "
                        + "payload TEXT NOT NULL)"
        };
    }

    @Override
    public synchronized void saveTask(DeviceTaskStore.Task task) {
        ContentValues values = new ContentValues();
        values.put("task_id", task.taskId);
        values.put("lease_id", task.leaseId);
        values.put("session_epoch", task.sessionEpoch);
        values.put("lease_expires_at_ms", task.leaseExpiresAtMs);
        values.put("phase", task.phase);
        values.put("payload", task.payload);
        database.getWritableDatabase().insertWithOnConflict(
                "tasks", null, values, SQLiteDatabase.CONFLICT_REPLACE);
    }

    @Override
    public synchronized List<DeviceTaskStore.Task> loadTasks() {
        List<DeviceTaskStore.Task> tasks = new ArrayList<DeviceTaskStore.Task>();
        Cursor cursor = database.getReadableDatabase().query(
                "tasks", null, null, null, null, null, "rowid ASC");
        try {
            while (cursor.moveToNext()) {
                tasks.add(new DeviceTaskStore.Task(
                        cursor.getString(cursor.getColumnIndexOrThrow("task_id")),
                        cursor.getString(cursor.getColumnIndexOrThrow("lease_id")),
                        cursor.getLong(cursor.getColumnIndexOrThrow("session_epoch")),
                        cursor.getLong(cursor.getColumnIndexOrThrow("lease_expires_at_ms")),
                        cursor.getString(cursor.getColumnIndexOrThrow("phase")),
                        cursor.getString(cursor.getColumnIndexOrThrow("payload"))));
            }
        } finally {
            cursor.close();
        }
        return tasks;
    }

    @Override
    public synchronized void saveResult(DeviceTaskStore.Result result) {
        ContentValues values = new ContentValues();
        values.put("idempotency_key", result.idempotencyKey);
        values.put("task_id", result.taskId);
        values.put("payload", result.payload);
        database.getWritableDatabase().insertWithOnConflict(
                "results", null, values, SQLiteDatabase.CONFLICT_IGNORE);
    }

    @Override
    public synchronized List<DeviceTaskStore.Result> loadResults() {
        List<DeviceTaskStore.Result> results = new ArrayList<DeviceTaskStore.Result>();
        Cursor cursor = database.getReadableDatabase().query(
                "results", null, null, null, null, null, "rowid ASC");
        try {
            while (cursor.moveToNext()) {
                results.add(new DeviceTaskStore.Result(
                        cursor.getString(cursor.getColumnIndexOrThrow("idempotency_key")),
                        cursor.getString(cursor.getColumnIndexOrThrow("task_id")),
                        cursor.getString(cursor.getColumnIndexOrThrow("payload"))));
            }
        } finally {
            cursor.close();
        }
        return results;
    }

    @Override
    public synchronized void deleteResult(String idempotencyKey) {
        database.getWritableDatabase().delete(
                "results", "idempotency_key = ?", new String[] {idempotencyKey});
    }

    private static final class QueueDatabase extends SQLiteOpenHelper {
        QueueDatabase(Context context) { super(context, DB_NAME, null, 1); }

        @Override
        public void onCreate(SQLiteDatabase database) {
            database.execSQL(schema()[0]);
            database.execSQL(schema()[1]);
        }

        @Override
        public void onUpgrade(SQLiteDatabase database, int oldVersion, int newVersion) {}
    }
}
