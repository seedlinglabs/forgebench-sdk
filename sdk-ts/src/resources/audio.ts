/**
 * Audio — Transcription and Speech Synthesis through the governed chokepoint.
 *
 *   audio.transcriptions.create -> POST /v1/audio/transcriptions  (multipart; audio seconds)
 *   audio.speech.create         -> POST /v1/audio/speech          (JSON; characters)
 *
 * Both are Governed Calls: authenticated, budget-gated before any provider
 * call (402 -> BudgetExceededError), metered in their native Usage Unit and
 * audited. Audio is never stored. `session` sets `X-Forgebench-Session`, the
 * Voice Conversation key (`metadata.session` also works and wins).
 */

import type { Transport } from "../http.js";
import type {
  AudioInput,
  SpeechAudio,
  SpeechRequest,
  Transcription,
  TranscriptionRequest,
} from "../types.js";

/** Voice Conversation key header, for calls that do not carry `metadata.session`. */
export const SESSION_HEADER = "X-Forgebench-Session";
/** Trace-id response header of the /v1/audio/* routes. */
export const AUDIO_TRACE_ID_HEADER = "X-Forgebench-Trace-Id";

export interface AudioCallOptions {
  signal?: AbortSignal;
  timeoutMs?: number;
}

function sessionHeaders(session?: string): Record<string, string> | undefined {
  return session ? { [SESSION_HEADER]: session } : undefined;
}

function toBlob(data: Blob | Uint8Array | ArrayBuffer, contentType?: string): Blob {
  if (data instanceof Blob) {
    return contentType && !data.type ? new Blob([data], { type: contentType }) : data;
  }
  return new Blob([data as BlobPart], contentType ? { type: contentType } : undefined);
}

function appendFile(form: FormData, file: AudioInput): void {
  if (file instanceof Blob) {
    form.append("file", file, (file as File).name || "audio");
  } else if (file instanceof Uint8Array || file instanceof ArrayBuffer) {
    form.append("file", toBlob(file), "audio");
  } else {
    const f = file as { data: Blob | Uint8Array | ArrayBuffer; filename?: string; contentType?: string };
    form.append("file", toBlob(f.data, f.contentType), f.filename ?? "audio");
  }
}

export class TranscriptionsResource {
  constructor(private readonly transport: Transport) {}

  /**
   * Transcription. `file` is a Blob/File, bytes, or `{data, filename,
   * contentType}`.
   *
   * @throws BudgetExceededError (402) before any provider call when over budget.
   * @throws PermissionDeniedError with `code === "model_not_allowed"` for a
   *   model outside the allowlist.
   */
  async create(body: TranscriptionRequest, opts: AudioCallOptions = {}): Promise<Transcription> {
    const form = new FormData();
    appendFile(form, body.file);
    form.append("model", body.model);
    if (body.language !== undefined) form.append("language", body.language);
    if (body.prompt !== undefined) form.append("prompt", body.prompt);
    if (body.response_format !== undefined) form.append("response_format", body.response_format);
    if (body.metadata) form.append("metadata", JSON.stringify(body.metadata));

    const res = await this.transport.requestRaw("/v1/audio/transcriptions", {
      method: "POST",
      form,
      headers: sessionHeaders(body.session),
      signal: opts.signal,
      timeoutMs: opts.timeoutMs,
    });
    const traceId = res.headers.get(AUDIO_TRACE_ID_HEADER);
    const text = await res.text();
    if ((res.headers.get("content-type") ?? "").includes("json")) {
      const raw = JSON.parse(text) as Record<string, unknown>;
      return { text: String(raw.text ?? ""), raw, traceId };
    }
    return { text, raw: {}, traceId };
  }
}

export class SpeechResource {
  constructor(private readonly transport: Transport) {}

  /**
   * Speech Synthesis. Resolves to the audio bytes plus the trace id. The
   * voice policy may refuse the call: UnprocessableEntityError
   * (`disclosure_missing`) or PermissionDeniedError (`voice_not_allowed`,
   * `marking_required`).
   */
  async create(body: SpeechRequest, opts: AudioCallOptions = {}): Promise<SpeechAudio> {
    const { session, ...payload } = body;
    const res = await this.transport.requestRaw("/v1/audio/speech", {
      method: "POST",
      body: payload,
      headers: sessionHeaders(session),
      signal: opts.signal,
      timeoutMs: opts.timeoutMs,
    });
    return {
      audio: await res.arrayBuffer(),
      contentType: res.headers.get("content-type"),
      traceId: res.headers.get(AUDIO_TRACE_ID_HEADER),
    };
  }
}

export class AudioResource {
  readonly transcriptions: TranscriptionsResource;
  readonly speech: SpeechResource;
  constructor(transport: Transport) {
    this.transcriptions = new TranscriptionsResource(transport);
    this.speech = new SpeechResource(transport);
  }
}
