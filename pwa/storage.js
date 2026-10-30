let databasePromise;
const scope = new URL('./', import.meta.url).pathname;
function database() {
  databasePromise ??= new Promise((resolve, reject) => {
    const request = indexedDB.open(`kifu-on-device:${scope}`, 1);
    request.onupgradeneeded = () => request.result.createObjectStore('records');
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(new Error('端末内の保存領域を開けません。プライベートブラウズや空き容量を確認してください。'));
  });
  return databasePromise;
}
export async function stored(key, value) {
  const db = await database();
  return new Promise((resolve, reject) => {
    const transaction = db.transaction('records', value === undefined ? 'readonly' : 'readwrite');
    const store = transaction.objectStore('records');
    const request = value === undefined ? store.get(key) : store.put(value, key);
    transaction.oncomplete = () => resolve(request.result);
    transaction.onerror = () => reject(new Error('端末への保存に失敗しました。空き容量を確認し、結果をJSONまたはKIFで保存してください。'));
    transaction.onabort = transaction.onerror;
  });
}
export async function fileId(file) {
  // 元動画を複製せず、小さな断片と属性から再選択した動画を識別する。
  const middle = Math.max(0, Math.floor(file.size / 2) - 32768);
  const pieces = await Promise.all([file.slice(0, 65536).arrayBuffer(), file.slice(middle, middle + 65536).arrayBuffer(), file.slice(Math.max(0, file.size - 65536)).arrayBuffer()]);
  // 写真選択時にFileの名前・更新日が変わっても同じ内容なら再開できる。
  const attributes = new TextEncoder().encode(String(file.size));
  const bytes = new Uint8Array(attributes.length + pieces.reduce((sum, part) => sum + part.byteLength, 0));
  bytes.set(attributes); let offset = attributes.length;
  for (const piece of pieces) { bytes.set(new Uint8Array(piece), offset); offset += piece.byteLength; }
  return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), value => value.toString(16).padStart(2, '0')).join('');
}
