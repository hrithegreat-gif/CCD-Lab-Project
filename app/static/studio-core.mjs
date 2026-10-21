/* Pure studio contract helpers. No browser storage, analytics or provider SDKs. */
export const MODELS = Object.freeze([
  { id: 'sentiment', name: 'DistilBERT', subtitle: 'SST-2 · quantized ONNX', task: 'sentiment', capability: null, provider: 'local', runtime: 'ONNX Runtime · CPU', description: 'Understand the tone of English text with a compact, local sentiment model.' },
  { id: 'qwen3.5-0.8b', name: 'Qwen 3.5', subtitle: '0.8B · local generation', task: 'generate', capability: 'qwen-generation', provider: 'local', runtime: 'Local · CPU', description: 'Generate text with the optional local Qwen adapter.' },
  { id: 'groq-gpt-oss-120b', name: 'GPT-OSS', subtitle: '120B · Groq', task: 'generate', capability: 'groq-text', provider: 'groq', runtime: 'Groq · hosted provider', description: 'Generate text through the optional Groq adapter. Provider usage may be billable.' },
  { id: 'kitten-speech', name: 'Kitten Mini', subtitle: 'Text to speech · WAV', task: 'speech', capability: 'kitten-speech', provider: 'local', runtime: 'Local · CPU', description: 'Turn text into downloadable speech with the optional Kitten adapter.' },
  { id: 'groq-transcription', name: 'Whisper Turbo', subtitle: 'Audio to text · Groq', task: 'transcribe', capability: 'groq-transcription', provider: 'groq', runtime: 'Groq · hosted provider', description: 'Transcribe an audio file through the optional Groq adapter. Provider usage may be billable.' },
]);
export const DEFAULT_CONFIG = Object.freeze({
  auth_required: false, max_request_bytes: 65536, sentiment_max_input_chars: 2000,
  generation_max_input_chars: 2000, generation_max_output_tokens: 256,
  speech_max_input_chars: 1000, transcription_max_bytes: 10485760,
});
export const SAMPLES = Object.freeze({
  sentiment: [
    ['Positive review', 'I love this product. The thoughtful design and helpful support made the whole experience wonderful.'],
    ['Negative review', 'I am disappointed with this product. It is unreliable, frustrating, and not worth the money.'],
    ['Mixed feedback', 'The design is beautiful and the team was helpful, but the setup was slow and several features felt unfinished.'],
  ],
  generate: [
    ['Explain a concept', 'Explain model quantization in three clear sentences for someone new to machine learning.'],
    ['Write a launch note', 'Write a short, friendly launch announcement for a new AI model playground.'],
    ['Brainstorm ideas', 'Suggest three practical ways a small business could use text sentiment analysis.'],
  ],
  speech: [
    ['Welcome message', 'Welcome to Inference Studio. Your models, one playground.'],
    ['Product narration', 'Explore real model inference with a clear, simple workspace built for experimentation.'],
    ['Short announcement', 'Your model is ready. Let us make something great.'],
  ],
});
export const characterCount = (value) => Array.from(value).length;
export const jsonBytes = (value) => new TextEncoder().encode(JSON.stringify(value)).byteLength;
export const formatMilliseconds = (value) => Number.isFinite(value) && value >= 0
  ? `${value < 10 ? value.toFixed(2) : value.toFixed(1)} ms` : '—';
export const formatBytes = (value) => value >= 1048576 ? `${(value / 1048576).toFixed(1)} MB` : `${(value / 1024).toFixed(1)} KB`;

export function normalizeConfig(raw = {}) {
  const result = { ...DEFAULT_CONFIG, auth_required: raw.auth_required === true };
  for (const key of Object.keys(DEFAULT_CONFIG)) {
    if (key !== 'auth_required' && Number.isSafeInteger(raw[key]) && raw[key] > 0) result[key] = raw[key];
  }
  return result;
}

export function modelStates(metadata, capabilities, config = DEFAULT_CONFIG, baselineReady = false) {
  const baseline = Array.isArray(metadata) ? metadata.find((item) => item?.id === 'sentiment') : null;
  const caps = capabilities?.capabilities || {};
  return MODELS.map((model) => {
    const capability = caps[model.capability];
    const state = !model.capability ? (baseline?.ready === true && baselineReady ? 'ready' : 'unavailable')
      : ['disabled', 'loading', 'unavailable', 'ready'].includes(capability?.state) ? capability.state : 'unavailable';
    return { ...model, state, metadata: !model.capability ? baseline : capability,
      maxChars: model.task === 'sentiment' ? (Number.isSafeInteger(baseline?.max_input_chars) && baseline.max_input_chars > 0 ? Math.min(baseline.max_input_chars, config.sentiment_max_input_chars) : config.sentiment_max_input_chars)
        : model.task === 'speech' ? config.speech_max_input_chars : config.generation_max_input_chars,
      voices: Array.isArray(capability?.voices) ? capability.voices.filter((voice) => typeof voice === 'string') : [],
    };
  });
}

const AUDIO_TYPES = new Set(['audio/wav', 'audio/x-wav', 'audio/wave', 'audio/vnd.wave', 'audio/mpeg', 'audio/mp3', 'audio/mpga', 'audio/mp4', 'audio/m4a', 'audio/x-m4a', 'audio/ogg', 'application/ogg', 'audio/webm', 'audio/flac', 'audio/x-flac']);
const AUDIO_EXTENSIONS = { wav: 'audio/wav', mp3: 'audio/mpeg', m4a: 'audio/mp4', mp4: 'audio/mp4', ogg: 'audio/ogg', webm: 'audio/webm', flac: 'audio/flac' };
export function audioContentType(file) {
  if (!file) return null;
  const type = String(file.type || '').toLowerCase().split(';')[0];
  return AUDIO_TYPES.has(type) ? type : AUDIO_EXTENSIONS[String(file.name || '').split('.').pop().toLowerCase()] || null;
}

export class StudioError extends Error {
  constructor(message, status = 0) { super(message); this.name = 'StudioError'; this.status = status; }
}

export function buildRequest(model, input, options = {}, config = DEFAULT_CONFIG) {
  if (!model || model.state !== 'ready') throw new StudioError('This model is not ready. Choose a ready model or ask the operator to enable it.');
  if (model.provider === 'groq' && options.providerConsent !== true) throw new StudioError('Confirm hosted-provider usage before sending this request.');
  if (model.task === 'transcribe') {
    const file = options.file;
    if (!file || file.size <= 0) throw new StudioError('Choose a nonempty audio file to transcribe.');
    const contentType = audioContentType(file);
    if (!contentType) throw new StudioError('Use a WAV, MP3, M4A, OGG, WebM, or FLAC audio file.');
    const limit = Math.min(config.transcription_max_bytes, config.max_request_bytes);
    if (file.size > limit) throw new StudioError(`Audio exceeds the current ${formatBytes(limit)} request limit.`);
    return { path: '/api/transcriptions', body: file, contentType };
  }
  if (typeof input !== 'string' || !input.trim()) throw new StudioError('Enter some text before running the model.');
  if (characterCount(input) > model.maxChars) throw new StudioError(`Keep your input within ${model.maxChars.toLocaleString()} characters.`);
  let path, body;
  if (model.task === 'sentiment') {
    path = '/api/inference'; body = { model: 'sentiment', input };
  } else if (model.task === 'generate') {
    const maxTokens = Number(options.maxTokens);
    if (!Number.isInteger(maxTokens) || maxTokens < 1 || maxTokens > config.generation_max_output_tokens) {
      throw new StudioError(`Output tokens must be between 1 and ${config.generation_max_output_tokens}.`);
    }
    path = '/api/generate'; body = { model: model.id, input, max_output_tokens: maxTokens };
  } else if (model.task === 'speech') {
    path = '/api/speech'; body = { input };
    if (options.voice) {
      if (model.voices.length && !model.voices.includes(options.voice)) throw new StudioError('Choose a voice offered by this model.');
      body.voice = options.voice;
    }
  } else throw new StudioError('Unknown model task.');
  if (jsonBytes(body) > config.max_request_bytes) throw new StudioError('This input exceeds the server request-byte limit. Shorten it and try again.');
  return { path, body: JSON.stringify(body), contentType: 'application/json' };
}

export function statusMessage(status) {
  return ({
    401: 'An API key is required, or the current key was rejected. Update session access and try again.',
    403: 'This request is not allowed by the server.',
    404: 'This model or endpoint is not available on this server.',
    413: 'The request is too large for the server. Reduce the input or audio size.',
    415: 'The server does not support this audio format.',
    422: 'The server rejected the input or settings. Check the limits and try again.',
    429: 'The model is at capacity. Wait a moment, then run it again.',
    500: 'The model could not complete this request. Check server logs without sharing your input or key.',
    502: 'The hosted provider could not complete this request. Ask the operator to check provider access.',
    503: 'The model is not ready or the service is temporarily unavailable. Refresh its status.',
    504: 'The server deadline was exceeded. No result was fabricated; try a shorter input later.',
  })[status] || `The server returned HTTP ${status}. Please try again later.`;
}

const SAFE_PATHS = new Set(['/studio/config', '/api/models', '/api/capabilities', '/api/inference', '/api/generate', '/api/speech', '/api/transcriptions', '/health/live', '/health/ready']);
export async function apiRequest(path, { key = '', method = 'GET', body, contentType, signal, timeout = 8000, fetcher = globalThis.fetch } = {}) {
  if (!SAFE_PATHS.has(path) || !['GET', 'POST'].includes(method)) throw new StudioError('The studio only calls its own allowlisted API endpoints.');
  const controller = new AbortController();
  let timedOut = false;
  const abort = () => controller.abort();
  if (signal?.aborted) controller.abort();
  else signal?.addEventListener('abort', abort, { once: true });
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, timeout);
  try {
    const headers = {};
    if (key && path.startsWith('/api/')) headers.Authorization = `Bearer ${key}`;
    if (contentType) headers['Content-Type'] = contentType;
    const response = await fetcher(path, { method, headers, body, signal: controller.signal, credentials: 'same-origin', cache: 'no-store', redirect: 'error' });
    if (!response.ok) throw new StudioError(statusMessage(response.status), response.status);
    if (path === '/api/speech') {
      if (response.headers.get('content-type')?.split(';')[0] !== 'audio/wav') throw new StudioError('The server did not return the expected WAV audio.');
      const blob = await response.blob();
      if (!blob.size) throw new StudioError('The server returned empty audio.');
      const timing = response.headers.get('x-processing-time-ms');
      const rate = response.headers.get('x-sample-rate');
      return { blob, processing_time_ms: timing !== null && Number.isFinite(Number(timing)) && Number(timing) >= 0 ? Number(timing) : null,
        sample_rate: rate !== null && Number.isFinite(Number(rate)) && Number(rate) > 0 ? Number(rate) : null, audio_bytes: blob.size };
    }
    return await response.json();
  } catch (error) {
    if (error instanceof StudioError) throw error;
    if (controller.signal.aborted) {
      if (timedOut) throw new StudioError('The browser wait timed out. Server work may continue; no automatic retry was sent.');
      throw new StudioError('Stopped waiting for this request. Server work may continue.', -1);
    }
    throw new StudioError('Cannot reach the API or read its response. Check the server connection and refresh status.');
  } finally {
    clearTimeout(timer); signal?.removeEventListener('abort', abort);
  }
}

const finiteNonnegative = (value) => Number.isFinite(value) && value >= 0;
export function validateResult(model, data) {
  if (model.task === 'speech') {
    if (!(data.blob instanceof Blob) || data.blob.size <= 0) throw new StudioError('The server returned an invalid audio result.');
    return data;
  }
  if (data?.status !== 'success' || !finiteNonnegative(data.processing_time_ms)) throw new StudioError('The server returned an invalid model result.');
  if (model.task === 'sentiment') {
    if (data.model !== 'sentiment' || !['positive', 'negative'].includes(data.prediction) || !finiteNonnegative(data.confidence)
        || data.confidence > 1 || !Number.isInteger(data.tokens_processed) || data.tokens_processed < 1 || typeof data.truncated !== 'boolean') {
      throw new StudioError('The server returned an invalid sentiment result.');
    }
  } else if (model.task === 'generate') {
    if (data.model !== model.id || data.provider !== model.provider || typeof data.output !== 'string'
        || !Number.isInteger(data.input_tokens) || data.input_tokens < 0 || !Number.isInteger(data.output_tokens) || data.output_tokens < 0
        || typeof data.truncated_input !== 'boolean') throw new StudioError('The server returned an invalid generation result.');
  } else if (model.task === 'transcribe') {
    if (data.model !== 'whisper-large-v3-turbo' || typeof data.text !== 'string'
        || (data.duration_seconds !== null && data.duration_seconds !== undefined && !finiteNonnegative(data.duration_seconds))) {
      throw new StudioError('The server returned an invalid transcription result.');
    }
  } else throw new StudioError('The server returned an unknown model task.');
  return data;
}

export function serializableResult(data) {
  const { blob, ...rest } = data;
  return blob ? { status: 'success', format: 'audio/wav', ...rest } : rest;
}
