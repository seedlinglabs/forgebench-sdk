/**
 * Turn whatever a traced function took or returned into JSON the server
 * accepts. Never throws: anything that cannot be represented becomes a short
 * description (cycles, functions, symbols, non-finite numbers, bigints...).
 */

/** Same cap the server applies to each input / output / metadata field. */
export const MAX_FIELD_CHARS = 64_000;
export const TRUNCATED = "…[truncated]";
const MAX_DEPTH = 20;
// Values visited per conversion: a huge or deeply shared structure is cut
// here instead of being walked whole before the size cap applies.
const MAX_NODES = 10_000;
const MAX_DESCRIPTION_CHARS = 1_000;

interface Walk {
  seen: WeakSet<object>;
  nodes: number;
}

export function toJsonable(value: unknown): unknown {
  return convert(value, 0, { seen: new WeakSet<object>(), nodes: 0 });
}

/**
 * `value` when its JSON form fits {@link MAX_FIELD_CHARS}, else that JSON cut
 * to size with a {@link TRUNCATED} marker — so the server keeps it.
 */
export function capped(value: unknown): unknown {
  if (value === undefined || value === null) return null;
  if (typeof value === "string") {
    return value.length <= MAX_FIELD_CHARS ? value : value.slice(0, MAX_FIELD_CHARS) + TRUNCATED;
  }
  const text = JSON.stringify(value);
  if (text.length <= MAX_FIELD_CHARS) return value;
  return text.slice(0, MAX_FIELD_CHARS) + TRUNCATED;
}

function convert(value: unknown, depth: number, walk: Walk): unknown {
  if (++walk.nodes > MAX_NODES) return "<truncated>";
  if (value === null || value === undefined) return null;
  switch (typeof value) {
    case "string":
      // Longer than the field cap gets cut there anyway; don't copy it whole.
      return value.length > MAX_FIELD_CHARS ? value.slice(0, MAX_FIELD_CHARS + 1) : value;
    case "boolean":
      return value;
    case "number":
      return Number.isFinite(value) ? value : String(value);
    case "bigint":
      return value.toString();
    case "function":
      return `[function ${value.name || "anonymous"}]`;
    case "symbol":
      return value.toString();
  }
  if (depth >= MAX_DEPTH) return "<max depth>";
  const obj = value as object;
  if (walk.seen.has(obj)) return "<circular>";
  walk.seen.add(obj);
  try {
    if (value instanceof Date) return Number.isNaN(value.getTime()) ? "Invalid Date" : value.toISOString();
    if (value instanceof Error) return { name: value.name, message: value.message };
    if (ArrayBuffer.isView(value) || value instanceof ArrayBuffer) {
      return `<${(value as ArrayBuffer).byteLength} bytes>`;
    }
    if (Array.isArray(value) || value instanceof Set) {
      const items: unknown[] = [];
      for (const v of value as Iterable<unknown>) {
        if (walk.nodes >= MAX_NODES) {
          items.push("<truncated>");
          break;
        }
        items.push(convert(v, depth + 1, walk));
      }
      return items;
    }
    const toJSON = (value as { toJSON?: unknown }).toJSON;
    if (typeof toJSON === "function") return convert(toJSON.call(value), depth + 1, walk);
    const entries: Iterable<[unknown, unknown]> = value instanceof Map ? value : ownEntries(value);
    const out: Record<string, unknown> = {};
    for (const [k, v] of entries) {
      if (walk.nodes >= MAX_NODES) {
        out["<truncated>"] = true;
        break;
      }
      out[String(k)] = convert(v, depth + 1, walk);
    }
    return out;
  } catch {
    return describe(value);
  } finally {
    walk.seen.delete(obj);
  }
}

/** An object's own enumerable entries, lazily — never copied whole up front. */
function* ownEntries(obj: object): Generator<[string, unknown]> {
  for (const key in obj) {
    if (Object.prototype.hasOwnProperty.call(obj, key)) yield [key, (obj as Record<string, unknown>)[key]];
  }
}

function describe(value: unknown): string {
  try {
    return String(value).slice(0, MAX_DESCRIPTION_CHARS);
  } catch {
    return "<unrepresentable>";
  }
}
