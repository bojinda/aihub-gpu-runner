// CPU-only IndexedDB implementation; production uses the browser's native engine.
import {IDBFactory} from 'fake-indexeddb';
export function installBrowser() {
  const indexedDB = new IDBFactory();
  Object.defineProperty(globalThis, 'indexedDB', {value: indexedDB, configurable: true});
  return {indexedDB};
}
