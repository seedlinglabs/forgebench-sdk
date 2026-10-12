/**
 * Which observation the running code is inside of.
 *
 * Uses `AsyncLocalStorage` where the runtime has it (Node ≥20.16 / 22, Bun),
 * loaded through `process.getBuiltinModule` so the SDK keeps no static
 * `node:` import and still bundles for browsers and edge runtimes. Elsewhere
 * a plain variable is used: nesting is right for sequential code, but steps
 * running CONCURRENTLY may be attached to the wrong parent.
 */

interface ContextStore<T> {
  getStore(): T | undefined;
  run<R>(store: T, fn: () => R): R;
}

type AsyncLocalStorageClass = new <T>() => ContextStore<T>;

function loadAsyncLocalStorage(): AsyncLocalStorageClass | undefined {
  const proc = (globalThis as { process?: { getBuiltinModule?: (id: string) => unknown } }).process;
  try {
    const mod = proc?.getBuiltinModule?.("node:async_hooks") as
      | { AsyncLocalStorage?: AsyncLocalStorageClass }
      | undefined;
    return mod?.AsyncLocalStorage;
  } catch {
    return undefined;
  }
}

class VariableStore<T> implements ContextStore<T> {
  private current: T | undefined;

  getStore(): T | undefined {
    return this.current;
  }

  run<R>(store: T, fn: () => R): R {
    const previous = this.current;
    this.current = store;
    let result: R;
    try {
      result = fn();
    } catch (err) {
      this.current = previous;
      throw err;
    }
    if (result instanceof Promise) {
      return result.finally(() => {
        this.current = previous;
      }) as R;
    }
    this.current = previous;
    return result;
  }
}

export function createContextStore<T>(): ContextStore<T> {
  const AsyncLocalStorage = loadAsyncLocalStorage();
  return AsyncLocalStorage ? new AsyncLocalStorage<T>() : new VariableStore<T>();
}
