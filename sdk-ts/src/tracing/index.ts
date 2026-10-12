/**
 * Tracing: record your agent's own steps as one nested trace.
 *
 * Mark the functions you want to see; model calls made inside them nest
 * under them automatically, with tokens and cost:
 *
 * @example
 * import { Forgebench, observe, updateActiveTrace } from "@seedlinglabs/forgebench-sdk";
 *
 * const forgebench = new Forgebench({ apiKey: process.env.FORGEBENCH_API_KEY! });
 *
 * const getWeather = observe(async (city: string) => ({ city, temp: 18 }), { asType: "tool" });
 *
 * const run = observe(async (question: string) => {
 *   updateActiveTrace({ userId: "u_42", sessionId: "chat_7" });
 *   const weather = await getWeather("sf");
 *   return forgebench.chat.create({ messages: [{ role: "user", content: question }] }); // nested
 * }, { name: "support-agent", asType: "agent" });
 *
 * Steps are sent in the background with the client's API key — no other keys,
 * no extra setup — and only ever WRITTEN: the SDK never reads traces back.
 * Module-level functions record with the most recently created client.
 */

import type { Observation, ObservationUpdate, TraceUpdate } from "./observation.js";
import {
  activeObservation,
  resolveTracer,
  wrapObserved,
  type ObserveOptions,
  type StartOptions,
} from "./tracer.js";

/**
 * Wrap `fn` (sync or async) so every call is recorded as an observation: its
 * arguments as input (a single argument as itself), its result as output, its
 * duration, and an error as `level: "ERROR"` (re-thrown unchanged). `name`
 * defaults to the function's name and can be anything that helps you debug.
 */
export function observe<F extends (...args: never[]) => unknown>(fn: F, options: ObserveOptions = {}): F {
  return wrapObserved(fn, options, resolveTracer);
}

/** Run `fn` with a new active observation; steps and model calls inside nest under it. */
export function startActiveObservation<R>(
  name: string,
  fn: (observation: Observation) => R,
  options: StartOptions = {},
): R {
  return resolveTracer().startActiveObservation(name, fn, options);
}

/** A step that does NOT become active, for work that starts and ends in different places. Call `end()`. */
export function startObservation(name: string, options: StartOptions = {}): Observation {
  return resolveTracer().startObservation(name, options);
}

/** Change the active step. */
export function updateActiveObservation(update: ObservationUpdate): void {
  resolveTracer().updateActiveObservation(update);
}

/** Set trace-level fields: `userId`, `sessionId`, `tags`, `metadata`, `name`. */
export function updateActiveTrace(update: TraceUpdate): void {
  resolveTracer().updateActiveTrace(update);
}

export function getActiveTraceId(): string | undefined {
  return activeObservation()?.traceId;
}

export function getActiveObservationId(): string | undefined {
  return activeObservation()?.id;
}

export { createTraceId } from "./tracer.js";
export { Observation } from "./observation.js";
export type { ObservationType, ObservationLevel, ObservationUpdate, TraceUpdate } from "./observation.js";
export type { ObserveOptions, StartOptions } from "./tracer.js";
export type { TracingOptions } from "./config.js";
