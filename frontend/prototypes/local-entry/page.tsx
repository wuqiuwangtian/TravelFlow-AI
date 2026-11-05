"use client";

import { useEffect, useState } from "react";
import { ActionFeedback, Button, Card } from "@/components/ui";

type Entry = { id: string; amount_cents: number };

async function database(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open("travelflow-p0-isolated", 1);
    request.onupgradeneeded = () => {
      request.result.createObjectStore("records", { keyPath: "id" });
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
    request.onblocked = () => reject(new Error("原型数据库被旧窗口占用"));
  });
}

async function records(entry?: Entry): Promise<Entry[]> {
  const db = await database();
  try {
    return await new Promise((resolve, reject) => {
      const transaction = db.transaction("records", entry ? "readwrite" : "readonly");
      const store = transaction.objectStore("records");
      if (entry) store.add(entry);
      const request = store.getAll();
      transaction.oncomplete = () => resolve(request.result as Entry[]);
      transaction.onabort = () => reject(transaction.error ?? new Error("本地事务已中止"));
      transaction.onerror = () => reject(transaction.error ?? new Error("本地事务失败"));
    });
  } finally {
    db.close();
  }
}

export default function LocalEntry() {
  const [items, setItems] = useState<Entry[]>([]);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    records().then((value) => { if (active) setItems(value); })
      .catch((e) => { if (active) setError(String(e)); })
      .finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, []);

  async function append() {
    setBusy(true);
    setError("");
    try {
      setItems(await records({ id: crypto.randomUUID(), amount_cents: 123 }));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="tf-page space-y-4">
      <h1 className="text-2xl font-bold text-ink">本地入口隔离原型</h1>
      <p className="text-sm text-muted">
        仅验证静态入口与本地事务。使用虚构记录，不是正式账本、同步或Android安装包。
      </p>
      <Card className="space-y-4 p-5">
        <p role="status">本地记录 {items.length} 条 · 合计 ¥{(items.reduce((n, e) => n + e.amount_cents, 0) / 100).toFixed(2)}</p>
        <Button disabled={busy || !!error} onClick={append}>保存虚构记录1.23元</Button>
        <ActionFeedback pending={busy ? "正在访问本地存储…" : undefined} error={error || undefined} />
        <p className="text-xs text-muted">保存成功仅表示本机事务完成；没有调用后端或传给其他设备。</p>
      </Card>
    </main>
  );
}
