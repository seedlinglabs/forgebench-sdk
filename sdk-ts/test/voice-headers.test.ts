import assert from "node:assert/strict";
import { test } from "node:test";
import { SESSION_HEADER, TURN_HEADER, voiceHeaders } from "../dist/index.js";

test("session only", () => {
  assert.deepEqual(voiceHeaders("s1"), { [SESSION_HEADER]: "s1" });
});

test("turn and traceparent", () => {
  const tp = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01";
  assert.deepEqual(voiceHeaders("s1", { turn: 3, traceparent: tp }), {
    [SESSION_HEADER]: "s1",
    [TURN_HEADER]: "3",
    traceparent: tp,
  });
});

test("rejects what the server would refuse", () => {
  assert.throws(() => voiceHeaders(""), RangeError);
  assert.throws(() => voiceHeaders("x".repeat(129)), RangeError);
  assert.throws(() => voiceHeaders("bad\nheader"), RangeError);
  assert.throws(() => voiceHeaders("s", { turn: -1 }), RangeError);
  assert.throws(() => voiceHeaders("s", { turn: 1.5 }), RangeError);
});

test("consecutive internal spaces allowed (same as the Python SDK)", () => {
  assert.equal(voiceHeaders("a  b")[SESSION_HEADER], "a  b");
});
